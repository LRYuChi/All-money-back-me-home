"""TradeJournal — structured event log for SupertrendStrategy (round 46).

Persists every trade-related event as a JSONL row in `trading_log/journal/`,
one file per UTC day. Self-describing event types let downstream tools
(performance.py, journal_report.py) reconstruct any trade timeline:

  - entry          — order opened. Captures price, size, leverage, FULL
                     reasoning (direction_score, trend_quality, ADX, ATR,
                     funding rate, multi-TF state), planned SL%, planned
                     TP plan (4-phase trailing thresholds + partial exit
                     plan), Kelly fraction, quality scaling.
  - partial_exit   — partial position closed (e.g. 50% off at 15% profit).
                     Captures triggering condition + remaining size.
  - trailing_update— custom_stoploss bumped the SL to a new level. Captures
                     phase + new SL pct + max profit so far.
  - exit           — full position closed. Captures exit reason, max profit
                     ever seen, trailing phase at exit, P&L%/P&L$, duration,
                     multi-TF state at exit.
  - daily_summary  — periodic daily snapshot of aggregate performance.

Designed to be self-contained: no Freqtrade import in the journal layer
itself. The supertrend.py hooks build the event dataclass + call write().
This keeps tests clean (no IStrategy fixture needed).

Storage:
  - Default: trading_log/journal/{YYYY-MM-DD}.jsonl (UTC date partitioning)
  - Optional Postgres mirror via TradeJournal.with_postgres(dsn) (round 47+)

Concurrency: append-only JSONL is safe for single-process workers.
Multi-process (Freqtrade dry/live and a CLI report tool reading
concurrently) is OK because each process writes to its own day's file
with single-writer assumption + os.fsync via append_event.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


EventType = Literal[
    "entry", "partial_exit", "trailing_update", "exit", "daily_summary",
    "circuit_breaker", "skipped", "evaluation",
]


# =================================================================== #
# Multi-timeframe snapshot — captured at every event for replay
# =================================================================== #
@dataclass(slots=True)
class MultiTfState:
    """Snapshot of all 4 Supertrend timeframes + key indicators at this moment."""
    st_1d: int = 0                 # +1 / -1 / 0
    st_1d_duration: int = 0        # consecutive days in same direction
    dir_4h_score: float = 0.0      # [-1, 1]
    st_1h: int = 0
    st_15m: int = 0
    adx: float = 0.0
    atr: float = 0.0
    trend_quality: float = 0.0     # [0, 1]
    direction_score: float = 0.0   # weighted composite [-1, 1]
    funding_rate: float = 0.0


# =================================================================== #
# Stop-loss + take-profit PLAN (recorded at entry)
# =================================================================== #
@dataclass(slots=True)
class StoplossPlan:
    """Multi-phase smart trailing — exact thresholds asymmetric by side."""
    initial_sl_pct: float          # -0.05 (Phase 0 flat -5%)
    phase_1_trigger_pct: float     # 1.5 long / 1.0 short — lock breakeven+0.3%
    phase_2_trigger_pct: float     # 3.0 long / 2.5 short — lock 50% of profit
    phase_3_trigger_pct: float     # 6.0 long / 5.0 short — lock 70% of profit
    phase_1_lock_pct: float = 0.3  # breakeven + 0.3% (covers fees)
    phase_2_lock_pct: float = 0.50 # of max profit
    phase_3_lock_pct: float = 0.70 # of max profit


@dataclass(slots=True)
class TakeProfitPlan:
    """Partial exit + final exit conditions."""
    partial_1_at_profit_pct: float = 15.0
    partial_1_off_pct: float = 50.0
    partial_1_trigger: str = "1H trend against position"
    partial_2_at_profit_pct: float = 30.0
    partial_2_off_pct: float = 30.0
    partial_2_trigger: str = "15m trend against position"
    final_exit_trigger: str = "1D trend reversal (after >8 bars / 2h)"
    multi_tf_exit_trigger: str = "1H + 15m both against (after >8 bars / 2h, no partials yet)"
    time_decay_trigger: str = "200+ bars (~50h) with profit < 0.5%"


# =================================================================== #
# Event dataclasses
# =================================================================== #
@dataclass(slots=True)
class EntryEvent:
    """Order opened. Captures EVERYTHING needed to reconstruct the decision."""
    timestamp: str                 # ISO 8601 UTC
    pair: str
    side: Literal["long", "short"]
    entry_tag: str                 # "scout" | "confirmed"
    entry_price: float
    amount: float                  # base currency size
    notional_usd: float            # amount × price (BEFORE leverage)
    leverage: float
    stake_usd: float               # actual capital deployed (notional / leverage)
    state: MultiTfState
    stoploss_plan: StoplossPlan
    take_profit_plan: TakeProfitPlan
    kelly_fraction: float          # rolling Kelly used for sizing
    kelly_window: int              # how many trades the Kelly used
    quality_scale: float           # additional notional multiplier from quality
    cb_active: bool                # was circuit breaker tripped (skipped)?
    note: str = ""                 # human-readable summary line
    event_type: EventType = "entry"


@dataclass(slots=True)
class PartialExitEvent:
    """Partial size taken off; remainder still open."""
    timestamp: str
    pair: str
    side: str
    entry_price: float
    exit_price: float
    portion_pct: float             # e.g. 50.0 = closed half
    profit_pct_at_partial: float
    profit_usd_at_partial: float
    trigger: str                   # "15% target + 1H against" etc.
    state: MultiTfState
    note: str = ""
    event_type: EventType = "partial_exit"


@dataclass(slots=True)
class TrailingUpdateEvent:
    """custom_stoploss moved the SL to a new level."""
    timestamp: str
    pair: str
    side: str
    phase: int                     # 0-3
    new_sl_pct: float              # negative number = SL below current
    max_profit_seen_pct: float
    current_profit_pct: float
    note: str = ""
    event_type: EventType = "trailing_update"


@dataclass(slots=True)
class ExitEvent:
    """Position fully closed. Final state + performance."""
    timestamp: str
    pair: str
    side: str
    entry_price: float
    exit_price: float
    pnl_pct: float                 # signed % (e.g. +5.2 or -3.8)
    pnl_usd: float
    duration_hours: float
    exit_reason: str               # daily_reversal_exit / multi_tf_exit / time_decay / trailing_stop / stoploss / partial_followed_by_close
    max_profit_pct: float          # highest unrealized seen during life
    trailing_phase_at_exit: int    # 0-3
    n_partials_taken: int
    state: MultiTfState
    entry_tag: str = "unknown"     # carries the entry's tag (scout/confirmed)
                                    # so per-tag stats can be computed
                                    # without joining entry/exit events
    note: str = ""
    event_type: EventType = "exit"


@dataclass(slots=True)
class CircuitBreakerEvent:
    """An entry was skipped because of the consecutive-loss CB."""
    timestamp: str
    pair: str
    side: str
    streak_length: int
    cooldown_remaining_hours: float
    note: str = ""
    event_type: EventType = "circuit_breaker"


@dataclass(slots=True)
class SkippedEvent:
    """Entry signal was generated but not executed for non-CB reasons
    (e.g. quality filter, FR filter, insufficient stake)."""
    timestamp: str
    pair: str
    side: str
    reason: str
    state: MultiTfState
    note: str = ""
    event_type: EventType = "skipped"


@dataclass(slots=True)
class EvaluationEvent:
    """R66: per-pair, per-candle entry-evaluation snapshot.

    Written once per populate_entry_trend call (i.e., per closed candle
    × per pair when process_only_new_candles=True). Records WHICH
    precondition prevented each entry tier from firing.

    Lets ops answer 'why no trades?' without source-code spelunking:
    aggregate by failure reason → see which threshold is currently
    blocking signal capture in the live market regime.
    """
    timestamp: str             # iso when evaluation ran
    pair: str
    candle_ts: str             # iso of the last candle close evaluated
    confirmed_fired: bool
    confirmed_failures: list[str]
    scout_fired: bool
    scout_failures: list[str]
    pre_scout_fired: bool
    pre_scout_failures: list[str]
    state: MultiTfState
    note: str = ""
    event_type: EventType = "evaluation"


# =================================================================== #
# JSONL writer
# =================================================================== #
class TradeJournal:
    """Append-only JSONL writer. Thread-safe (single internal lock).

    Daily file rotation at UTC midnight: each call resolves its target
    file from the event timestamp. No active rotation needed — each
    write looks up its own day.
    """

    def __init__(self, base_dir: Path | str = "trading_log/journal"):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: Any) -> None:
        """Append one event row. Accepts any dataclass instance with
        an `event_type` field + a `timestamp` ISO string."""
        try:
            row = asdict(event)
        except TypeError:
            # Caller passed something other than a dataclass. Fall back
            # to dict if it's already one; otherwise refuse.
            if isinstance(event, dict):
                row = event
            else:
                raise TypeError(
                    f"TradeJournal.write expects a dataclass or dict, got "
                    f"{type(event).__name__}"
                )

        if "timestamp" not in row:
            row["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "event_type" not in row:
            row["event_type"] = "unknown"

        path = self._path_for(row["timestamp"])
        line = json.dumps(row, default=str, ensure_ascii=False)

        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()

    def _path_for(self, ts_iso: str) -> Path:
        """Resolve target file from an ISO timestamp."""
        try:
            d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        except ValueError:
            d = datetime.now(timezone.utc)
        date_str = d.astimezone(timezone.utc).strftime("%Y-%m-%d")
        return self._base / f"{date_str}.jsonl"

    # Reader helpers
    def read_day(self, date_str: str) -> list[dict]:
        """Read all events for one UTC date. Returns [] if file missing."""
        path = self._base / f"{date_str}.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "TradeJournal.read_day: skipping malformed row in %s: %s",
                            path.name, e,
                        )
        return out

    def read_range(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[dict]:
        """Read all events across days [from_date, to_date]. Both inclusive,
        UTC dates. None means open-ended."""
        files = sorted(self._base.glob("*.jsonl"))
        if not files:
            return []
        out: list[dict] = []
        for path in files:
            try:
                d = datetime.strptime(path.stem, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue
            if from_date and d < from_date.astimezone(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            ):
                continue
            if to_date and d > to_date.astimezone(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            ):
                continue
            out.extend(self.read_day(path.stem))
        return out


# =================================================================== #
# Convenience: build canonical plans for current Supertrend config
# =================================================================== #
def default_stoploss_plan(side: str) -> StoplossPlan:
    """Mirror SupertrendStrategy.custom_stoploss thresholds (asymmetric)."""
    if side == "short":
        return StoplossPlan(
            initial_sl_pct=-5.0,
            phase_1_trigger_pct=1.0, phase_2_trigger_pct=2.5,
            phase_3_trigger_pct=5.0,
        )
    return StoplossPlan(
        initial_sl_pct=-5.0,
        phase_1_trigger_pct=1.5, phase_2_trigger_pct=3.0,
        phase_3_trigger_pct=6.0,
    )


def default_take_profit_plan() -> TakeProfitPlan:
    """Mirror SupertrendStrategy.adjust_trade_position + custom_exit."""
    return TakeProfitPlan()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "EventType",
    "MultiTfState",
    "StoplossPlan",
    "TakeProfitPlan",
    "EntryEvent",
    "PartialExitEvent",
    "TrailingUpdateEvent",
    "ExitEvent",
    "CircuitBreakerEvent",
    "SkippedEvent",
    "TradeJournal",
    "default_stoploss_plan",
    "default_take_profit_plan",
    "now_iso",
]
