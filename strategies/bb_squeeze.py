"""BB/KC Squeeze Strategy — Volatility Compression Breakout.

Detects when Bollinger Bands contract inside Keltner Channels (squeeze),
then enters on the breakout direction when the squeeze releases.

Complements SMCTrend by covering the consolidation→breakout regime
where SMC has no signals (no OB/FVG zones form in tight ranges).

Core logic:
1. Squeeze ON: BB(20,2) fully inside KC(20 EMA, 2×ATR)
2. Squeeze OFF: BB expands back outside KC → breakout starting
3. Direction: Momentum oscillator (close - midpoint of highest high / lowest low)
4. Confirmation: 4H HTF trend + confidence engine + volume
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IStrategy, IntParameter
from pandas import DataFrame

# Ensure guards/ and market_monitor/ are importable
_parent = str(Path(__file__).resolve().parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

try:
    import smartmoneyconcepts as smc
except ImportError:
    smc = None

logger = logging.getLogger(__name__)

# Optional Telegram
try:
    from market_monitor.telegram_zh import send_message as _tg_send  # noqa: F401
    _TG = True
except ImportError:
    _TG = False

# Optional state store
try:
    from market_monitor.state_store import BotStateStore  # noqa: F401
    _STATE = True
except ImportError:
    _STATE = False


class BBSqueeze(IStrategy):
    """Bollinger Band / Keltner Channel Squeeze Breakout Strategy."""

    INTERFACE_VERSION = 3

    # --- Core settings ---
    timeframe = "15m"
    can_short = True
    stoploss = -0.05
    use_custom_stoploss = True
    trailing_stop = False
    startup_candle_count = 200
    process_only_new_candles = True

    # --- Protections ---
    @property
    def protections(self):
        return [
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 192,
                "trade_limit": 10,
                "stop_duration_candles": 48,
                "max_allowed_drawdown": 0.15,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 96,
                "trade_limit": 4,
                "stop_duration_candles": 24,
                "only_per_pair": False,
            },
        ]

    # --- Hyperopt parameters ---
    bb_period = IntParameter(15, 30, default=20, space="buy", optimize=True)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, space="buy", optimize=True)
    kc_period = IntParameter(15, 30, default=20, space="buy", optimize=True)
    kc_mult = DecimalParameter(1.0, 3.0, default=2.0, space="buy", optimize=True)
    mom_period = IntParameter(8, 20, default=12, space="buy", optimize=True)
    atr_period = IntParameter(10, 20, default=14, space="buy", optimize=True)
    atr_sl_mult = DecimalParameter(1.0, 2.5, default=1.5, space="buy", optimize=True)
    max_leverage = DecimalParameter(1.0, 5.0, default=3.0, space="buy", optimize=True)
    min_confidence = DecimalParameter(0.2, 0.6, default=0.4, space="buy", optimize=True)

    # --- Position management ---
    position_adjustment_enable = True
    max_entry_position_adjustment = 1  # 1 add-on max (2 total)

    # --- Informative pairs (4H HTF) ---
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, "4h") for pair in pairs]

    # ================================================================
    # INDICATORS
    # ================================================================

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]

        # --- Bollinger Bands ---
        bb = ta.BBANDS(dataframe, timeperiod=self.bb_period.value,
                       nbdevup=self.bb_std.value, nbdevdn=self.bb_std.value)
        dataframe["bb_upper"] = bb["upperband"]
        dataframe["bb_middle"] = bb["middleband"]
        dataframe["bb_lower"] = bb["lowerband"]

        # --- Keltner Channels ---
        dataframe["kc_middle"] = ta.EMA(dataframe, timeperiod=self.kc_period.value)
        atr = ta.ATR(dataframe, timeperiod=self.atr_period.value)
        dataframe["atr"] = atr
        dataframe["kc_upper"] = dataframe["kc_middle"] + self.kc_mult.value * atr
        dataframe["kc_lower"] = dataframe["kc_middle"] - self.kc_mult.value * atr

        # --- Squeeze detection ---
        dataframe["squeeze_on"] = (
            (dataframe["bb_lower"] > dataframe["kc_lower"])
            & (dataframe["bb_upper"] < dataframe["kc_upper"])
        )
        dataframe["squeeze_off"] = ~dataframe["squeeze_on"]
        # Squeeze just released (was on, now off)
        dataframe["squeeze_fire"] = (
            dataframe["squeeze_off"]
            & dataframe["squeeze_on"].shift(1).fillna(False)
        )

        # --- Momentum oscillator (TTM Squeeze style) ---
        # Momentum = close - midpoint(highest high, lowest low) over period
        highest = dataframe["high"].rolling(self.mom_period.value).max()
        lowest = dataframe["low"].rolling(self.mom_period.value).min()
        midpoint = (highest + lowest) / 2
        dataframe["momentum"] = dataframe["close"] - (midpoint + dataframe["kc_middle"]) / 2
        # Momentum direction (rising = bullish)
        dataframe["mom_rising"] = dataframe["momentum"] > dataframe["momentum"].shift(1)

        # --- ATR for stop-loss ---
        dataframe["atr_pct"] = atr / dataframe["close"]

        # --- Volume filter ---
        dataframe["vol_ma20"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_above_avg"] = dataframe["volume"] > dataframe["vol_ma20"]

        # --- RSI (for additional confluence) ---
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # --- 4H HTF Trend (simplified: EMA50 direction) ---
        if self.dp:
            htf_df = self.dp.get_pair_dataframe(pair, "4h")
            if len(htf_df) > 50:
                htf_df["ema50"] = ta.EMA(htf_df, timeperiod=50)
                htf_df["htf_trend"] = np.where(
                    htf_df["close"] > htf_df["ema50"], 1,
                    np.where(htf_df["close"] < htf_df["ema50"], -1, 0)
                )
                # Also compute HTF CHoCH if smartmoneyconcepts available
                if smc is not None and len(htf_df) > 20:
                    try:
                        htf_swing = smc.swing_highs_lows(htf_df, swing_length=10)
                        htf_df["swing_hl"] = htf_swing["HighLow"]
                        htf_bos = smc.bos_choch(htf_df, htf_df["swing_hl"], close_break=True)
                        htf_df["htf_choch"] = htf_bos["CHOCH"]
                        htf_df["htf_choch"] = htf_df["htf_choch"].ffill()
                    except Exception:
                        htf_df["htf_choch"] = 0

                import pandas as pd
                htf_df["htf_trend"] = htf_df["htf_trend"].ffill()
                htf_merge = htf_df[["date", "htf_trend", "htf_choch"]].copy()
                htf_merge["htf_choch"] = htf_merge["htf_choch"].fillna(0)
                dataframe = pd.merge_asof(
                    dataframe, htf_merge, on="date", direction="backward"
                )
            else:
                dataframe["htf_trend"] = 0
                dataframe["htf_choch"] = 0
        else:
            dataframe["htf_trend"] = 0
            dataframe["htf_choch"] = 0

        # --- Confidence (local, simplified) ---
        # Uses momentum alignment + volume + trend for a 0-1 score
        mom_score = np.clip(np.abs(dataframe["momentum"]) / (atr + 1e-10) * 0.5, 0, 1)
        trend_score = np.where(dataframe["htf_trend"] != 0, 0.7, 0.3)
        vol_score = np.clip(dataframe["volume"] / (dataframe["vol_ma20"] + 1e-10) * 0.5, 0.1, 1.0)
        dataframe["confidence"] = (mom_score * 0.4 + trend_score * 0.35 + vol_score * 0.25)
        dataframe["confidence"] = dataframe["confidence"].ewm(span=3).mean().clip(0, 1)

        return dataframe

    # ================================================================
    # ENTRY SIGNALS
    # ================================================================

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- LONG ---
        dataframe.loc[
            (
                (dataframe["squeeze_fire"] | (  # Squeeze just released
                    dataframe["squeeze_off"]     # OR already off + momentum accelerating
                    & dataframe["mom_rising"]
                    & dataframe["momentum"].shift(1).fillna(0) <= 0  # Crossed above zero
                    & dataframe["momentum"] > 0
                ))
                & (dataframe["momentum"] > 0)           # Momentum bullish
                & (dataframe["htf_trend"] > 0)           # 4H bullish
                & (dataframe["confidence"] > self.min_confidence.value)
                & (dataframe["vol_above_avg"])            # Volume confirmation
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # --- SHORT ---
        dataframe.loc[
            (
                (dataframe["squeeze_fire"] | (
                    dataframe["squeeze_off"]
                    & (~dataframe["mom_rising"])
                    & dataframe["momentum"].shift(1).fillna(0) >= 0
                    & dataframe["momentum"] < 0
                ))
                & (dataframe["momentum"] < 0)            # Momentum bearish
                & (dataframe["htf_trend"] < 0)           # 4H bearish
                & (dataframe["confidence"] > self.min_confidence.value)
                & (dataframe["vol_above_avg"])
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        # Log signals
        n_long = int(dataframe.get("enter_long", 0).sum())
        n_short = int(dataframe.get("enter_short", 0).sum())
        if len(dataframe) > 0:
            last = dataframe.iloc[-1]
            logger.info(
                "BBSqueeze %s: %d long, %d short | squeeze=%s mom=%.4f htf=%d conf=%.2f",
                metadata.get("pair", "?"), n_long, n_short,
                "FIRE" if last.get("squeeze_fire") else ("ON" if last.get("squeeze_on") else "OFF"),
                last.get("momentum", 0), last.get("htf_trend", 0), last.get("confidence", 0),
            )

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exits handled by custom_exit + custom_stoploss
        return dataframe

    # ================================================================
    # EXIT LOGIC
    # ================================================================

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float,
                    **kwargs) -> Optional[str]:
        """Exit on 2.5R take-profit or HTF CHoCH reversal."""
        # Minimum hold: 4 candles (1 hour on 15m)
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60
        if trade_duration < 60:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        atr = last.get("atr", 0)
        if atr > 0:
            atr_sl_pct = (atr * self.atr_sl_mult.value) / trade.open_rate
            if atr_sl_pct > 0:
                position_profit = current_profit / max(trade.leverage, 1.0)
                r_multiple = position_profit / atr_sl_pct
                if r_multiple >= 2.0:
                    return "take_profit_2R"

        # Momentum reversal exit (after hold time)
        if not trade.is_short and last.get("momentum", 0) < 0 and last.get("mom_rising") is False:
            return "momentum_reversal"
        elif trade.is_short and last.get("momentum", 0) > 0 and last.get("mom_rising") is True:
            return "momentum_reversal"

        # HTF CHoCH exit
        if trade.is_short and last.get("htf_choch") == 1:
            return "htf_choch_reversal"
        elif not trade.is_short and last.get("htf_choch") == -1:
            return "htf_choch_reversal"

        return None

    # ================================================================
    # STOP-LOSS
    # ================================================================

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float | None:
        """ATR-based stop with breakeven at 1R."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        atr = dataframe.iloc[-1].get("atr", 0)
        if atr <= 0:
            return None

        atr_sl_pct = max((atr * self.atr_sl_mult.value) / trade.open_rate, 0.003)
        position_profit = current_profit / max(trade.leverage, 1.0)
        r_multiple = position_profit / atr_sl_pct if atr_sl_pct > 0 else 0

        if r_multiple >= 1.5:
            # Trail at 0.7R below
            trail = atr_sl_pct * 0.7
            return max(-trail, -0.008)
        elif r_multiple >= 1.0:
            # Breakeven + 0.3% buffer
            breakeven_sl = -(current_profit - 0.003)
            return min(breakeven_sl, -0.003)
        else:
            # Initial: ATR-based stop
            return -atr_sl_pct

    # ================================================================
    # POSITION SIZING
    # ================================================================

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Confidence-based leverage (same formula as SMC)."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 1.0
        confidence = dataframe.iloc[-1].get("confidence", 0.5)
        max_lev = self.max_leverage.value
        lev = 1.0 + (max_lev - 1.0) * (confidence ** 2)
        return min(max(lev, 1.0), max_leverage)

    def custom_stake_amount(self, current_time, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str, **kwargs) -> float:
        """Confidence-scaled sizing with Guard pre-limit."""
        pair = kwargs.get("pair", "")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        confidence = dataframe.iloc[-1].get("confidence", 0.5)
        scale = 0.2 + 1.3 * confidence
        adjusted = proposed_stake * scale

        # Risk cap: 2% of account
        try:
            if self.wallets:
                acct = self.wallets.get_total("USDT")
                if acct <= 0:
                    return 0
                atr = dataframe.iloc[-1].get("atr", 0)
                if atr > 0 and current_rate > 0:
                    atr_sl_pct = max((atr * self.atr_sl_mult.value) / current_rate, 0.003)
                    max_risk = (acct * 0.02) / atr_sl_pct
                    adjusted = min(adjusted, max_risk)
        except Exception as e:
            logger.error("Risk cap failed: %s", e)

        # Pre-limit for MaxPositionGuard (30%)
        try:
            if self.wallets and current_rate > 0:
                acct = self.wallets.get_total("USDT")
                est_lev = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)
                max_pos = acct * 0.30
                max_stake_guard = max_pos / est_lev if est_lev > 0 else max_pos
                if adjusted > max_stake_guard:
                    logger.info("BBSqueeze stake %.1f → %.1f (30%% guard, lev=%.2fx)",
                                adjusted, max_stake_guard, est_lev)
                    adjusted = max_stake_guard
        except Exception as e:
            logger.error("Pre-limit failed: %s", e)

        if min_stake is not None:
            adjusted = max(adjusted, min_stake)
        return min(adjusted, max_stake)

    # ================================================================
    # TRADE CONFIRMATION (Guard Pipeline)
    # ================================================================

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        """Guard pipeline check (shared with SMC)."""
        if self.config.get("runmode", {}).value in ("live", "dry_run"):
            try:
                from guards.base import GuardContext
                from guards.pipeline import create_default_pipeline

                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                confidence = 0.5
                if len(dataframe) > 0:
                    confidence = dataframe.iloc[-1].get("confidence", 0.5)
                actual_leverage = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)

                # Fallback shrink
                stake_usd = amount * rate
                acct_bal = self.wallets.get_total("USDT") if self.wallets else 1000
                max_pos = acct_bal * 0.30
                if stake_usd * actual_leverage > max_pos and actual_leverage > 0:
                    stake_usd = max_pos / actual_leverage
                    amount = stake_usd / rate if rate > 0 else amount

                open_pos = {}
                for t in Trade.get_trades_proxy(is_open=True):
                    open_pos[t.pair] = {
                        "value": t.stake_amount * t.leverage,
                        "side": t.trade_direction if hasattr(t, "trade_direction") else "long",
                    }

                ctx = GuardContext(
                    symbol=pair,
                    side="short" if side == "short" else "long",
                    amount=stake_usd,
                    leverage=actual_leverage,
                    account_balance=acct_bal,
                    open_positions=open_pos,
                    confidence=confidence,
                )
                pipeline = create_default_pipeline()
                rejection = pipeline.run(ctx)
                if rejection:
                    logger.warning("BBSqueeze Guard rejected %s %s: %s", pair, side, rejection)
                    return False
            except Exception as e:
                logger.error("BBSqueeze Guard error — BLOCKING: %s", e)
                return False
        return True

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime, **kwargs) -> bool:
        """Record guard state on exit."""
        profit_usdt = trade.calc_profit(rate)
        try:
            from guards.pipeline import get_guard, save_state
            from guards.guards import CooldownGuard, DailyLossGuard, ConsecutiveLossGuard

            cooldown = get_guard(CooldownGuard)
            if cooldown:
                cooldown.record_trade(pair)
            consec = get_guard(ConsecutiveLossGuard)
            if consec:
                consec.record_result(is_win=(profit_usdt > 0))
            if profit_usdt < 0:
                daily = get_guard(DailyLossGuard)
                if daily:
                    daily.record_loss(abs(profit_usdt))
            save_state()
        except Exception as e:
            logger.error("BBSqueeze guard state update failed: %s", e)
        return True

    # ================================================================
    # PARTIAL PROFIT-TAKING
    # ================================================================

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> float | None:
        """50% at 1R, remaining 50% trails."""
        if current_profit <= 0:
            return None

        pair = trade.pair
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        atr = dataframe.iloc[-1].get("atr", 0)
        if atr > 0 and trade.stake_amount > 0:
            atr_sl_pct = (atr * self.atr_sl_mult.value) / trade.open_rate
            if atr_sl_pct > 0:
                position_profit = current_profit / max(trade.leverage, 1.0)
                r_multiple = position_profit / atr_sl_pct

                partials = int(trade.get_custom_data("partials") or 0) if hasattr(trade, "get_custom_data") else 0

                # 1.0R → sell 50%
                if r_multiple >= 1.0 and partials < 1:
                    if hasattr(trade, "set_custom_data"):
                        trade.set_custom_data("partials", 1)
                    partial = trade.stake_amount * 0.50
                    if min_stake and partial < min_stake:
                        return None
                    logger.info("BBSqueeze partial 1R %s: -%.2f (R=%.1f)", pair, partial, r_multiple)
                    return -partial

        return None
