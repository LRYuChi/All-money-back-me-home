"""Mean Reversion strategy — R67.

Chop-period complement to SupertrendStrategy. Built on the explicit
observation that R48 regime detector classifies BTC into 4 buckets:

    TRENDING            → SUPERTREND fires (1.0× sizing)
    VOLATILE_TRENDING   → SUPERTREND fires (0.7× sizing)
    CHOPPY              → SUPERTREND scales to 0.3× — MOST CAPITAL IDLE
    DEAD                → SUPERTREND blocks entirely — ALL CAPITAL IDLE

This strategy targets the bottom two regimes — when the trend strategy
correctly stops working, this one starts. Net effect: at least one leg
is always producing edge, capital stops idling during chop.

Entry logic (CHOPPY / VOLATILE_TRENDING only):
  LONG  when close < BB_lower(20, 2.0σ) AND RSI(14) < 30
  SHORT when close > BB_upper(20, 2.0σ) AND RSI(14) > 70

Exit:
  Primary    — close crosses BB_mid (mean reversion target)
  Stop       — 1.5× ATR(14) hard stop at exchange
  Time stop  — 24h max hold (chop windows are short; if MR thesis
               doesn't play out in 24h it probably won't)
  Regime    — exit immediately on regime → TRENDING (means breakout
               in progress, MR setup is invalidated)

Position sizing:
  Smaller than SUPERTREND by design. MR has higher trade frequency but
  lower per-trade edge. Default 0.05 of bankroll per trade (Kelly half
  of conservative 0.10 on assumed win-rate 0.60 + 1:1 R/R).

Escape hatches (all default OFF until paper-trading observation period):
  MR_ENABLED                  — master switch (default 0)
  MR_REGIME_GATE              — 1 = only fire in CHOPPY/VOLATILE_TRENDING (default 1)
  MR_BB_PERIOD / MR_BB_SIGMA  — Bollinger params (default 20 / 2.0)
  MR_RSI_OVERSOLD / OVERBOUGHT — entry thresholds (default 30 / 70)
  MR_ATR_STOP_MULT            — stop loss = N × ATR (default 1.5)
  MR_TIME_STOP_HOURS          — max hold (default 24)
  MR_KELLY_FRACTION           — bankroll % per trade (default 0.05)

Designed to share strategies/journal.py + dashboard endpoints with
SUPERTREND. Trade events go to the same JSONL stream tagged with
strategy="mean_reversion".
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame

# Re-use SUPERTREND scaffolding (project root on path)
_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

logger = logging.getLogger(__name__)

# Reuse journal + regime modules. Defensive imports — strategy must
# never fail to load if journal infra is unavailable.
try:
    from strategies.journal import (
        EntryEvent,
        ExitEvent,
        MultiTfState,
        SkippedEvent,
        TradeJournal,
        now_iso,
    )
    _JOURNAL_DIR = os.environ.get(
        "SUPERTREND_JOURNAL_DIR", "trading_log/journal",
    )
    _journal: TradeJournal | None = TradeJournal(_JOURNAL_DIR)
except Exception:
    _journal = None

try:
    from strategies.market_regime import (
        MarketRegimeDetector,
        Regime,
    )
    _REGIME_AVAILABLE = True
except Exception:
    _REGIME_AVAILABLE = False


def _safe_journal_write(event) -> None:
    if _journal is None:
        return
    try:
        _journal.write(event)
    except Exception as e:
        logger.warning("MR journal write failed: %s", e)


# =================================================================== #
# Pure indicator helpers (testable without IStrategy scaffolding)
# =================================================================== #
def compute_bollinger(close: pd.Series, period: int = 20,
                      sigma: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower) Bollinger Bands."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + sigma * std
    lower = mid - sigma * std
    return upper, mid, lower


def is_long_entry(close: float, bb_lower: float, rsi: float,
                  rsi_oversold: float = 30.0) -> bool:
    """Pure entry-condition check for long. NaN-safe."""
    if any(pd.isna(x) for x in (close, bb_lower, rsi)):
        return False
    return close < bb_lower and rsi < rsi_oversold


def is_short_entry(close: float, bb_upper: float, rsi: float,
                   rsi_overbought: float = 70.0) -> bool:
    """Pure entry-condition check for short. NaN-safe."""
    if any(pd.isna(x) for x in (close, bb_upper, rsi)):
        return False
    return close > bb_upper and rsi > rsi_overbought


def is_mean_reverted(close: float, bb_mid: float, side: str,
                     tolerance: float = 0.001) -> bool:
    """Has the price reverted to (or past) the BB midline for given side?
    `tolerance` is fractional — 0.001 = 0.1% slack."""
    if any(pd.isna(x) for x in (close, bb_mid)):
        return False
    if side == "long":
        # Reverted means price has risen back to >= mid (within tolerance)
        return close >= bb_mid * (1 - tolerance)
    if side == "short":
        return close <= bb_mid * (1 + tolerance)
    return False


# =================================================================== #
# Strategy class
# =================================================================== #
class MeanReversionStrategy(IStrategy):
    """BB + RSI mean-reversion. Gated to CHOPPY/VOLATILE_TRENDING regimes.

    Capital-efficient complement to SupertrendStrategy — fires precisely
    when SUPERTREND is sized down, so total portfolio always has at
    least one strategy producing edge.
    """

    INTERFACE_VERSION = 3

    timeframe = "15m"
    startup_candle_count = 60   # 30 BB + 14 RSI + buffer

    # Hard stoploss is set dynamically per-pair via custom_stoploss
    # (1.5× ATR). Static -3% is a fallback safety floor — if custom
    # returns None, FT uses this.
    stoploss = -0.03
    trailing_stop = False
    use_custom_stoploss = True

    can_short = True
    trading_mode = "futures"
    margin_mode = "isolated"

    # Conservative — MR has higher freq but smaller per-trade edge
    process_only_new_candles = True

    # ---- Tunable params (env-overridable) ---- #
    bb_period = int(os.environ.get("MR_BB_PERIOD", "20"))
    bb_sigma = float(os.environ.get("MR_BB_SIGMA", "2.0"))
    rsi_period = 14
    rsi_oversold = float(os.environ.get("MR_RSI_OVERSOLD", "30"))
    rsi_overbought = float(os.environ.get("MR_RSI_OVERBOUGHT", "70"))
    atr_period = 14
    atr_stop_mult = float(os.environ.get("MR_ATR_STOP_MULT", "1.5"))
    time_stop_hours = float(os.environ.get("MR_TIME_STOP_HOURS", "24"))
    kelly_fraction = float(os.environ.get("MR_KELLY_FRACTION", "0.05"))

    # Regime detector cache (lazy init — first call to _gate_passes)
    _regime_detector_cache = None

    def _get_regime_detector(self):
        if not _REGIME_AVAILABLE:
            return None
        if self._regime_detector_cache is None:
            try:
                self._regime_detector_cache = MarketRegimeDetector()
            except Exception as e:
                logger.warning("MR: regime detector init failed: %s", e)
        return self._regime_detector_cache

    def _regime_gate_passes(self) -> tuple[bool, str]:
        """Returns (allow_entry, reason). Default ALLOW when detector
        is unavailable — fail-open to avoid silently disabling strategy."""
        if os.environ.get("MR_REGIME_GATE", "1") != "1":
            return True, "gate_off"
        det = self._get_regime_detector()
        if det is None:
            return True, "detector_unavailable"
        try:
            snap = det.detect()
            if snap.regime in (Regime.CHOPPY, Regime.VOLATILE_TRENDING):
                return True, f"regime_ok={snap.regime.value}"
            return False, f"regime_blocks={snap.regime.value}"
        except Exception as e:
            logger.debug("MR: regime detect error: %s", e)
            return True, "detector_error"

    # ----------------------------------------------------------------- #
    # populate_indicators
    # ----------------------------------------------------------------- #
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # BB bands
        upper, mid, lower = compute_bollinger(
            dataframe["close"], self.bb_period, self.bb_sigma,
        )
        dataframe["bb_upper"] = upper
        dataframe["bb_mid"] = mid
        dataframe["bb_lower"] = lower

        # RSI
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)

        # ATR for stop loss
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)

        return dataframe

    # ----------------------------------------------------------------- #
    # populate_entry_trend
    # ----------------------------------------------------------------- #
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if os.environ.get("MR_ENABLED", "0") != "1":
            return dataframe   # master switch off → never enter

        # Edge-trigger: only fire on the candle the condition first forms
        # (mirrors SUPERTREND's design — avoids over-firing while in
        # a sustained oversold/overbought zone)
        oversold_now = (dataframe["close"] < dataframe["bb_lower"]) & \
                       (dataframe["rsi"] < self.rsi_oversold)
        overbought_now = (dataframe["close"] > dataframe["bb_upper"]) & \
                         (dataframe["rsi"] > self.rsi_overbought)

        # `astype(bool)` avoids pandas FutureWarning about object→bool downcast
        prev_oversold = oversold_now.shift(1).fillna(False).astype(bool)
        prev_overbought = overbought_now.shift(1).fillna(False).astype(bool)
        oversold_just = oversold_now & ~prev_oversold
        overbought_just = overbought_now & ~prev_overbought

        dataframe.loc[oversold_just, "enter_long"] = 1
        dataframe.loc[oversold_just, "enter_tag"] = "mr_long_oversold"

        dataframe.loc[overbought_just, "enter_short"] = 1
        dataframe.loc[overbought_just, "enter_tag"] = "mr_short_overbought"

        return dataframe

    # ----------------------------------------------------------------- #
    # populate_exit_trend — primary mean-reversion exit at midline
    # ----------------------------------------------------------------- #
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Long: exit when price reverts up to BB mid (or above)
        dataframe.loc[
            (dataframe["close"] >= dataframe["bb_mid"]),
            "exit_long",
        ] = 1
        # Short: exit when price reverts down to BB mid (or below)
        dataframe.loc[
            (dataframe["close"] <= dataframe["bb_mid"]),
            "exit_short",
        ] = 1
        return dataframe

    # ----------------------------------------------------------------- #
    # confirm_trade_entry — gate by regime + journal entry event
    # ----------------------------------------------------------------- #
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str,
                            current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        # Master switch (defensive — populate_entry_trend already gates,
        # but env may have been flipped between candle eval and order)
        if os.environ.get("MR_ENABLED", "0") != "1":
            return False

        # Regime gate
        allow, reason = self._regime_gate_passes()
        if not allow:
            logger.info("MR entry blocked: %s %s — %s", pair, side, reason)
            try:
                _safe_journal_write(SkippedEvent(
                    timestamp=now_iso(),
                    pair=pair, side=side,
                    reason=f"R67 MR regime gate: {reason}",
                    state=MultiTfState(),
                    note="mean_reversion",
                ))
            except Exception:
                pass
            return False

        # Journal the entry event for dashboard visibility
        try:
            _safe_journal_write(EntryEvent(
                timestamp=now_iso(),
                pair=pair,
                side=side,
                entry_tag=f"MR:{entry_tag or 'unknown'}",
                entry_price=float(rate),
                amount=float(amount),
                notional_usd=float(amount * rate),
                leverage=float(kwargs.get("leverage") or 1.0),
                stake_usd=float(amount * rate / max(kwargs.get("leverage") or 1.0, 1.0)),
                state=MultiTfState(),
                stoploss_plan=None,
                take_profit_plan=None,
                kelly_fraction=self.kelly_fraction,
                kelly_window=0,
                quality_scale=1.0,
                cb_active=False,
                note=f"MR entry — {reason}",
            ))
        except Exception as e:
            logger.warning("MR entry journal write failed: %s", e)

        return True

    # ----------------------------------------------------------------- #
    # custom_stoploss — 1.5× ATR hard stop
    # ----------------------------------------------------------------- #
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        **kwargs) -> float | None:
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) == 0:
                return None
            atr = float(df.iloc[-1].get("atr", 0))
            if atr <= 0:
                return None
            entry_price = float(trade.open_rate)
            stop_distance = atr * self.atr_stop_mult
            sl_pct = -(stop_distance / entry_price)
            # Cap at -10% — ATR can spike absurd values, never go wider
            return max(sl_pct, -0.10)
        except Exception:
            return None

    # ----------------------------------------------------------------- #
    # custom_exit — time stop + regime invalidation
    # ----------------------------------------------------------------- #
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float,
                    **kwargs) -> str | None:
        # Time stop
        held_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if held_hours > self.time_stop_hours:
            return f"mr_time_stop_{int(held_hours)}h"

        # Regime invalidation — if BTC just transitioned to TRENDING, the
        # MR thesis (chop range-bound) is invalid; close immediately
        if os.environ.get("MR_REGIME_GATE", "1") == "1" and _REGIME_AVAILABLE:
            det = self._get_regime_detector()
            if det is not None:
                try:
                    snap = det.detect()
                    if snap.regime == Regime.TRENDING:
                        return f"mr_regime_invalidated_{snap.regime.value}"
                except Exception:
                    pass

        return None

    # ----------------------------------------------------------------- #
    # custom_stake_amount — fixed-fraction Kelly
    # ----------------------------------------------------------------- #
    def custom_stake_amount(self, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str, **kwargs) -> float:
        if os.environ.get("MR_ENABLED", "0") != "1":
            return 0.0
        balance = self.wallets.get_total_stake_amount() if self.wallets else 1000
        target = balance * self.kelly_fraction
        if min_stake is not None:
            target = max(target, min_stake)
        return min(target, max_stake)

    # ----------------------------------------------------------------- #
    # confirm_trade_exit — journal exit event with PnL
    # ----------------------------------------------------------------- #
    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime,
                           **kwargs) -> bool:
        try:
            entry_price = float(trade.open_rate)
            exit_price = float(rate)
            side = "short" if trade.is_short else "long"
            if side == "long":
                pnl_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl_pct = (entry_price / exit_price - 1) * 100
            pnl_usd = pnl_pct / 100 * float(trade.stake_amount)
            duration_h = (current_time - trade.open_date_utc).total_seconds() / 3600

            _safe_journal_write(ExitEvent(
                timestamp=now_iso(),
                pair=pair,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                duration_hours=duration_h,
                exit_reason=f"mr:{exit_reason}",
                max_profit_pct=0.0,
                trailing_phase_at_exit=0,
                n_partials_taken=0,
                state=MultiTfState(),
                entry_tag=getattr(trade, "enter_tag", "mr_unknown") or "mr_unknown",
                note="mean_reversion",
            ))
        except Exception as e:
            logger.warning("MR exit journal write failed: %s", e)
        return True
