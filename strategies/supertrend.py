"""Supertrend 4-Layer MTF — Profit Maximizer Edition.

1D → 4H → 1H → 15m multi-timeframe Supertrend.
Smart trailing stop: let winners run, lock profit at breakeven+fees.
Long/Short asymmetric exits (shorts lock faster).
Trend quality filter: only trade in confirmed, high-quality trends.

Architecture:
  1D Supertrend → Macro direction
  4H Direction Engine → 4-factor confidence
  1H Supertrend → Trend filter
  15m Supertrend → Entry trigger
  Trend Quality Score → Entry gate (quality > 0.5)

Smart Stoploss:
  Phase 0: Flat -5% (give room to breathe)
  Phase 1: Lock at entry + 0.3% (breakeven after fees)
  Phase 2: Trail at 50% of max profit
  Phase 3: Trail at 70% of max profit

Designed for USDT perpetual futures on OKX via Freqtrade.
"""

from __future__ import annotations

import logging
import os
import sys
import numpy as np
import pandas as pd
import talib.abstract as ta
from datetime import datetime
from pathlib import Path
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_open
from pandas import DataFrame

# Add project root for imports
_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

try:
    from market_monitor.telegram_zh import send_message as _tg_send
    _TG = True
except ImportError:
    _TG = False

# Round 46: structured trade journal (JSONL events) + performance aggregator
try:
    from strategies.journal import (
        CircuitBreakerEvent,
        EntryEvent,
        EvaluationEvent,
        ExitEvent,
        MultiTfState,
        PartialExitEvent,
        SkippedEvent,
        TradeJournal,
        TrailingUpdateEvent,
        default_stoploss_plan,
        default_take_profit_plan,
        now_iso,
    )
    _JOURNAL_DIR = os.environ.get(
        "SUPERTREND_JOURNAL_DIR", "trading_log/journal",
    )
    _journal: TradeJournal | None = TradeJournal(_JOURNAL_DIR)
except Exception as _e:   # pragma: no cover — defensive
    _journal = None

# Round 48: market regime filter (system-wide context — gates entries
# during prolonged chop / dead-vol periods). Escape hatch:
# SUPERTREND_REGIME_FILTER=0 returns NoOp detector (always TRENDING).
try:
    from strategies.market_regime import (
        MarketRegimeDetector,
        NoOpRegimeDetector,
        Regime,
        RegimeSnapshot,
        SizingAdjustment,
    )
    _REGIME_AVAILABLE = True
except Exception:
    _REGIME_AVAILABLE = False

# R57: pre-entry alpha filters (FR contra-signal + orderbook microstructure).
# Both default OFF — set SUPERTREND_FR_ALPHA=1 / SUPERTREND_ORDERBOOK_CONFIRM=1
# to enable. Modules are pure (no I/O at import) so this is cheap.
try:
    from strategies.funding_alpha import (
        FR_EXTREME,
        FR_MILD,
        fr_signal_strength,
    )
    _FR_AVAILABLE = True
except Exception:
    _FR_AVAILABLE = False

try:
    from strategies.orderbook_signals import (
        evaluate as _ob_evaluate,
        should_confirm_entry as _ob_should_confirm,
    )
    _OB_AVAILABLE = True
except Exception:
    _OB_AVAILABLE = False

# R58: correlation/rotation sizing — applied in custom_stake_amount.
# Default OFF (SUPERTREND_CORRELATION_FILTER=1 to enable). When on:
#   * mean off-diagonal ρ ≥ 0.85 → return 0 (block — concentrated risk)
#   * else multiply target stake by rotation_sizing_multiplier(phase, pair)
try:
    from strategies.correlation_state import (
        DominancePhase,
        build_snapshot as _corr_build_snapshot,
        rotation_sizing_multiplier as _corr_rotation_mult,
    )
    _CORR_AVAILABLE = True
except Exception:
    _CORR_AVAILABLE = False

logger = logging.getLogger(__name__)


def _safe_journal_write(event) -> None:
    """Wrapper: journal failures NEVER block trading."""
    if _journal is None:
        return
    try:
        _journal.write(event)
    except Exception as e:
        logger.warning("trade journal write failed: %s", e)


def _snapshot_state(dataframe_row) -> MultiTfState:
    """Pull the multi-TF + indicator snapshot from the latest analyzed row."""
    if dataframe_row is None:
        return MultiTfState()
    g = lambda k, d=0: dataframe_row.get(k, d) if hasattr(dataframe_row, "get") else d  # noqa
    try:
        return MultiTfState(
            st_1d=int(g("st_1d", 0) or 0),
            st_1d_duration=int(g("st_1d_duration", 0) or 0),
            dir_4h_score=float(g("dir_4h_score", 0.0) or 0.0),
            st_1h=int(g("st_1h", 0) or 0),
            st_15m=int(g("st_trend", 0) or 0),
            adx=float(g("adx", 0.0) or 0.0),
            atr=float(g("atr", 0.0) or 0.0),
            trend_quality=float(g("trend_quality", 0.0) or 0.0),
            direction_score=float(g("direction_score", 0.0) or 0.0),
            funding_rate=float(g("funding_rate", 0.0) or 0.0),
        )
    except Exception:
        return MultiTfState()


def _send_to_all_bots(text: str) -> None:
    """Send message to both notification bot AND AI bot."""
    # 1. Notification bot (TELEGRAM_TOKEN)
    if _TG:
        _tg_send(text)
    # 2. AI bot (TG_AI_BOT_TOKEN) — separate bot, same chat
    try:
        import json
        import urllib.request
        ai_token = os.environ.get("TG_AI_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if ai_token and chat_id:
            payload = json.dumps({"chat_id": int(chat_id), "text": text, "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{ai_token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.debug("AI bot send failed: %s", e)


def _calc_supertrend(df: DataFrame, period: int = 10, multiplier: float = 3.0) -> DataFrame:
    atr = ta.ATR(df, timeperiod=period)
    src = (df["high"].values + df["low"].values) / 2
    close = df["close"].values
    atr_vals = atr.values
    n = len(df)
    up, dn, trend = np.zeros(n), np.zeros(n), np.ones(n)

    for i in range(n):
        if np.isnan(atr_vals[i]):
            up[i] = dn[i] = src[i]; continue
        raw_up = src[i] - multiplier * atr_vals[i]
        raw_dn = src[i] + multiplier * atr_vals[i]
        if i > 0:
            up[i] = max(raw_up, up[i-1]) if close[i-1] > up[i-1] else raw_up
            dn[i] = min(raw_dn, dn[i-1]) if close[i-1] < dn[i-1] else raw_dn
        else:
            up[i], dn[i] = raw_up, raw_dn
        if i > 0:
            if trend[i-1] == -1 and close[i] > dn[i-1]: trend[i] = 1
            elif trend[i-1] == 1 and close[i] < up[i-1]: trend[i] = -1
            else: trend[i] = trend[i-1]

    df["st_up"], df["st_dn"], df["st_trend"] = up, dn, trend
    return df


def _calc_4h_direction(df4h: DataFrame) -> DataFrame:
    close = df4h["close"]
    ema50 = ta.EMA(df4h, timeperiod=50)
    ema200 = ta.EMA(df4h, timeperiod=200)
    bull = (close > ema50) & (ema50 > ema200)
    bear = (close < ema50) & (ema50 < ema200)
    structure = np.where(bull, 1.0, np.where(bear, -1.0, 0.0))

    roc_24 = close.pct_change(6)
    roc_72 = close.pct_change(18)
    both_pos = (roc_24 > 0) & (roc_72 > 0)
    both_neg = (roc_24 < 0) & (roc_72 < 0)
    stronger = np.where(np.abs(roc_24) > np.abs(roc_72), np.sign(roc_24), np.sign(roc_72))
    momentum = np.where(both_pos, 1.0, np.where(both_neg, -1.0, stronger * 0.5))

    df4h = _calc_supertrend(df4h, period=10, multiplier=3.0)
    st_dir = np.where(df4h["st_trend"] == 1, 1.0, -1.0)

    atr_4h = ta.ATR(df4h, timeperiod=14)
    atr_expanding = atr_4h > atr_4h.shift(6)
    price_rising = close > close.shift(6)
    vol_score = np.where(atr_expanding & price_rising, 1.0,
                np.where(atr_expanding & ~price_rising, -1.0, 0.0))

    raw = 0.30 * structure + 0.30 * momentum + 0.25 * st_dir + 0.15 * vol_score
    df4h["dir_4h_score"] = np.clip(pd.Series(raw).ewm(span=3, min_periods=1).mean().values, -1, 1)
    return df4h


class SupertrendStrategy(IStrategy):
    """Two-phase entry: scout (3-layer aligned) + confirm (15m flip)."""

    INTERFACE_VERSION = 3

    timeframe = "15m"
    startup_candle_count = 250

    # ----------------------------------------------------------------- #
    # Stoploss configuration (Round 47 fix)
    # ----------------------------------------------------------------- #
    # `stoploss = -0.05` is the INITIAL static SL (Phase 0). Always required
    # by Freqtrade as a safety floor.
    #
    # `use_custom_stoploss = True` (was False — bug): enables the 4-phase
    # trailing logic in `custom_stoploss()`. Without this, Freqtrade ignores
    # the method entirely and the round 46 trailing event tracking + the
    # whole "lock 50%/70% profit" design is dead code.
    #
    # `trailing_stop = False`: explicitly off so Freqtrade's built-in
    # trailing doesn't double up with our custom logic. Our custom_stoploss
    # IS the trailing.
    #
    # Interaction with config's `stoploss_on_exchange = true`:
    #   - At entry: -5% SL is sent to OKX as a stop order on the book.
    #   - On profit phase transitions: custom_stoploss returns a TIGHTER
    #     SL pct. Freqtrade then cancels the OKX SL and submits a new one
    #     at the new (closer-to-current-price) level.
    #   - Freqtrade only updates if the new SL is closer to current price
    #     than existing — so we never accidentally widen the SL (good).
    #   - Cost: 1 cancel + 1 new SL order per phase transition (max 3
    #     transitions per trade lifetime). Negligible API budget.
    #
    # Hardware safety: SL stays as a real order on OKX even if the
    # Freqtrade container dies — the position has hard protection at all
    # times.
    stoploss = -0.05
    trailing_stop = False
    use_custom_stoploss = True

    can_short = True
    trading_mode = "futures"
    margin_mode = "isolated"

    st_period = 10
    st_multiplier = 3.0
    adx_threshold = 25

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return ([(p, "1h") for p in pairs]
                + [(p, "4h") for p in pairs]
                + [(p, "1d") for p in pairs])

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]

        # === 15m ===
        dataframe = _calc_supertrend(dataframe, self.st_period, self.st_multiplier)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.st_period)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["volume_ma_20"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["atr_rising"] = dataframe["atr"] > dataframe["atr"].shift(4)

        dataframe["st_buy"] = (dataframe["st_trend"] == 1) & (dataframe["st_trend"].shift(1) == -1)
        dataframe["st_sell"] = (dataframe["st_trend"] == -1) & (dataframe["st_trend"].shift(1) == 1)

        # === Funding Rate filter (if available) ===
        if "funding_rate" in dataframe.columns:
            fr = dataframe["funding_rate"].fillna(0)
            # Extreme positive FR → avoid longs (crowd overleveraged long)
            dataframe["fr_ok_long"] = fr < 0.001   # < 0.1%/8h
            # Extreme negative FR → avoid shorts
            dataframe["fr_ok_short"] = fr > -0.001  # > -0.1%/8h
            # FR as quality bonus: extreme negative + buying = short squeeze potential
            dataframe["fr_bonus_long"] = (fr < -0.0005).astype(float) * 0.1
            dataframe["fr_bonus_short"] = (fr > 0.0005).astype(float) * 0.1
        else:
            dataframe["fr_ok_long"] = True
            dataframe["fr_ok_short"] = True
            dataframe["fr_bonus_long"] = 0.0
            dataframe["fr_bonus_short"] = 0.0

        # === 1H Supertrend ===
        htf1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")
        if len(htf1h) > 0:
            htf1h = _calc_supertrend(htf1h, self.st_period, self.st_multiplier)
            m = htf1h[["date", "st_trend"]].rename(columns={"st_trend": "st_1h"}).copy()
            m["date"] = pd.to_datetime(m["date"])
            dataframe["date"] = pd.to_datetime(dataframe["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["st_1h"] = 0

        # === 4H Direction Engine ===
        htf4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        if len(htf4h) > 0:
            htf4h = _calc_4h_direction(htf4h)
            m4 = htf4h[["date", "dir_4h_score"]].copy()
            m4["date"] = pd.to_datetime(m4["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m4.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["dir_4h_score"] = 0.0

        # === 1D Supertrend ===
        htf1d = self.dp.get_pair_dataframe(pair=pair, timeframe="1d")
        if len(htf1d) > 0:
            htf1d = _calc_supertrend(htf1d, self.st_period, self.st_multiplier)

            # 1D trend duration (how many consecutive days in same direction)
            st_1d_vals = htf1d["st_trend"].values
            duration = np.zeros(len(st_1d_vals))
            for i in range(1, len(st_1d_vals)):
                if st_1d_vals[i] == st_1d_vals[i-1]:
                    duration[i] = duration[i-1] + 1
                else:
                    duration[i] = 1
            htf1d["st_1d_duration"] = duration

            m1d = htf1d[["date", "st_trend", "st_1d_duration"]].rename(
                columns={"st_trend": "st_1d"}).copy()
            m1d["date"] = pd.to_datetime(m1d["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m1d.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["st_1d"] = 0
            dataframe["st_1d_duration"] = 0

        # === Trend Quality Score ===
        dir_1d = np.where(dataframe["st_1d"] == 1, 1.0, -1.0)
        dir_4h = dataframe["dir_4h_score"].fillna(0).values
        dir_1h = np.where(dataframe["st_1h"] == 1, 1.0, -1.0)

        adx_norm = (dataframe["adx"] / 50).clip(0, 1)
        duration_norm = (dataframe["st_1d_duration"] / 30).clip(0, 1)
        alignment = ((dir_1d * dir_1h) > 0).astype(float)  # Same direction = 1
        atr_expand = dataframe["atr_rising"].astype(float)

        dataframe["trend_quality"] = (
            0.25 * adx_norm + 0.25 * duration_norm
            + 0.25 * alignment + 0.25 * atr_expand
        )

        # Composite direction
        dataframe["direction_score"] = 0.40 * dir_1d + 0.35 * dir_4h + 0.25 * dir_1h
        dataframe["all_bullish"] = (dataframe["st_1d"] == 1) & (dir_4h > 0.2) & (dataframe["st_1h"] == 1)
        dataframe["all_bearish"] = (dataframe["st_1d"] == -1) & (dir_4h < -0.2) & (dataframe["st_1h"] == -1)

        # R49: pre-scout helper (1D + 4H aligned but 1H still ambiguous).
        # Catches the earliest possible directional bias before 1H confirms.
        # `pair_bullish_2tf` = 1D + 4H bullish (don't require 1H)
        dataframe["pair_bullish_2tf"] = (
            (dataframe["st_1d"] == 1) & (dir_4h > 0.2)
        )
        dataframe["pair_bearish_2tf"] = (
            (dataframe["st_1d"] == -1) & (dir_4h < -0.2)
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        quality = (
            (dataframe["adx"] > self.adx_threshold)
            & (dataframe["volume"] > dataframe["volume_ma_20"] * 1.2)
            & dataframe["atr_rising"]
            & (dataframe["trend_quality"] > 0.5)
        )

        # === Phase 2 (confirmed): 4-layer aligned + 15m flip ===
        mask_confirmed_long = dataframe["st_buy"] & dataframe["all_bullish"] & quality & dataframe["fr_ok_long"]
        dataframe.loc[mask_confirmed_long, "enter_long"] = 1
        dataframe.loc[mask_confirmed_long, "enter_tag"] = "confirmed"

        mask_confirmed_short = dataframe["st_sell"] & dataframe["all_bearish"] & quality & dataframe["fr_ok_short"]
        dataframe.loc[mask_confirmed_short, "enter_short"] = 1
        dataframe.loc[mask_confirmed_short, "enter_tag"] = "confirmed"

        # === Phase 1 (scout): 3-layer aligned + quality, 15m NOT yet flipped ===
        # P0-3 (2026-04-23): Edge-trigger only — scout fires on the candle the
        # 3-layer alignment first forms, NOT every candle while it remains.
        # Reverts behaviour from commit 4ae76e4. Backtest evidence (200d):
        #   continuous trigger → 673 trades, -7.62%
        #   edge trigger only  → 129 trades, +28.80%
        # The strategy's edge depends on letting confirmed/daily_reversal exits
        # develop; over-firing scout dilutes those into noise.
        bull_just_formed = dataframe["all_bullish"] & (~dataframe["all_bullish"].shift(1).fillna(False))
        bear_just_formed = dataframe["all_bearish"] & (~dataframe["all_bearish"].shift(1).fillna(False))

        three_bull = bull_just_formed & (dataframe["st_trend"] == -1)
        three_bear = bear_just_formed & (dataframe["st_trend"] == 1)

        mask_scout_long = three_bull & quality & dataframe["fr_ok_long"] & ~mask_confirmed_long
        dataframe.loc[mask_scout_long, "enter_long"] = 1
        dataframe.loc[mask_scout_long, "enter_tag"] = "scout"

        mask_scout_short = three_bear & quality & dataframe["fr_ok_short"] & ~mask_confirmed_short
        dataframe.loc[mask_scout_short, "enter_short"] = 1
        dataframe.loc[mask_scout_short, "enter_tag"] = "scout"

        # === Phase 0 (pre-scout, R49): 1D+4H aligned, 1H + 15m still pending
        # Edge-trigger only — fires on the bar 2-TF alignment first forms.
        # Smaller sizing (0.25 Kelly) to test the earliest directional thesis.
        # Opt-in via SUPERTREND_KELLY_MODE != "binary".
        if os.environ.get("SUPERTREND_KELLY_MODE", "three_stage") != "binary":
            two_tf_bull_just_formed = (
                dataframe["pair_bullish_2tf"]
                & (~dataframe["pair_bullish_2tf"].shift(1).fillna(False))
            )
            two_tf_bear_just_formed = (
                dataframe["pair_bearish_2tf"]
                & (~dataframe["pair_bearish_2tf"].shift(1).fillna(False))
            )
            # Pre-scout requires 1H NOT yet aligned (otherwise it'd be scout)
            mask_pre_scout_long = (
                two_tf_bull_just_formed
                & (dataframe["st_1h"] != 1)
                & quality & dataframe["fr_ok_long"]
                & ~mask_confirmed_long
                & ~mask_scout_long
            )
            dataframe.loc[mask_pre_scout_long, "enter_long"] = 1
            dataframe.loc[mask_pre_scout_long, "enter_tag"] = "pre_scout"

            mask_pre_scout_short = (
                two_tf_bear_just_formed
                & (dataframe["st_1h"] != -1)
                & quality & dataframe["fr_ok_short"]
                & ~mask_confirmed_short
                & ~mask_scout_short
            )
            dataframe.loc[mask_pre_scout_short, "enter_short"] = 1
            dataframe.loc[mask_pre_scout_short, "enter_tag"] = "pre_scout"

        # R66: write per-pair evaluation telemetry (default ON, opt-out
        # via SUPERTREND_EVAL_JOURNAL=0). Records WHICH precondition
        # prevented each entry tier from firing on the latest candle.
        # Lets ops aggregate "why no trades" by failure reason.
        if os.environ.get("SUPERTREND_EVAL_JOURNAL", "1") == "1" \
                and len(dataframe) > 0:
            try:
                self._write_evaluation_event(dataframe, metadata)
            except Exception as e:
                logger.debug("evaluation write failed for %s: %s",
                             metadata.get("pair", "?"), e)

        return dataframe

    def _write_evaluation_event(self, dataframe: DataFrame,
                                metadata: dict) -> None:
        """R66: snapshot the LAST candle's per-tier entry evaluation."""
        last = dataframe.iloc[-1]
        pair = metadata.get("pair", "?")

        # Compute per-tier failure reasons by re-checking the same
        # masks populate_entry_trend used. Keep reasons short + stable
        # so dashboard can group_by reason.
        adx = float(last.get("adx", 0) or 0)
        vol = float(last.get("volume", 0) or 0)
        vol_ma = float(last.get("volume_ma_20", 1) or 1)
        atr_rising = bool(last.get("atr_rising", False))
        quality_score = float(last.get("trend_quality", 0) or 0)
        st_buy = bool(last.get("st_buy", False))
        st_sell = bool(last.get("st_sell", False))
        all_bull = bool(last.get("all_bullish", False))
        all_bear = bool(last.get("all_bearish", False))
        fr_long = bool(last.get("fr_ok_long", True))
        fr_short = bool(last.get("fr_ok_short", True))
        st_15m = int(last.get("st_trend", 0) or 0)
        st_1h_val = int(last.get("st_1h", 0) or 0)
        pair_bull_2tf = bool(last.get("pair_bullish_2tf", False))
        pair_bear_2tf = bool(last.get("pair_bearish_2tf", False))

        # Edge-trigger checks need previous candle
        prev = dataframe.iloc[-2] if len(dataframe) >= 2 else None
        prev_all_bull = bool(prev["all_bullish"]) if prev is not None else False
        prev_all_bear = bool(prev["all_bearish"]) if prev is not None else False
        prev_pair_bull_2tf = bool(prev["pair_bullish_2tf"]) if prev is not None else False
        prev_pair_bear_2tf = bool(prev["pair_bearish_2tf"]) if prev is not None else False

        bull_just_formed = all_bull and not prev_all_bull
        bear_just_formed = all_bear and not prev_all_bear
        two_tf_bull_just = pair_bull_2tf and not prev_pair_bull_2tf
        two_tf_bear_just = pair_bear_2tf and not prev_pair_bear_2tf

        # Common quality gate
        quality_fails = []
        if adx <= self.adx_threshold:
            quality_fails.append(f"adx<={self.adx_threshold}")
        if vol <= vol_ma * 1.2:
            quality_fails.append("vol<=1.2*ma")
        if not atr_rising:
            quality_fails.append("atr_not_rising")
        if quality_score <= 0.5:
            quality_fails.append("quality<=0.5")

        # Confirmed: st_buy & all_bullish & quality & fr_ok_long  (or short variant)
        conf_long_fails = list(quality_fails)
        if not st_buy:
            conf_long_fails.append("st_buy=False")
        if not all_bull:
            conf_long_fails.append("all_bullish=False")
        if not fr_long:
            conf_long_fails.append("fr_blocks_long")

        conf_short_fails = list(quality_fails)
        if not st_sell:
            conf_short_fails.append("st_sell=False")
        if not all_bear:
            conf_short_fails.append("all_bearish=False")
        if not fr_short:
            conf_short_fails.append("fr_blocks_short")

        confirmed_fired = len(conf_long_fails) == 0 or len(conf_short_fails) == 0
        confirmed_failures = (
            conf_long_fails if len(conf_long_fails) <= len(conf_short_fails)
            else conf_short_fails
        )

        # Scout: bull_just_formed & st_trend==-1 & quality & fr_ok_long
        sc_long_fails = list(quality_fails)
        if not bull_just_formed:
            sc_long_fails.append("bull_just_formed=False")
        if st_15m != -1:
            sc_long_fails.append("st_trend!=-1")
        if not fr_long:
            sc_long_fails.append("fr_blocks_long")

        sc_short_fails = list(quality_fails)
        if not bear_just_formed:
            sc_short_fails.append("bear_just_formed=False")
        if st_15m != 1:
            sc_short_fails.append("st_trend!=1")
        if not fr_short:
            sc_short_fails.append("fr_blocks_short")

        scout_fired = len(sc_long_fails) == 0 or len(sc_short_fails) == 0
        scout_failures = (
            sc_long_fails if len(sc_long_fails) <= len(sc_short_fails)
            else sc_short_fails
        )

        # Pre-scout: 2tf_just_formed & st_1h != aligned & quality & fr
        ps_long_fails = list(quality_fails)
        if not two_tf_bull_just:
            ps_long_fails.append("pair_bullish_2tf_just_formed=False")
        if st_1h_val == 1:
            ps_long_fails.append("st_1h_already_aligned_long")
        if not fr_long:
            ps_long_fails.append("fr_blocks_long")

        ps_short_fails = list(quality_fails)
        if not two_tf_bear_just:
            ps_short_fails.append("pair_bearish_2tf_just_formed=False")
        if st_1h_val == -1:
            ps_short_fails.append("st_1h_already_aligned_short")
        if not fr_short:
            ps_short_fails.append("fr_blocks_short")

        pre_scout_fired = (
            len(ps_long_fails) == 0 or len(ps_short_fails) == 0
        )
        pre_scout_failures = (
            ps_long_fails if len(ps_long_fails) <= len(ps_short_fails)
            else ps_short_fails
        )

        candle_ts = ""
        try:
            ts_val = last.get("date", "")
            candle_ts = (
                ts_val.isoformat() if hasattr(ts_val, "isoformat")
                else str(ts_val)
            )
        except Exception:
            pass

        _safe_journal_write(EvaluationEvent(
            timestamp=now_iso(),
            pair=pair,
            candle_ts=candle_ts,
            confirmed_fired=confirmed_fired,
            confirmed_failures=confirmed_failures,
            scout_fired=scout_fired,
            scout_failures=scout_failures,
            pre_scout_fired=pre_scout_fired,
            pre_scout_failures=pre_scout_failures,
            state=_snapshot_state(last),
        ))

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Only exit via custom_exit + custom_stoploss
        # No populate_exit_trend signals (let profits run!)
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None
        last = dataframe.iloc[-1]
        bars = (current_time - trade.open_date_utc).total_seconds() / 900
        is_long = not trade.is_short

        # R50: weighted exit — replaces single-condition triggers when
        # SUPERTREND_EXIT_MODE != "legacy". Score 0-1 based on 4 weighted
        # factors. ≥ 0.75 = full close. 0.5-0.75 = handled by
        # adjust_trade_position (50% reduction). < 0.5 = hold.
        exit_mode = os.environ.get("SUPERTREND_EXIT_MODE", "weighted")

        if exit_mode == "weighted":
            score, breakdown = self._exit_signal_score(
                trade, dataframe, current_time, current_profit,
            )
            # Persist score on trade for adjust_trade_position to read
            try:
                trade.set_custom_data("exit_signal_score", score)
                trade.set_custom_data("exit_signal_breakdown", breakdown)
            except Exception:
                pass

            if score >= 0.75 and bars > 8:
                return f"weighted_exit_full[{score:.2f}]"

            # Below 0.75 → fall through to legacy emergency triggers below

        # === Legacy / emergency triggers ===
        # 1D trend reversal → force exit (strongest signal). Always
        # active as a safety net even in weighted mode (if a trade has
        # been running > 24h and 1D reverses, just close it).
        daily_against = (is_long and last.get("st_1d") == -1) or (not is_long and last.get("st_1d") == 1)
        if daily_against and bars > 8:
            return "daily_reversal_exit"

        # Legacy multi-tf exit (only when not in weighted mode)
        if exit_mode == "legacy":
            if trade.nr_of_successful_exits > 0:
                pass  # tail rides till 1D reversal
            else:
                st_against = (is_long and last["st_trend"] == -1) or (not is_long and last["st_trend"] == 1)
                hourly_against = (is_long and last.get("st_1h") == -1) or (not is_long and last.get("st_1h") == 1)
                if st_against and hourly_against and bars > 8:
                    return "multi_tf_exit"

        # P2-9 (round 47): tightened time_decay tiers.
        # Old: only fired at 200 bars (~50h) with 0<profit<0.5%.
        #      Most "stuck" trades sit between -1% and +1% — old check
        #      missed those because it required strictly positive profit.
        # New: three tiers, progressively more aggressive.
        #
        # Tier A — quality lost early:
        #   if bars > 50 (~12.5h) and trend_quality dropped below 0.30,
        #   → exit. The setup that justified entry has decayed.
        # Tier B — long sideways with no edge:
        #   if bars > 100 (~25h) and |profit| < 1%,
        #   → exit. Capital is locked up earning ~0.
        # Tier C — terminal stuck (existing, broadened):
        #   if bars > 200 (~50h) and -0.5% < profit < +0.5%,
        #   → exit. Was profit > 0; now also catches small losers.
        quality_now = float(last.get("trend_quality", 0.5))
        if bars > 50 and quality_now < 0.30:
            return "time_decay_quality_lost"
        if bars > 100 and abs(current_profit) < 0.01:
            return "time_decay_sideways"
        if bars > 200 and -0.005 < current_profit < 0.005:
            return "time_decay_terminal"

        return None

    # R50: Weighted exit signal scoring.
    # 4 factors, total weight = 1.0. Each factor returns 0..1 reflecting
    # how strongly THIS factor argues for exit. Aggregate × weight.
    #
    # Factors (long position; short flips signs):
    #   1. 1D reversal           weight 0.30  (strongest single signal)
    #   2. 4H dir_score reversal weight 0.25
    #   3. 15m N consecutive bars weight 0.25  (N=2 → 0.5; N=3+ → 1.0)
    #   4. ADX trending down     weight 0.20
    #
    # Total ≥ 0.75 → full close
    # 0.50-0.75 → reduce 50% (handled by adjust_trade_position)
    # < 0.50 → hold

    _EXIT_WEIGHT_1D = 0.30
    _EXIT_WEIGHT_4H = 0.25
    _EXIT_WEIGHT_15M = 0.25
    _EXIT_WEIGHT_ADX = 0.20

    def _exit_signal_score(self, trade: Trade, dataframe,
                           current_time: datetime,
                           current_profit: float) -> tuple[float, dict]:
        """Compute 4-factor weighted exit score (0-1) + breakdown for journal.

        Returns (total_score, {factor: score}). dataframe must be
        analyzed with all multi-TF columns present.
        """
        if len(dataframe) < 6:
            return 0.0, {"reason": "insufficient_data"}

        last = dataframe.iloc[-1]
        is_long = not trade.is_short
        breakdown: dict[str, float] = {}

        # Factor 1: 1D reversal
        st_1d_val = last.get("st_1d", 0)
        f1 = 1.0 if (
            (is_long and st_1d_val == -1)
            or (not is_long and st_1d_val == 1)
        ) else 0.0
        breakdown["1d_reversal"] = f1

        # Factor 2: 4H dir_score crossed zero against position
        dir_now = float(last.get("dir_4h_score", 0.0))
        # Look 3 bars back (~45min) — has it flipped sign?
        if len(dataframe) >= 4:
            dir_past = float(dataframe.iloc[-4].get("dir_4h_score", 0.0))
        else:
            dir_past = dir_now
        f2 = 0.0
        if is_long:
            if dir_past > 0 and dir_now < 0:
                f2 = 1.0
            elif dir_past > 0 and dir_now < 0.1:
                f2 = 0.5     # partial reversal
        else:
            if dir_past < 0 and dir_now > 0:
                f2 = 1.0
            elif dir_past < 0 and dir_now > -0.1:
                f2 = 0.5
        breakdown["4h_dir_reversal"] = f2

        # Factor 3: 15m consecutive bars against
        recent_15m = dataframe.iloc[-3:]["st_trend"].values
        target_against = -1 if is_long else 1
        consec = sum(1 for v in recent_15m if v == target_against)
        if consec == 0:
            f3 = 0.0
        elif consec == 1:
            f3 = 0.25
        elif consec == 2:
            f3 = 0.5
        else:
            f3 = 1.0
        breakdown["15m_consecutive_against"] = f3

        # Factor 4: ADX trending down (regime weakening)
        adx_now = float(last.get("adx", 25.0))
        if len(dataframe) >= 7:
            adx_past = float(dataframe.iloc[-7].get("adx", 25.0))
        else:
            adx_past = adx_now
        # ADX dropping > 5 points in 6 bars (~1.5h) signals fading trend
        adx_drop = adx_past - adx_now
        f4 = 0.0
        if adx_drop > 8:
            f4 = 1.0
        elif adx_drop > 5:
            f4 = 0.7
        elif adx_drop > 2:
            f4 = 0.3
        breakdown["adx_declining"] = f4

        score = (
            self._EXIT_WEIGHT_1D * f1
            + self._EXIT_WEIGHT_4H * f2
            + self._EXIT_WEIGHT_15M * f3
            + self._EXIT_WEIGHT_ADX * f4
        )
        return score, breakdown

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float:
        """Smart trailing stop — profit-phase based.

        Phase 0: Flat -5% (breathe)
        Phase 1: Lock at entry + 0.3% (breakeven after OKX fees)
        Phase 2: Trail at 50% of max profit
        Phase 3: Trail at 70% of max profit

        Shorts use tighter thresholds (1.0/2.5/5.0 vs 1.5/3.0/6.0).

        Round 46: persists max_profit + trailing_phase to trade custom data
        so confirm_trade_exit can record the journal exit event with full
        post-mortem context. Phase transitions emit a trailing_update
        journal event for live debugging.
        """
        profit_pct = current_profit * 100

        # Asymmetric thresholds: shorts lock faster
        if trade.is_short:
            p1, p2, p3 = 1.0, 2.5, 5.0
        else:
            p1, p2, p3 = 1.5, 3.0, 6.0

        # Round 46: track max profit ever seen + current phase via Freqtrade
        # custom data (key-value store per trade). Defensive: getter/setter
        # may be missing in older FT versions or on naked Trade objects.
        prior_max = 0.0
        prior_phase = 0
        try:
            prior_max = float(trade.get_custom_data("max_profit_pct", 0.0) or 0.0)
            prior_phase = int(trade.get_custom_data("trailing_phase", 0) or 0)
        except Exception:
            pass

        if profit_pct > prior_max:
            try:
                trade.set_custom_data("max_profit_pct", profit_pct)
            except Exception:
                pass

        # Determine current phase
        if profit_pct >= p3:
            phase = 3
            sl = stoploss_from_open(current_profit * 0.70, current_profit,
                                    is_short=trade.is_short)
        elif profit_pct >= p2:
            phase = 2
            sl = stoploss_from_open(current_profit * 0.50, current_profit,
                                    is_short=trade.is_short)
        elif profit_pct >= p1:
            phase = 1
            sl = stoploss_from_open(0.003, current_profit,
                                    is_short=trade.is_short)
        else:
            phase = 0
            sl = -0.05

        # Persist phase + emit journal event ONLY on phase transition
        if phase != prior_phase:
            try:
                trade.set_custom_data("trailing_phase", phase)
            except Exception:
                pass
            _safe_journal_write(TrailingUpdateEvent(
                timestamp=now_iso(),
                pair=pair,
                side="short" if trade.is_short else "long",
                phase=phase,
                new_sl_pct=float(sl) * 100,
                max_profit_seen_pct=max(prior_max, profit_pct),
                current_profit_pct=profit_pct,
                note=f"Phase {prior_phase} → {phase}",
            ))

        return sl

    # Two-phase: scout DCA + partial exits
    position_adjustment_enable = True
    max_entry_position_adjustment = 1  # Allow 1 add-on (scout → confirm)

    # Position sizing: Rolling Kelly (adapts to recent performance)
    _KELLY_FRAC = 0.75
    _KELLY_LOOKBACK = 60  # Rolling window
    _KELLY_DEFAULT_WR = 0.355
    _KELLY_DEFAULT_WL = 3.36

    def _calc_rolling_kelly(self) -> float:
        """Calculate Kelly% from recent trades (rolling 60). Falls back to static."""
        try:
            recent = Trade.get_trades_proxy(is_open=False)
            trades = list(recent)[-self._KELLY_LOOKBACK:]
            if len(trades) < 10:
                # Not enough data, use defaults
                wr = self._KELLY_DEFAULT_WR
                wl = self._KELLY_DEFAULT_WL
            else:
                wins = [t for t in trades if t.close_profit and t.close_profit > 0]
                losses = [t for t in trades if t.close_profit and t.close_profit <= 0]
                wr = len(wins) / len(trades)
                avg_win = sum(t.close_profit for t in wins) / len(wins) if wins else 0.01
                avg_loss = abs(sum(t.close_profit for t in losses) / len(losses)) if losses else 0.05
                wl = avg_win / avg_loss if avg_loss > 0 else 1.0
        except Exception:
            wr = self._KELLY_DEFAULT_WR
            wl = self._KELLY_DEFAULT_WL

        if wl <= 0:
            return 0.05
        full_kelly = max(0, wr - (1 - wr) / wl)
        return full_kelly * self._KELLY_FRAC

    # P0-4: account-level circuit breaker — pause new entries after 3 consecutive losses
    _CB_LOSS_STREAK = 3
    _CB_COOLDOWN_HOURS = 12

    # R48: Market regime detector (lazy-init on first use). Escape hatch:
    # SUPERTREND_REGIME_FILTER=0 → uses NoOp (always TRENDING). Pulls
    # BTC daily candles via dp.get_pair_dataframe so we don't need extra
    # HTTP plumbing; cached internally for 5 min.
    _regime_detector_cache: object | None = None

    def _get_regime_detector(self):
        """Lazy-construct + cache the regime detector. The factory pattern
        lets us swap in NoOp via env var without restart."""
        if self._regime_detector_cache is not None:
            return self._regime_detector_cache
        if not _REGIME_AVAILABLE:
            return None
        if os.environ.get("SUPERTREND_REGIME_FILTER", "1").strip() in ("0", "false", "False"):
            self._regime_detector_cache = NoOpRegimeDetector()
            logger.info(
                "regime filter: DISABLED via SUPERTREND_REGIME_FILTER=0 — "
                "NoOp detector returns TRENDING always",
            )
            return self._regime_detector_cache

        def _fetch_btc_daily():
            df = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="1d")
            return df if df is not None and len(df) > 0 else None

        self._regime_detector_cache = MarketRegimeDetector(
            _fetch_btc_daily, ttl_seconds=300.0,
        )
        return self._regime_detector_cache

    def _current_regime_snapshot(self):
        """Returns RegimeSnapshot or None if detector unavailable."""
        det = self._get_regime_detector()
        if det is None:
            return None
        try:
            return det.detect()
        except Exception as e:
            logger.warning("regime detect failed: %s", e)
            return None

    # ----- R57: pre-entry alpha filters --------------------------- #
    # Both default OFF. When enabled, run AFTER regime/concentration
    # checks but BEFORE journal write — so a blocked entry never
    # appears in the journal as if it executed.
    #
    # Threshold rationale:
    #   FR strength is in [-1, 1] from tanh. We block on opposing
    #   strength >= 0.5 (≈ |FR| 0.55*FR_EXTREME = ~0.055%/8h) which
    #   corresponds to "extreme" zone — common enough to filter, rare
    #   enough not to over-suppress.
    _FR_BLOCK_STRENGTH = 0.5   # |strength| above this opposing the trade aborts

    def _funding_filter_block(self, side: str,
                              funding_rate: float) -> str | None:
        """Returns reason string if FR opposes the intended side strongly
        enough to abort, else None. Caller logs/blocks accordingly."""
        if not _FR_AVAILABLE:
            return None
        if os.environ.get("SUPERTREND_FR_ALPHA", "0") != "1":
            return None
        try:
            strength = fr_signal_strength(funding_rate)
        except Exception:
            return None
        # strength > 0 favors LONG, < 0 favors SHORT
        opposes_long = (side == "long" and strength < 0)
        opposes_short = (side == "short" and strength > 0)
        if (opposes_long or opposes_short) and \
                abs(strength) >= self._FR_BLOCK_STRENGTH:
            return (
                f"FR contra-signal: fr={funding_rate:+.4%} "
                f"strength={strength:+.2f} opposing {side}"
            )
        return None

    def _orderbook_filter_block(self, pair: str,
                                side: str) -> str | None:
        """Returns reason string if order-book microstructure strongly
        opposes the intended side, else None. Defensive: any I/O failure
        returns None (no signal — proceed)."""
        if not _OB_AVAILABLE:
            return None
        if os.environ.get("SUPERTREND_ORDERBOOK_CONFIRM", "0") != "1":
            return None
        try:
            book = self.dp.orderbook(pair, maximum=10)
        except Exception as e:
            logger.debug("orderbook fetch failed for %s: %s", pair, e)
            return None
        try:
            # No recent_trades fetch — pass empty list, signal degrades
            # gracefully to imbalance-only (which is the higher-weighted
            # component anyway).
            sig = _ob_evaluate(book or {}, [])
            proceed, reason = _ob_should_confirm(sig, side)
            if not proceed:
                return reason
        except Exception as e:
            logger.debug("orderbook evaluate failed for %s: %s", pair, e)
        return None

    def _pre_entry_filter_block(self, pair: str, side: str,
                                state) -> str | None:
        """Run all enabled pre-entry alpha filters. Returns the FIRST
        blocking reason or None if all pass."""
        # 1. Funding contra-signal (cheap — uses cached state)
        reason = self._funding_filter_block(side, state.funding_rate)
        if reason:
            return reason
        # 2. Orderbook microstructure (REST call — slowest, runs last)
        reason = self._orderbook_filter_block(pair, side)
        if reason:
            return reason
        return None

    # ----- R58: correlation / rotation stake sizing --------------- #
    # Applied INSIDE custom_stake_amount AFTER regime/concentration/CB
    # checks. Default OFF (SUPERTREND_CORRELATION_FILTER=1 to enable).
    # Returns (multiplier, block_reason_or_none):
    #   * (0.0, "...") → callsite returns 0 stake + skipped event
    #   * (mult, None) → callsite multiplies target_pct by mult
    _CORR_LOOKBACK_DAYS = 30
    _CORR_CONCENTRATION_THRESHOLD = 0.85

    def _gather_closes_for_correlation(
        self, intended_pair: str,
    ) -> dict[str, list[float]]:
        """Pull daily closes for currently-open pairs + intended pair.
        Defensive: dp failures fall through with whatever was collected."""
        closes: dict[str, list[float]] = {}
        pairs_to_query: set[str] = {intended_pair}
        try:
            for t in Trade.get_trades_proxy(is_open=True):
                if getattr(t, "pair", None):
                    pairs_to_query.add(t.pair)
        except Exception:
            pass
        # Always include BTC/USDT:USDT — needed for dominance phase classifier
        pairs_to_query.add("BTC/USDT:USDT")
        for p in pairs_to_query:
            try:
                df = self.dp.get_pair_dataframe(p, "1d")
                if df is not None and len(df) >= self._CORR_LOOKBACK_DAYS + 1:
                    closes[p] = df["close"].astype(float).tolist()
            except Exception as e:
                logger.debug("correlation: %s 1d fetch failed: %s", p, e)
        return closes

    def _correlation_stake_multiplier(
        self, intended_pair: str,
    ) -> tuple[float, str | None]:
        """Compute the rotation/correlation sizing multiplier for a new
        entry on `intended_pair`. Returns (mult, block_reason_or_None).

        block_reason set → caller should size 0. None → caller multiplies.
        """
        if not _CORR_AVAILABLE:
            return (1.0, None)
        if os.environ.get("SUPERTREND_CORRELATION_FILTER", "0") != "1":
            return (1.0, None)
        try:
            closes = self._gather_closes_for_correlation(intended_pair)
            if len(closes) < 2:
                # Need ≥2 valid pairs for a matrix → fall through unchanged
                return (1.0, None)
            snap = _corr_build_snapshot(
                closes, lookback_days=self._CORR_LOOKBACK_DAYS,
            )
        except Exception as e:
            logger.debug("correlation snapshot failed: %s", e)
            return (1.0, None)

        # Block on concentration ONLY when intended pair would join an
        # already-correlated cluster (we have ≥2 open positions feeding the
        # matrix). Otherwise mean ρ on a 2-element matrix is just BTC vs
        # the intended pair — too noisy to act on.
        try:
            n_open = sum(
                1 for t in Trade.get_trades_proxy(is_open=True)
            )
        except Exception:
            n_open = 0
        if n_open >= 2 and snap.mean_correlation >= self._CORR_CONCENTRATION_THRESHOLD:
            return (0.0, (
                f"correlation concentration: mean ρ={snap.mean_correlation:.2f} "
                f"≥ {self._CORR_CONCENTRATION_THRESHOLD} (open={n_open})"
            ))

        try:
            mult = _corr_rotation_mult(snap.dominance_phase, intended_pair)
        except Exception:
            mult = 1.0
        return (mult, None)

    # P1-4 (round 47): Direction concentration cap.
    # max_open_trades=3 means we can hold up to 3 positions simultaneously.
    # Without this guard, all 3 could be the same side (3 longs in a bull
    # cluster), concentrating directional risk. Cap at 2 same-side trades
    # so the third slot is reserved for the OPPOSITE direction (or stays
    # empty if no opposite signal arrives).
    #
    # Example: max_open_trades=3, _MAX_SAME_SIDE=2
    #   Open: BTC long, ETH long → can NOT open AVAX long
    #   Open: BTC long, ETH long → CAN open AVAX short
    _MAX_SAME_SIDE = 2

    def _same_side_open_count(self, side: str) -> int:
        """Count currently-open trades on the requested side."""
        try:
            wants_short = (side == "short")
            return sum(
                1 for t in Trade.get_trades_proxy(is_open=True)
                if bool(t.is_short) == wants_short
            )
        except Exception:
            return 0

    def _direction_concentration_blocked(self, side: str) -> bool:
        return self._same_side_open_count(side) >= self._MAX_SAME_SIDE

    def _circuit_breaker_active(self, current_time: datetime) -> bool:
        """Return True if last N closed trades were all losses within cooldown window.

        Live observation 2026-04-23: 12 consecutive losses went undetected.
        This breaker forces a 12h pause after 3 losses in a row, giving the
        operator a chance to investigate or for market regime to shift.
        """
        try:
            recent = sorted(
                Trade.get_trades_proxy(is_open=False),
                key=lambda x: x.close_date or current_time,
                reverse=True,
            )[: self._CB_LOSS_STREAK]
        except Exception:
            return False
        if len(recent) < self._CB_LOSS_STREAK:
            return False
        if not all(t.close_profit is not None and t.close_profit < 0 for t in recent):
            return False
        last_close = max((t.close_date for t in recent if t.close_date), default=None)
        if last_close is None:
            return False
        # Freqtrade trade close_date is naive UTC; current_time may be tz-aware
        if last_close.tzinfo is None and current_time.tzinfo is not None:
            from datetime import timezone as _tz
            last_close = last_close.replace(tzinfo=_tz.utc)
        elapsed = (current_time - last_close).total_seconds()

        # R48: regime-aware cooldown.
        # TRENDING:           6h  (recover fast — losses likely noise)
        # VOLATILE_TRENDING: 12h  (default)
        # CHOPPY:            48h  (don't restart into chop)
        # DEAD:              72h  (no point — entries blocked anyway)
        cooldown_hours = self._CB_COOLDOWN_HOURS
        try:
            regime_snap = self._current_regime_snapshot()
            if regime_snap is not None and _REGIME_AVAILABLE:
                adj = SizingAdjustment.for_regime(regime_snap.regime)
                cooldown_hours = adj.cooldown_hours
        except Exception:
            pass
        return elapsed < cooldown_hours * 3600

    def custom_stake_amount(self, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str, **kwargs) -> float:
        """Rolling Kelly × trend quality. Scout = 25%, Confirmed = 75%.

        P0-4: bail out early if account-level circuit breaker is tripped.
        P1-4 (round 47): bail out if same-side concentration cap hit.
        R48: market regime check — DEAD blocks, CHOPPY shrinks heavily.
        Returning 0 causes Freqtrade to skip this entry attempt.
        """
        # R48: market regime check (system-wide context)
        regime_snap = self._current_regime_snapshot()
        regime_adj = None
        if regime_snap is not None and _REGIME_AVAILABLE:
            regime_adj = SizingAdjustment.for_regime(regime_snap.regime)
            if regime_adj.block_new_entries:
                logger.warning(
                    "Regime DEAD — blocking entry on %s. %s",
                    kwargs.get("pair", ""), regime_snap.as_compact_str(),
                )
                try:
                    pair = kwargs.get("pair", "")
                    _safe_journal_write(SkippedEvent(
                        timestamp=now_iso(),
                        pair=pair, side=side or "unknown",
                        reason=(
                            f"regime: {regime_snap.regime.value} "
                            f"(ATR={regime_snap.atr_price_ratio:.2%}, "
                            f"ADX={regime_snap.adx_30d_median:.1f}, "
                            f"H={regime_snap.hurst_exponent:.2f})"
                        ),
                        state=MultiTfState(),
                        note="R48 regime gate — DEAD market",
                    ))
                    _send_to_all_bots(
                        f"💀 *Regime: DEAD*\n"
                        f"BTC {regime_snap.as_compact_str()}\n"
                        f"已跳過: `{pair}` ({side or '?'})\n"
                        f"市場無 edge，全停新進場"
                    )
                except Exception as e:
                    logger.warning("regime journal/alert failed: %s", e)
                return 0.0

        # P1-4: direction concentration cap
        if self._direction_concentration_blocked(side):
            logger.warning(
                "Direction concentration blocked — already %d open %s trades "
                "(cap %d). Skipping new %s entry on %s.",
                self._same_side_open_count(side), side,
                self._MAX_SAME_SIDE, side, kwargs.get("pair", ""),
            )
            try:
                pair = kwargs.get("pair", "")
                _safe_journal_write(SkippedEvent(
                    timestamp=now_iso(),
                    pair=pair, side=side or "unknown",
                    reason=f"direction_concentration: already {self._same_side_open_count(side)} open {side}, cap {self._MAX_SAME_SIDE}",
                    state=MultiTfState(),
                    note="P1-4 portfolio guard",
                ))
                _send_to_all_bots(
                    f"⚠️ *方向集中度 cap*\n"
                    f"已開 `{self._same_side_open_count(side)}` 個 `{side}` 倉 "
                    f"(上限 `{self._MAX_SAME_SIDE}`)\n"
                    f"跳過: `{pair}` ({side})\n"
                    f"理由: 防止單向過度集中風險"
                )
            except Exception as e:
                logger.warning("direction concentration journal/alert failed: %s", e)
            return 0.0

        if self._circuit_breaker_active(current_time):
            logger.warning(
                "Circuit breaker active — last %d closed trades all losses within %dh cooldown. "
                "Skipping new entry.", self._CB_LOSS_STREAK, self._CB_COOLDOWN_HOURS,
            )
            # Round 46: journal the skip + Telegram alert (loud)
            try:
                pair = kwargs.get("pair", "")
                _safe_journal_write(CircuitBreakerEvent(
                    timestamp=now_iso(),
                    pair=pair,
                    side=side or "unknown",
                    streak_length=self._CB_LOSS_STREAK,
                    cooldown_remaining_hours=float(self._CB_COOLDOWN_HOURS),
                    note=f"CB tripped — skipping entry on {pair}",
                ))
                _send_to_all_bots(
                    f"⛔ *斷路器啟動*\n"
                    f"連續 `{self._CB_LOSS_STREAK}` 次虧損，"
                    f"暫停 `{self._CB_COOLDOWN_HOURS}h` 進場\n"
                    f"已跳過: `{pair}` ({side or '?'})\n"
                    f"請檢查: 市場 regime / 訊號品質 / 倉位大小"
                )
            except Exception as e:
                logger.warning("CB journal/alert failed: %s", e)
            return 0.0

        target_pct = self._calc_rolling_kelly()
        target_pct = max(0.03, min(target_pct, 0.20))

        # R48: regime-aware sizing multiplier
        # TRENDING 1.0 / VOLATILE_TRENDING 0.7 / CHOPPY 0.3 / DEAD 0 (handled above)
        if regime_adj is not None:
            target_pct *= regime_adj.kelly_multiplier
            logger.debug(
                "regime sizing: regime=%s mult=%.2f → target_pct=%.4f",
                regime_snap.regime.value if regime_snap else "?",
                regime_adj.kelly_multiplier, target_pct,
            )

        # R58: correlation/rotation adjustment
        # Block when portfolio is already concentrated; else apply
        # rotation multiplier (BTC_STRONG → 0.7× alts; ALT_SEASON →
        # 1.2× alts / 0.7× BTC&ETH; CONSOLIDATION/UNKNOWN → 1.0×).
        intended_pair = kwargs.get("pair", "")
        corr_mult, corr_block = self._correlation_stake_multiplier(intended_pair)
        if corr_block:
            logger.warning(
                "correlation block on %s: %s", intended_pair, corr_block,
            )
            try:
                _safe_journal_write(SkippedEvent(
                    timestamp=now_iso(),
                    pair=intended_pair, side=side or "unknown",
                    reason=f"R58 {corr_block}",
                    state=MultiTfState(),
                    note="R58 correlation gate",
                ))
                _send_to_all_bots(
                    f"🔗 *Correlation 集中度攔截*\n"
                    f"`{intended_pair}` ({side or '?'})\n"
                    f"{corr_block}"
                )
            except Exception as e:
                logger.warning("correlation alert failed: %s", e)
            return 0.0
        if corr_mult != 1.0:
            target_pct *= corr_mult
            logger.debug(
                "rotation sizing: pair=%s mult=%.2f → target_pct=%.4f",
                intended_pair, corr_mult, target_pct,
            )

        # R49: tag-conditioned Kelly fraction.
        # Mode controlled by SUPERTREND_KELLY_MODE env:
        #   binary       — legacy 0.25 / 0.75 (scout / confirmed only)
        #   three_stage  — default: 0.25 / 0.50 / 0.85 (pre_scout / scout / confirmed)
        #   continuous   — kelly × quality × |dir_score| × adx_norm (all tags equal)
        kelly_mode = os.environ.get("SUPERTREND_KELLY_MODE", "three_stage")

        if kelly_mode == "continuous":
            # Continuous: each entry's size precisely reflects its signal strength
            pair = kwargs.get("pair", "")
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(dataframe) > 0:
                row = dataframe.iloc[-1]
                quality_now = float(row.get("trend_quality", 0.5))
                dir_now = abs(float(row.get("direction_score", 0.0)))
                adx_now = float(row.get("adx", 25.0))
                adx_norm = min(adx_now / 50.0, 1.0)
                strength = quality_now * dir_now * adx_norm
                # Cap range: 0.10 (min meaningful) to 1.0 (perfect signal)
                target_pct *= max(0.10, min(strength, 1.0))
            else:
                target_pct *= 0.30   # safe fallback
        elif kelly_mode == "three_stage":
            # New 3-tier scaling
            stage_map = {
                "pre_scout": 0.25,   # earliest signal, smallest test
                "scout":     0.50,   # 1D+4H+1H aligned, mid-size
                "confirmed": 0.85,   # 4-tf perfect alignment, near-max
            }
            target_pct *= stage_map.get(entry_tag or "", 0.50)
        else:
            # Binary legacy mode (backwards compat)
            if entry_tag == "scout":
                target_pct *= 0.25
            else:
                target_pct *= 0.75

        balance = self.wallets.get_total_stake_amount() if self.wallets else 1000
        base_stake = balance * target_pct

        pair = kwargs.get("pair", "")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        quality_scale = 1.0
        if len(dataframe) > 0:
            q = float(dataframe.iloc[-1].get("trend_quality", 0.5))
            quality_scale = 0.7 + q * 0.6

        stake = base_stake * quality_scale
        stake = min(stake, balance * 0.25)
        if min_stake is not None:
            stake = max(stake, min_stake)
        return min(stake, max_stake)

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> float | None:
        """Scout DCA + partial exits for big winners."""
        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if len(dataframe) == 0:
            return None
        last = dataframe.iloc[-1]
        entries_done = trade.nr_of_successful_entries
        exits_done = trade.nr_of_successful_exits
        is_long = not trade.is_short

        # === Phase 1→2: Scout DCA when 15m confirms ===
        # P2-8 (round 47): scale the addon by CURRENT trend_quality.
        # Old behaviour: blind ×2.0 on every scout→confirm transition.
        # Problem: if quality at confirmation has degraded (e.g. ADX
        # collapsed), we'd still 3x the position into a deteriorating
        # trend. New behaviour: addon multiplier slides 1.5×–2.5×
        # based on quality (0.5 → 1.5×, 1.0 → 2.5×).
        if trade.enter_tag == "scout" and entries_done == 1:
            # 15m just flipped in our direction?
            is_15m_confirmed = (
                (is_long and last.get("st_trend") == 1)
                or (not is_long and last.get("st_trend") == -1)
            )
            if is_15m_confirmed and current_profit > -0.02:
                quality_now = float(last.get("trend_quality", 0.5))
                # Quality 0.5 → 1.5× (normal); 1.0 → 2.5× (high-conviction)
                # Quality 0.0 → 1.5× (clamped: don't go below baseline)
                addon_mult = 1.5 + max(0.0, quality_now - 0.5) * 2.0
                addon = trade.stake_amount * addon_mult
                logger.info(
                    "scout DCA: quality=%.2f → addon=%.2fx stake=$%.2f",
                    quality_now, addon_mult, addon,
                )
                return addon

        # R50: weighted-exit partial reduction hook (score 0.5-0.75 → -50%).
        # Only fires when SUPERTREND_EXIT_MODE is "weighted" (default).
        # Reads the score persisted by custom_exit on the trade.
        exit_mode = os.environ.get("SUPERTREND_EXIT_MODE", "weighted")
        if exit_mode == "weighted" and exits_done == 0:
            try:
                pending_score = float(
                    trade.get_custom_data("exit_signal_score", 0.0) or 0.0,
                )
            except Exception:
                pending_score = 0.0
            if 0.50 <= pending_score < 0.75:
                _safe_journal_write(PartialExitEvent(
                    timestamp=now_iso(),
                    pair=trade.pair,
                    side="long" if is_long else "short",
                    entry_price=float(trade.open_rate),
                    exit_price=float(current_rate),
                    portion_pct=50.0,
                    profit_pct_at_partial=current_profit * 100,
                    profit_usd_at_partial=trade.calc_profit(current_rate),
                    trigger=f"R50 weighted exit score {pending_score:.2f}",
                    state=_snapshot_state(last),
                    note="weighted exit partial reduce",
                ))
                # Clear the score so it doesn't double-fire
                try:
                    trade.set_custom_data("exit_signal_score", 0.0)
                except Exception:
                    pass
                return -(trade.stake_amount * 0.50)

        # === Partial exits for big winners ===
        is_1h_against = (
            (is_long and last.get("st_1h", 1) == -1)
            or (not is_long and last.get("st_1h", -1) == 1)
        )
        if current_profit > 0.15 and exits_done == 0 and is_1h_against:
            # Round 46: journal the partial exit decision
            _safe_journal_write(PartialExitEvent(
                timestamp=now_iso(),
                pair=trade.pair,
                side="long" if is_long else "short",
                entry_price=float(trade.open_rate),
                exit_price=float(current_rate),
                portion_pct=50.0,
                profit_pct_at_partial=current_profit * 100,
                profit_usd_at_partial=trade.calc_profit(current_rate),
                trigger="15% profit + 1H trend against",
                state=_snapshot_state(last),
                note="P1: lock first half",
            ))
            return -(trade.stake_amount * 0.50)

        is_15m_against = (
            (is_long and last.get("st_trend", 1) == -1)
            or (not is_long and last.get("st_trend", -1) == 1)
        )
        if current_profit > 0.30 and exits_done == 1 and is_15m_against:
            _safe_journal_write(PartialExitEvent(
                timestamp=now_iso(),
                pair=trade.pair,
                side="long" if is_long else "short",
                entry_price=float(trade.open_rate),
                exit_price=float(current_rate),
                portion_pct=30.0,
                profit_pct_at_partial=current_profit * 100,
                profit_usd_at_partial=trade.calc_profit(current_rate),
                trigger="30% profit + 15m trend against",
                state=_snapshot_state(last),
                note="P2: take more off the table",
            ))
            return -(trade.stake_amount * 0.30)

        return None

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        """Round 46: rich entry — Telegram + structured journal event with
        full reasoning, planned SL/TP plan, and multi-TF state.

        R57: pre-entry alpha filters (funding contra-signal + orderbook
        microstructure) run BEFORE journal write. Both default OFF —
        SUPERTREND_FR_ALPHA=1 / SUPERTREND_ORDERBOOK_CONFIRM=1 to enable.
        Blocked entries get a SkippedEvent + Telegram and return False.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_row = dataframe.iloc[-1] if len(dataframe) > 0 else None
        state = _snapshot_state(last_row)

        # R57: pre-entry alpha filters (no-op when env vars unset)
        block_reason = self._pre_entry_filter_block(pair, side, state)
        if block_reason:
            logger.info("entry blocked: %s %s — %s", pair, side, block_reason)
            try:
                _safe_journal_write(SkippedEvent(
                    timestamp=now_iso(),
                    pair=pair,
                    side=side,
                    reason=f"R57 pre-entry filter: {block_reason}",
                    state=state,
                ))
            except Exception:
                pass
            try:
                _send_to_all_bots(
                    f"🚫 *進場攔截* `{pair}` {side.upper()}\n"
                    f"原因: {block_reason}"
                )
            except Exception:
                pass
            return False

        leverage = float(kwargs.get("leverage") or 1.0)
        notional_usd = amount * rate
        stake_usd = notional_usd / leverage if leverage > 0 else notional_usd

        sl_plan = default_stoploss_plan(side)
        tp_plan = default_take_profit_plan()

        kelly_full = self._calc_rolling_kelly()
        # Tag-conditioned scaling that mirrors custom_stake_amount logic
        kelly_scaled = kelly_full * (0.25 if entry_tag == "scout" else 0.75)
        quality_scale = 0.7 + state.trend_quality * 0.6

        # R48: pull current regime for journal context
        regime_snap = self._current_regime_snapshot()
        regime_str = (
            regime_snap.as_compact_str() if regime_snap is not None
            else "unknown"
        )

        # Persist the entry event (journal failures silently ignored)
        try:
            _safe_journal_write(EntryEvent(
                timestamp=now_iso(),
                pair=pair,
                side=side,
                entry_tag=entry_tag or "unknown",
                entry_price=float(rate),
                amount=float(amount),
                notional_usd=notional_usd,
                leverage=leverage,
                stake_usd=stake_usd,
                state=state,
                stoploss_plan=sl_plan,
                take_profit_plan=tp_plan,
                kelly_fraction=kelly_full,
                kelly_window=self._KELLY_LOOKBACK,
                quality_scale=quality_scale,
                cb_active=False,   # if cb_active we wouldn't be here
                note=f"Entry {entry_tag} {side} @ {rate:.4f} | regime={regime_str}",
            ))
        except Exception as e:
            logger.warning("entry journal write failed: %s", e)

        # Rich Telegram message (now includes SL/TP plan + leverage + Kelly + state)
        emoji = "🟢" if side == "long" else "🔴"
        phase = "🔍 試單" if entry_tag == "scout" else "✅ 確認"

        sl_at = rate * (1 + sl_plan.initial_sl_pct / 100) if side == "long" \
                else rate * (1 - sl_plan.initial_sl_pct / 100)

        _send_to_all_bots(
            f"{emoji} *進場 {side.upper()}* ({phase})\n"
            f"幣種: `{pair}` 價格: `{rate:.4f}`\n"
            f"\n"
            f"💰 *倉位*\n"
            f"   名目: `${notional_usd:,.0f}` | 槓桿: `{leverage:.1f}x` "
            f"| 保證金: `${stake_usd:,.0f}`\n"
            f"   Kelly: `{kelly_full:.1%}` (×`{0.25 if entry_tag == 'scout' else 0.75:.0%}` for {entry_tag}) "
            f"| 品質係數: `{quality_scale:.2f}`\n"
            f"\n"
            f"🛡️ *停損計畫*\n"
            f"   初始 SL: `{sl_plan.initial_sl_pct:.1f}%` (≈ `{sl_at:.4f}`)\n"
            f"   階段 1: 獲利 `{sl_plan.phase_1_trigger_pct:.1f}%` "
            f"→ 鎖盈 `{sl_plan.phase_1_lock_pct:+.1f}%` (覆蓋手續費)\n"
            f"   階段 2: 獲利 `{sl_plan.phase_2_trigger_pct:.1f}%` "
            f"→ 鎖 `{sl_plan.phase_2_lock_pct:.0%}` 利潤\n"
            f"   階段 3: 獲利 `{sl_plan.phase_3_trigger_pct:.1f}%` "
            f"→ 鎖 `{sl_plan.phase_3_lock_pct:.0%}` 利潤\n"
            f"\n"
            f"🎯 *停利規劃*\n"
            f"   `{tp_plan.partial_1_at_profit_pct:.0f}%` 獲利 + `1H` 反轉 → 出 `{tp_plan.partial_1_off_pct:.0f}%`\n"
            f"   `{tp_plan.partial_2_at_profit_pct:.0f}%` 獲利 + `15m` 反轉 → 再出 `{tp_plan.partial_2_off_pct:.0f}%`\n"
            f"   `1D` 反轉 → 全平 (尾單放飛)\n"
            f"\n"
            f"📊 *多時框狀態*\n"
            f"   1D: `{_arrow(state.st_1d)}` ({state.st_1d_duration:.0f}日) | "
            f"4H: `{state.dir_4h_score:+.2f}` | "
            f"1H: `{_arrow(state.st_1h)}` | "
            f"15m: `{_arrow(state.st_15m)}`\n"
            f"   方向分: `{state.direction_score:+.2f}` | 品質: `{state.trend_quality:.2f}` "
            f"| ADX: `{state.adx:.1f}`\n"
            f"   ATR: `{state.atr:.4f}` | FR: `{state.funding_rate:+.4%}`\n"
            f"\n"
            f"🌐 *市場 Regime*: `{regime_str}`\n"
            f"\n"
            f"策略: Supertrend 4L Scout (R48)"
        )
        return True

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime, **kwargs) -> bool:
        """Round 46: rich exit — Telegram + structured journal with max
        profit ever seen, trailing phase at exit, and final state."""
        pnl_pct = trade.calc_profit_ratio(rate) * 100
        pnl_usd = trade.calc_profit(rate)
        dur = (current_time - trade.open_date_utc).total_seconds() / 3600

        # Pull the max profit + phase recorded by custom_stoploss
        max_profit_pct = pnl_pct
        trailing_phase = 0
        try:
            max_profit_pct = max(
                float(trade.get_custom_data("max_profit_pct", 0.0) or 0.0),
                pnl_pct,
            )
            trailing_phase = int(trade.get_custom_data("trailing_phase", 0) or 0)
        except Exception:
            pass

        # Determine if it was a partial-followed-by-close (multiple exits)
        n_partials = getattr(trade, "nr_of_successful_exits", 0)

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_row = dataframe.iloc[-1] if len(dataframe) > 0 else None
        state = _snapshot_state(last_row)

        side = "short" if trade.is_short else "long"

        try:
            _safe_journal_write(ExitEvent(
                timestamp=now_iso(),
                pair=pair,
                side=side,
                entry_price=float(trade.open_rate),
                exit_price=float(rate),
                pnl_pct=float(pnl_pct),
                pnl_usd=float(pnl_usd),
                duration_hours=float(dur),
                exit_reason=exit_reason,
                max_profit_pct=float(max_profit_pct),
                trailing_phase_at_exit=trailing_phase,
                n_partials_taken=int(n_partials),
                state=state,
                entry_tag=getattr(trade, "enter_tag", "unknown") or "unknown",
                note=f"{side} {pair} {pnl_pct:+.2f}% via {exit_reason}",
            ))
        except Exception as e:
            logger.warning("exit journal write failed: %s", e)

        emoji = "💰" if pnl_pct > 0 else "💸"
        # Show how much we left on the table (max - final)
        slippage = max_profit_pct - pnl_pct
        slippage_note = (
            f" (高點留 `{slippage:+.2f}%` 在桌上)"
            if pnl_pct > 0 and slippage > 0.5 else ""
        )

        _send_to_all_bots(
            f"{emoji} *出場 {side.upper()}*\n"
            f"幣種: `{pair}`\n"
            f"P&L: `{pnl_pct:+.2f}%` (`${pnl_usd:+.2f}`)\n"
            f"進/出: `{trade.open_rate:.4f}` → `{rate:.4f}`\n"
            f"持倉: `{dur:.1f}h` | 原因: `{exit_reason}`\n"
            f"最高未實現: `{max_profit_pct:+.2f}%`"
            f"{slippage_note}\n"
            f"trailing 階段: `{trailing_phase}/3` | 已部分出場: `{n_partials}` 次\n"
            f"出場時 1D `{_arrow(state.st_1d)}` 1H `{_arrow(state.st_1h)}` "
            f"15m `{_arrow(state.st_15m)}`"
        )
        return True


def _arrow(st: int) -> str:
    """Tiny formatter: +1 → ▲, -1 → ▼, else →"""
    if st > 0:
        return "▲"
    if st < 0:
        return "▼"
    return "→"

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 2.0

        last = dataframe.iloc[-1]
        quality = float(last.get("trend_quality", 0.5))
        adx = last.get("adx", 25)

        # Quality-weighted leverage: quality 0.5 → 2x, quality 1.0 → 5x
        lev = 1.0 + quality * 4.0
        # ADX boost
        lev += max(adx - 30, 0) * 0.05

        return min(max(lev, 1.5), 5.0)
