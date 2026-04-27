"""PerformanceAggregator — derives stats from TradeJournal JSONL events.

Reads all `exit` events in the configured window and computes:
  - n_trades, wins, losses, win_rate
  - avg_win_pct, avg_loss_pct
  - profit_factor (gross_wins / |gross_losses|)
  - expectancy_pct (per-trade expected return)
  - kelly_fraction
  - max_drawdown_pct (peak-to-trough on equity curve)
  - current_streak (consecutive win/loss count, signed)
  - sharpe_estimate (simple mean/std×sqrt(N))

Plus per-tag (scout/confirmed), per-pair, and per-exit_reason breakdowns
so ops can answer "is the scout phase profitable on its own?" or "are we
losing on a particular pair?".

Pure data layer: no Freqtrade dep, no IO beyond reading the journal.
Tested in isolation with seeded events.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from strategies.journal import TradeJournal


@dataclass(slots=True)
class GroupStats:
    """Stats for a sub-group (per-tag / per-pair / per-exit_reason)."""
    n: int = 0
    wins: int = 0
    losses: int = 0
    sum_pnl_pct: float = 0.0
    sum_pnl_usd: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n > 0 else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.sum_pnl_pct / self.n if self.n > 0 else 0.0


@dataclass(slots=True)
class PerformanceSnapshot:
    """Aggregate stats over a time window."""
    window_from: str | None = None     # ISO string or "all_time"
    window_to: str | None = None
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0           # average winning trade's pct return
    avg_loss_pct: float = 0.0          # average losing trade's |pct return|
    sum_pnl_usd: float = 0.0
    profit_factor: float = 0.0         # gross_wins / |gross_losses|
    expectancy_pct: float = 0.0        # per-trade expected return
    kelly_fraction: float = 0.0        # full Kelly (caller scales by safety)
    sharpe_estimate: float = 0.0       # rough: mean/std × sqrt(N_per_year)
    max_drawdown_pct: float = 0.0      # peak-to-trough on cumulative pct
    current_streak: int = 0            # +ve = consecutive wins, -ve = losses
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    avg_duration_hours: float = 0.0
    by_tag: dict[str, GroupStats] = field(default_factory=dict)
    by_pair: dict[str, GroupStats] = field(default_factory=dict)
    by_exit_reason: dict[str, GroupStats] = field(default_factory=dict)


class PerformanceAggregator:
    """Reads exit events, returns a PerformanceSnapshot.

    Window options:
      - all_time (default)
      - last_n_days(n)
      - last_n_trades(n)
    """

    def __init__(self, journal: TradeJournal):
        self._journal = journal

    def snapshot(
        self,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        last_n_trades: int | None = None,
        exclude_dates: set[str] | None = None,
    ) -> PerformanceSnapshot:
        """Compute a snapshot over the configured window. last_n_trades
        wins over date window when both supplied.

        exclude_dates: set of ISO YYYY-MM-DD strings; exit events whose
        timestamp date falls in this set are dropped before aggregation.
        Used to exclude journal pollution (e.g. backtest runs that wrote
        into prod journal before R115, or force_entry test events).
        """
        events = self._journal.read_range(from_date, to_date)
        # Keep only exit events (we measure *closed* trades)
        exits = [e for e in events if e.get("event_type") == "exit"]
        if exclude_dates:
            exits = [
                e for e in exits
                if str(e.get("timestamp", ""))[:10] not in exclude_dates
            ]
        if last_n_trades is not None and last_n_trades > 0:
            exits = exits[-last_n_trades:]
        return self._aggregate(exits, from_date, to_date)

    # ---------------------------------------------------------------- #
    # Internal aggregation
    # ---------------------------------------------------------------- #
    def _aggregate(
        self,
        exits: list[dict],
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> PerformanceSnapshot:
        snap = PerformanceSnapshot(
            window_from=from_date.isoformat() if from_date else "all_time",
            window_to=to_date.isoformat() if to_date else "now",
        )
        if not exits:
            return snap

        # Sort by timestamp so streak/DD math works
        exits.sort(key=lambda e: e.get("timestamp", ""))

        wins_pct: list[float] = []
        losses_pct: list[float] = []
        pnl_pcts: list[float] = []
        durations: list[float] = []
        streak = 0      # signed: +n consecutive wins, -n consecutive losses
        longest_win = 0
        longest_loss = 0

        # Drawdown tracking on cumulative pct (additive — close enough
        # without needing to model leverage compounding precisely)
        cum = 0.0
        peak = 0.0
        max_dd = 0.0

        for e in exits:
            pnl_pct = float(e.get("pnl_pct") or 0)
            pnl_usd = float(e.get("pnl_usd") or 0)
            duration = float(e.get("duration_hours") or 0)
            tag = e.get("entry_tag") or _entry_tag_fallback(e)
            pair = e.get("pair") or "unknown"
            reason = e.get("exit_reason") or "unknown"

            snap.n_trades += 1
            snap.sum_pnl_usd += pnl_usd
            pnl_pcts.append(pnl_pct)
            durations.append(duration)

            if pnl_pct > 0:
                snap.n_wins += 1
                wins_pct.append(pnl_pct)
                streak = streak + 1 if streak >= 0 else 1
                longest_win = max(longest_win, streak)
            elif pnl_pct < 0:
                snap.n_losses += 1
                losses_pct.append(abs(pnl_pct))
                streak = streak - 1 if streak <= 0 else -1
                longest_loss = max(longest_loss, -streak)
            # zero PnL doesn't break a streak (rare; treat as no-op)

            # DD on cumulative
            cum += pnl_pct
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)

            # Group stats
            for bucket, key in (
                (snap.by_tag, tag),
                (snap.by_pair, pair),
                (snap.by_exit_reason, reason),
            ):
                gs = bucket.setdefault(key, GroupStats())
                gs.n += 1
                gs.sum_pnl_pct += pnl_pct
                gs.sum_pnl_usd += pnl_usd
                if pnl_pct > 0:
                    gs.wins += 1
                elif pnl_pct < 0:
                    gs.losses += 1

        # Aggregates
        snap.win_rate = snap.n_wins / snap.n_trades if snap.n_trades > 0 else 0.0
        snap.avg_win_pct = sum(wins_pct) / len(wins_pct) if wins_pct else 0.0
        snap.avg_loss_pct = sum(losses_pct) / len(losses_pct) if losses_pct else 0.0
        snap.current_streak = streak
        snap.longest_win_streak = longest_win
        snap.longest_loss_streak = longest_loss
        snap.max_drawdown_pct = max_dd
        snap.avg_duration_hours = sum(durations) / len(durations) if durations else 0.0

        gross_wins = sum(wins_pct)
        gross_losses = sum(losses_pct)
        if gross_losses > 0:
            snap.profit_factor = gross_wins / gross_losses
        elif gross_wins > 0:
            snap.profit_factor = float("inf")
        else:
            snap.profit_factor = 0.0

        snap.expectancy_pct = (
            snap.win_rate * snap.avg_win_pct
            - (1 - snap.win_rate) * snap.avg_loss_pct
        )

        # Full Kelly: f* = (p×b - q) / b, where b = avg_win/avg_loss
        if snap.avg_loss_pct > 0:
            b = snap.avg_win_pct / snap.avg_loss_pct
            if b > 0:
                p = snap.win_rate
                q = 1 - p
                snap.kelly_fraction = max(0.0, (p * b - q) / b)
        elif snap.avg_win_pct > 0:
            snap.kelly_fraction = 1.0   # all wins, no losses

        # Sharpe estimate: mean/std × sqrt(per-year). Given we don't know
        # the inter-trade interval cleanly, use a rough multiplier of
        # sqrt(N_trades). Useful for relative comparison, not absolute claims.
        if len(pnl_pcts) > 1:
            mu = sum(pnl_pcts) / len(pnl_pcts)
            var = sum((x - mu) ** 2 for x in pnl_pcts) / (len(pnl_pcts) - 1)
            sigma = math.sqrt(var) if var > 0 else 0.0
            if sigma > 0:
                snap.sharpe_estimate = (mu / sigma) * math.sqrt(len(pnl_pcts))

        return snap


def _entry_tag_fallback(exit_event: dict) -> str:
    """Older exit events may not carry entry_tag. Returns 'unknown'."""
    return exit_event.get("entry_tag", "unknown")


# =================================================================== #
# Pretty formatter for human-readable text + Telegram-friendly Markdown
# =================================================================== #
def format_snapshot_md(snap: PerformanceSnapshot) -> str:
    """Markdown summary for Telegram + CLI report."""
    if snap.n_trades == 0:
        return "_No closed trades in window — nothing to report._"

    lines = []
    lines.append(f"📊 *Supertrend 績效快照*")
    if snap.window_from and snap.window_from != "all_time":
        lines.append(f"窗口: `{snap.window_from[:10]}` → `{snap.window_to[:10]}`")
    lines.append(f"")
    lines.append(f"🎯 *核心指標*")
    lines.append(f"   交易數: `{snap.n_trades}` (勝 `{snap.n_wins}` / 負 `{snap.n_losses}`)")
    lines.append(f"   勝率: `{snap.win_rate:.1%}`")
    lines.append(f"   平均贏: `+{snap.avg_win_pct:.2f}%` | 平均輸: `-{snap.avg_loss_pct:.2f}%`")
    lines.append(f"   獲利因子: `{_fmt_pf(snap.profit_factor)}`")
    lines.append(f"   每筆預期: `{snap.expectancy_pct:+.2f}%`")
    lines.append(f"   累計 PnL: `${snap.sum_pnl_usd:+.2f}`")
    lines.append(f"")
    lines.append(f"🛡️ *風險*")
    lines.append(f"   最大回撤: `-{snap.max_drawdown_pct:.2f}%` (累計 pct)")
    lines.append(f"   Kelly 建議: `{snap.kelly_fraction:.1%}` (full)")
    lines.append(f"   Sharpe (估): `{snap.sharpe_estimate:.2f}`")
    lines.append(f"")
    lines.append(f"📈 *連續*")
    lines.append(
        f"   當前: `{_streak_label(snap.current_streak)}` | "
        f"最長連勝: `{snap.longest_win_streak}` | 最長連負: `{snap.longest_loss_streak}`"
    )
    lines.append(f"   平均持倉: `{snap.avg_duration_hours:.1f}h`")

    if snap.by_tag:
        lines.append(f"")
        lines.append(f"🏷️ *分階段* (entry_tag)")
        for tag, gs in sorted(snap.by_tag.items()):
            lines.append(
                f"   `{tag:<10}` n=`{gs.n}` 勝率=`{gs.win_rate:.1%}` "
                f"平均=`{gs.avg_pnl_pct:+.2f}%`"
            )

    if snap.by_exit_reason:
        lines.append(f"")
        lines.append(f"🚪 *出場原因*")
        for reason, gs in sorted(snap.by_exit_reason.items(),
                                 key=lambda kv: kv[1].n, reverse=True):
            lines.append(
                f"   `{reason:<20}` n=`{gs.n}` 平均=`{gs.avg_pnl_pct:+.2f}%`"
            )

    if snap.by_pair and len(snap.by_pair) <= 8:
        lines.append(f"")
        lines.append(f"🪙 *分幣種*")
        for pair, gs in sorted(snap.by_pair.items(),
                               key=lambda kv: kv[1].sum_pnl_pct,
                               reverse=True):
            lines.append(
                f"   `{pair:<25}` n=`{gs.n}` 累積=`{gs.sum_pnl_pct:+.2f}%`"
            )

    return "\n".join(lines)


def _fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


def _streak_label(s: int) -> str:
    if s == 0:
        return "—"
    if s > 0:
        return f"連勝 {s}"
    return f"連負 {-s}"


__all__ = [
    "GroupStats",
    "PerformanceSnapshot",
    "PerformanceAggregator",
    "format_snapshot_md",
]
