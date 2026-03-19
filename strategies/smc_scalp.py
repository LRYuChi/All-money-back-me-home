"""SMC Scalp — Short-term strategy derived from SMCTrend.

Architecture:
- 1H: BOS/CHoCH determines trend direction (HTF bias)
- 15m: Order Block + FVG for precise entries (LTF execution)
- Dynamic style: High confidence → Intraday Swing; Low confidence → Scalping
- ATR: Volatility-adjusted stop loss & dynamic take profit
- Killzone time filter (default ON for 15m)

Designed for USDT perpetual futures on OKX via Freqtrade.
"""

from __future__ import annotations

import logging
from datetime import datetime

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy
from pandas import DataFrame
from smartmoneyconcepts import smc

# Add project root to path for indicators import
_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from indicators.adam_projection import adam_projection  # noqa: E402

try:
    from market_monitor.telegram_zh import (  # noqa: E402
        notify_entry, notify_exit, notify_stoploss, notify_pyramid,
        notify_confidence_change,
    )
    _TG_AVAILABLE = True
except ImportError:
    _TG_AVAILABLE = False

try:
    from market_monitor.state_store import BotStateStore
    _STATE_AVAILABLE = True
except ImportError:
    _STATE_AVAILABLE = False

logger = logging.getLogger(__name__)


class SMCScalp(IStrategy):
    """SMC Short-term strategy — 15m entry with 1H trend bias."""

    INTERFACE_VERSION = 3

    # --- Strategy settings ---
    timeframe = "15m"
    can_short = True
    stoploss = -0.025  # 2.5% initial stop (tighter than SMCTrend's 3%)
    use_custom_stoploss = False
    trailing_stop = False

    # --- Protections (tighter for short-term) ---
    @property
    def protections(self):
        return [
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 32,   # 32 * 15m = 8hr
                "trade_limit": 8,
                "stop_duration_candles": 16,      # Pause 4hr
                "max_allowed_drawdown": 0.10,     # 10%
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 16,   # 16 * 15m = 4hr
                "trade_limit": 3,
                "stop_duration_candles": 8,       # Pause 2hr
                "only_per_pair": False,
            },
        ]

    # --- Live macro data cache ---
    _live_confidence: float | None = None
    _live_confidence_time: datetime | None = None

    # --- Signal audit tracking ---
    _signal_audit: dict = {}
    _stale_counter: int = 0
    _confidence_fetch_failures: int = 0
    _crypto_env_cache: dict = {}

    # Pyramid: allow 1 add-on (2 total entries)
    position_adjustment_enable = True
    max_entry_position_adjustment = 1

    startup_candle_count = 300  # 300 * 15m = 3.125 days

    # --- Hyperparameters ---
    # LTF (15m) SMC
    swing_length = IntParameter(3, 12, default=7, space="buy", optimize=True)
    # HTF (1H) SMC
    htf_swing_length = IntParameter(8, 20, default=12, space="buy", optimize=True)

    use_killzone = IntParameter(0, 1, default=1, space="buy", optimize=True)
    ob_strength_min = DecimalParameter(0.1, 0.8, default=0.3, space="buy", optimize=True)

    # ATR risk management
    atr_period = IntParameter(7, 20, default=14, space="buy", optimize=True)
    atr_sl_mult = DecimalParameter(0.8, 2.0, default=1.2, space="buy", optimize=True)
    # Dynamic TP: high-confidence and low-confidence multipliers
    atr_tp_mult_high = DecimalParameter(2.5, 5.0, default=3.5, space="sell", optimize=True)
    atr_tp_mult_low = DecimalParameter(1.0, 2.5, default=1.8, space="sell", optimize=True)

    # Adam Theory
    adam_lookback = IntParameter(12, 30, default=24, space="buy", optimize=True)
    use_adam_filter = IntParameter(0, 1, default=1, space="buy", optimize=True)

    # Confidence & leverage
    max_leverage = DecimalParameter(1.0, 5.0, default=3.0, space="buy", optimize=True)
    confidence_style_threshold = DecimalParameter(0.4, 0.8, default=0.6, space="buy",
                                                  optimize=True)

    # Premium/Discount window
    pd_window = IntParameter(16, 36, default=24, space="buy", optimize=True)

    # Active zone lifetimes (in 15m candles)
    ob_lifetime = IntParameter(8, 24, default=16, space="buy", optimize=True)
    fvg_lifetime = IntParameter(6, 20, default=12, space="buy", optimize=True)

    # Time decay (in 15m candles)
    time_decay_high = IntParameter(32, 64, default=48, space="sell", optimize=True)
    time_decay_low = IntParameter(8, 24, default=16, space="sell", optimize=True)

    # --- Futures settings ---
    leverage_default = 3.0

    # =============================================
    # Trade Style Engine
    # =============================================

    def _get_trade_style(self, confidence: float) -> dict:
        """Return trade parameters based on confidence level.

        High confidence (>= threshold): Intraday Swing — wider TP, longer hold, pyramid OK
        Low confidence (< threshold): Scalping — tight TP, quick exit, no pyramid
        """
        threshold = self.confidence_style_threshold.value

        if confidence >= threshold:
            return {
                "mode": "intraday_swing",
                "atr_tp_mult": self.atr_tp_mult_high.value,
                "allow_pyramid": True,
                "time_decay_candles": self.time_decay_high.value,
                "grade_b_allowed": True,
            }
        else:
            return {
                "mode": "scalping",
                "atr_tp_mult": self.atr_tp_mult_low.value,
                "allow_pyramid": False,
                "time_decay_candles": self.time_decay_low.value,
                "grade_b_allowed": False,
            }

    # =============================================
    # Signal Audit
    # =============================================

    def _audit_signals(self, dataframe: DataFrame, metadata: dict) -> None:
        """信號審計 — 追蹤指標變化，偵測數據停滯。"""
        if len(dataframe) == 0:
            return
        pair = metadata["pair"]
        last = dataframe.iloc[-1]

        audit_keys = {
            "htf_trend": last.get("htf_trend", 0),
            "confidence": round(float(last.get("confidence", 0)), 4),
            "conf_regime": str(last.get("conf_regime", "?")),
            "in_killzone": bool(last.get("in_killzone", False)),
            "atr_pct": round(float(last.get("atr_pct", 0)), 4),
            "in_bullish_ob": bool(last.get("in_bullish_ob", False)),
            "in_bearish_ob": bool(last.get("in_bearish_ob", False)),
            "in_bullish_fvg": bool(last.get("in_bullish_fvg", False)),
            "in_bearish_fvg": bool(last.get("in_bearish_fvg", False)),
        }

        old = self._signal_audit.get(pair, {})
        changes = {}
        for k, v in audit_keys.items():
            if k in old and old[k] != v:
                changes[k] = (old[k], v)

        if changes:
            self._stale_counter = 0
            for k, (old_v, new_v) in changes.items():
                logger.info("SIGNAL_CHANGE: %s %s %s -> %s", pair, k, old_v, new_v)
            if _STATE_AVAILABLE:
                from datetime import timezone
                BotStateStore.update(last_signal_change_time=datetime.now(timezone.utc).isoformat())
        else:
            if old:
                self._stale_counter += 1
                # 15m timeframe: 40 cycles = 10 hours
                if self._stale_counter >= 40:
                    logger.warning("STALE_DATA: %s 指標已 %d 個週期未變化", pair, self._stale_counter)
                    if self._stale_counter == 40 and _TG_AVAILABLE:
                        from market_monitor.telegram_zh import send_message
                        send_message(f"⚠️ *數據停滯警告*\n{pair} [15m] 指標已 {self._stale_counter * 15}分鐘 未變化")
                    if _STATE_AVAILABLE:
                        BotStateStore.increment("stale_data_alerts")

        self._signal_audit[pair] = audit_keys

    # =============================================
    # Bot lifecycle
    # =============================================

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """Fetch live macro data from Global Confidence Engine every 30 minutes."""
        if self.config.get("runmode", {}).value not in ("live", "dry_run"):
            return

        if self._live_confidence_time is not None:
            try:
                elapsed = (current_time - self._live_confidence_time).total_seconds()
            except TypeError:
                elapsed = 0
            if elapsed < 1800:  # 30 min throttle (faster than SMCTrend's 60 min)
                return

        try:
            import os
            os.environ.setdefault("FRED_API_KEY", os.environ.get("FRED_API_KEY", ""))
            from market_monitor.confidence_engine import GlobalConfidenceEngine
            engine = GlobalConfidenceEngine()
            result = engine.calculate()
            self._live_confidence = result["score"]
            self._live_confidence_time = datetime.now()
            self._confidence_fetch_failures = 0

            if _STATE_AVAILABLE:
                BotStateStore.update(
                    last_confidence_fetch=datetime.now().isoformat(),
                    confidence_score=result["score"],
                    confidence_regime=result["regime"],
                )

            if _TG_AVAILABLE:
                old_regime = getattr(self, "_last_regime", None)
                new_regime = result["regime"]
                if old_regime and old_regime != new_regime:
                    notify_confidence_change(
                        old_regime, new_regime, result["score"],
                        f"宏觀: {result['sandboxes']['macro']:.2f} | "
                        f"情緒: {result['sandboxes']['sentiment']:.2f}"
                    )
                self._last_regime = new_regime

            logger.info(
                "[Scalp] Confidence: %.2f (%s) | Macro=%.2f Sentiment=%.2f",
                result["score"], result["regime"],
                result["sandboxes"]["macro"], result["sandboxes"]["sentiment"]
            )
        except Exception as e:
            self._confidence_fetch_failures += 1
            logger.warning("[Scalp] Confidence Engine fetch failed (%d): %s",
                           self._confidence_fetch_failures, e)
            if _STATE_AVAILABLE:
                BotStateStore.increment("confidence_fetch_failures")

        # === Crypto Environment Engine (observation mode) ===
        try:
            import os
            from market_monitor.crypto_environment import CryptoEnvironmentEngine
            cg_key = os.environ.get("COINGLASS_API_KEY")
            crypto_engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
            for sym in ["BTC", "ETH", "SOL"]:
                cr = crypto_engine.calculate(sym)
                self._crypto_env_cache[sym] = cr
                logger.info(
                    "[Scalp] Crypto Env %s: %.2f (%s) | Deriv=%.2f Chain=%.2f Sent=%.2f",
                    sym, cr["score"], cr["regime"],
                    cr["sandboxes"]["derivatives"],
                    cr["sandboxes"]["onchain"],
                    cr["sandboxes"]["sentiment"],
                )
        except Exception as e:
            logger.warning("[Scalp] Crypto Environment fetch failed: %s", e)

    def informative_pairs(self):
        """Pull 1H data for HTF trend bias."""
        pairs = self.dp.current_whitelist()
        return [(pair, "1h") for pair in pairs]

    # =============================================
    # Indicators
    # =============================================

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate all SMC indicators on 15m + 1H HTF."""
        pair = metadata["pair"]

        # =============================================
        # 15m (LTF) SMC indicators
        # =============================================
        sl = self.swing_length.value

        swing_hl = smc.swing_highs_lows(dataframe, swing_length=sl)
        dataframe["swing_hl"] = swing_hl["HighLow"]
        dataframe["swing_level"] = swing_hl["Level"]

        bos_choch = smc.bos_choch(dataframe, swing_hl, close_break=True)
        dataframe["bos"] = bos_choch["BOS"]
        dataframe["choch"] = bos_choch["CHOCH"]
        dataframe["bos_level"] = bos_choch["Level"]

        ob = smc.ob(dataframe, swing_hl, close_mitigation=True)
        dataframe["ob"] = ob["OB"]
        dataframe["ob_top"] = ob["Top"]
        dataframe["ob_bottom"] = ob["Bottom"]
        dataframe["ob_volume"] = ob.get("OBVolume", np.nan)
        dataframe["ob_pct"] = ob.get("Percentage", np.nan)
        dataframe["ob_mitigated"] = ob.get("MitigatedIndex", np.nan)

        fvg = smc.fvg(dataframe)
        dataframe["fvg"] = fvg["FVG"]
        dataframe["fvg_top"] = fvg["Top"]
        dataframe["fvg_bottom"] = fvg["Bottom"]
        dataframe["fvg_mitigated"] = fvg.get("MitigatedIndex", np.nan)

        liq = smc.liquidity(dataframe, swing_hl)
        dataframe["liquidity"] = liq["Liquidity"]
        dataframe["liq_level"] = liq["Level"]
        dataframe["liq_swept"] = liq.get("Swept", np.nan)

        # =============================================
        # 1H (HTF) trend bias
        # =============================================
        htf_df = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")
        if len(htf_df) > 0:
            htf_sl = self.htf_swing_length.value
            htf_swing = smc.swing_highs_lows(htf_df, swing_length=htf_sl)
            htf_bos = smc.bos_choch(htf_df, htf_swing, close_break=True)

            htf_df["htf_bos"] = htf_bos["BOS"]
            htf_df["htf_choch"] = htf_bos["CHOCH"]

            htf_ob = smc.ob(htf_df, htf_swing, close_mitigation=True)
            htf_df["htf_ob"] = htf_ob["OB"]
            htf_df["htf_ob_top"] = htf_ob["Top"]
            htf_df["htf_ob_bottom"] = htf_ob["Bottom"]

            htf_fvg = smc.fvg(htf_df)
            htf_df["htf_fvg"] = htf_fvg["FVG"]
            htf_df["htf_fvg_top"] = htf_fvg["Top"]
            htf_df["htf_fvg_bottom"] = htf_fvg["Bottom"]

            # Forward-fill HTF zones
            htf_df["htf_ob_top"] = htf_df["htf_ob_top"].ffill()
            htf_df["htf_ob_bottom"] = htf_df["htf_ob_bottom"].ffill()
            htf_df["htf_ob"] = htf_df["htf_ob"].ffill()
            htf_df["htf_fvg_top"] = htf_df["htf_fvg_top"].ffill()
            htf_df["htf_fvg_bottom"] = htf_df["htf_fvg_bottom"].ffill()

            merge_cols = [
                "date", "htf_bos", "htf_choch",
                "htf_ob_top", "htf_ob_bottom", "htf_ob",
                "htf_fvg_top", "htf_fvg_bottom",
            ]
            htf_merge = htf_df[merge_cols].copy()
            htf_merge["date"] = pd.to_datetime(htf_merge["date"])
            dataframe["date"] = pd.to_datetime(dataframe["date"])

            dataframe = pd.merge_asof(
                dataframe.sort_values("date"),
                htf_merge.sort_values("date"),
                on="date",
                direction="backward",
            )

            dataframe["htf_trend"] = _compute_trend(dataframe, "htf_bos", "htf_choch")

            dataframe["in_htf_ob_zone"] = (
                (dataframe["close"] >= dataframe["htf_ob_bottom"])
                & (dataframe["close"] <= dataframe["htf_ob_top"])
            ).fillna(False)

            dataframe["in_htf_fvg_zone"] = (
                (dataframe["close"] >= dataframe["htf_fvg_bottom"])
                & (dataframe["close"] <= dataframe["htf_fvg_top"])
            ).fillna(False)

            dataframe["htf_zone_aligned"] = (
                dataframe["in_htf_ob_zone"] | dataframe["in_htf_fvg_zone"]
            )
        else:
            dataframe["htf_trend"] = 0
            dataframe["htf_bos"] = np.nan
            dataframe["htf_choch"] = np.nan
            dataframe["htf_zone_aligned"] = True

        # =============================================
        # Premium / Discount zones (shorter window for 15m)
        # =============================================
        dataframe = _add_premium_discount(dataframe, window=self.pd_window.value)

        # =============================================
        # ATR for dynamic risk
        # =============================================
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period.value)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"] * 100

        sl_mult = self.atr_sl_mult.value
        dataframe["atr_sl_dist"] = dataframe["atr"] * sl_mult

        # =============================================
        # Adam Theory projection
        # =============================================
        dataframe = adam_projection(dataframe, lookback=self.adam_lookback.value)

        # =============================================
        # Killzone + Activity Multiplier (same as SMCTrend)
        # =============================================
        dataframe["utc_hour"] = dataframe["date"].dt.hour
        activity_map = {
            0: 0.3,  1: 0.2,  2: 0.15, 3: 0.10,
            4: 0.10, 5: 0.15, 6: 0.3,  7: 0.7,
            8: 0.9,  9: 1.0, 10: 0.9,
            11: 0.7, 12: 0.9, 13: 1.2, 14: 1.5,
            15: 1.3, 16: 1.0, 17: 0.8,
            18: 0.6, 19: 0.5, 20: 0.7, 21: 0.8,
            22: 0.5, 23: 0.4,
        }
        dataframe["activity_mult"] = dataframe["utc_hour"].map(activity_map).fillna(0.3)
        dataframe["in_killzone"] = dataframe["activity_mult"] >= 0.7

        # =============================================
        # Active OB/FVG zone detection (shorter lifetimes)
        # =============================================
        dataframe = _detect_active_zones(
            dataframe,
            ob_lifetime=self.ob_lifetime.value,
            fvg_lifetime=self.fvg_lifetime.value,
        )

        # =============================================
        # Funding Rate filter (tighter for scalping: 0.03% vs 0.05%)
        # =============================================
        if "funding_rate" in dataframe.columns:
            fr = dataframe["funding_rate"].fillna(0)
            dataframe["fr_ok_long"] = fr < 0.0003
            dataframe["fr_ok_short"] = fr > -0.0003
        else:
            dataframe["fr_ok_long"] = True
            dataframe["fr_ok_short"] = True

        # =============================================
        # ATR volatility regime
        # =============================================
        atr_ma50 = dataframe["atr"].rolling(50).mean()
        dataframe["vol_regime_ok"] = (
            (dataframe["atr"] > atr_ma50 * 0.5)
            & (dataframe["atr"] < atr_ma50 * 3.0)
        )

        # =============================================
        # OTE zones
        # =============================================
        dataframe["in_ote_long"] = (
            (dataframe["close"] >= dataframe["ote_bottom"])
            & (dataframe["close"] <= dataframe["ote_top"])
            & (dataframe["in_discount"])
        )
        dataframe["in_ote_short"] = (
            (dataframe["close"] >= dataframe["range_high"]
             - (dataframe["range_high"] - dataframe["range_low"]) * 0.382)
            & (dataframe["in_premium"])
        )

        # =============================================
        # Recent liquidity sweep
        # =============================================
        dataframe["recent_liq_sweep"] = False
        if "liq_swept" in dataframe.columns:
            for lookback in range(1, 6):
                swept = dataframe["liq_swept"].shift(lookback)
                dataframe["recent_liq_sweep"] = dataframe["recent_liq_sweep"] | (swept == 1)

        # =============================================
        # Confidence Engine (15m-tuned)
        # =============================================
        dataframe = _calculate_confidence_scalp(dataframe)

        self._audit_signals(dataframe, metadata)

        return dataframe

    # =============================================
    # Entry / Exit
    # =============================================

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Entry conditions — dynamic Grade A/B filtering by confidence."""

        killzone_filter = (
            (dataframe["in_killzone"]) | (self.use_killzone.value == 0)
        )

        adam_long_filter = (
            (dataframe["adam_bullish"]) | (self.use_adam_filter.value == 0)
        )
        adam_short_filter = (
            (~dataframe["adam_bullish"]) | (self.use_adam_filter.value == 0)
        )

        confidence_ok = dataframe["confidence"] > 0.2
        threshold = self.confidence_style_threshold.value

        # Grade A: OB+FVG confluence (always allowed)
        zone_long_a = dataframe["ob_fvg_confluence_bull"]
        zone_long_b = (
            (dataframe["in_bullish_ob"] | dataframe["in_bullish_fvg"])
            & (dataframe["confidence"] >= threshold)  # Only in swing mode
        )

        htf_zone = dataframe.get("htf_zone_aligned", True)

        # ===== LONG =====
        dataframe.loc[
            (
                (dataframe["htf_trend"] > 0)
                & (dataframe["in_ote_long"])
                & (zone_long_a | (zone_long_b & htf_zone))
                & adam_long_filter
                & (dataframe["fr_ok_long"])
                & (dataframe["vol_regime_ok"])
                & confidence_ok
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== SHORT =====
        zone_short_a = dataframe["ob_fvg_confluence_bear"]
        zone_short_b = (
            (dataframe["in_bearish_ob"] | dataframe["in_bearish_fvg"])
            & (dataframe["confidence"] >= threshold)
        )

        dataframe.loc[
            (
                (dataframe["htf_trend"] < 0)
                & (dataframe["in_ote_short"])
                & (zone_short_a | (zone_short_b & htf_zone))
                & adam_short_filter
                & (dataframe["fr_ok_short"])
                & (dataframe["vol_regime_ok"])
                & confidence_ok
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        # ===== REVERSE CONFIDENCE SHORT =====
        # 當信心極低 (HIBERNATE < 0.20) 時，反向做空：
        # 市場結構崩壞，順勢做空獲利
        reverse_conf_short = (
            (dataframe["confidence"] < 0.20)
            & (dataframe["htf_trend"] < 0)
            & (dataframe["fr_ok_short"])
            & (dataframe["vol_regime_ok"])
            & killzone_filter
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[
            reverse_conf_short & (dataframe["enter_short"] != 1),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit on 15m CHoCH (structure reversal)."""
        dataframe.loc[(dataframe["choch"] == -1), "exit_long"] = 1
        dataframe.loc[(dataframe["choch"] == 1), "exit_short"] = 1
        return dataframe

    # =============================================
    # Custom Exit — Time Decay + Dynamic TP + Confidence Drop
    # =============================================

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> str | bool:
        """Dynamic exit based on trade style.

        1. ATR Take Profit — TP multiplier depends on confidence style
        2. Time Decay — exit with small profit if holding too long
        3. Confidence Drop — protect profits if confidence crashes
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return False

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        style = self._get_trade_style(confidence)

        # 1. ATR dynamic take profit
        atr = last.get("atr", 0)
        if atr > 0:
            tp_dist = atr * style["atr_tp_mult"]
            tp_pct = tp_dist / current_rate
            if current_profit >= tp_pct:
                mode_zh = "波段" if style["mode"] == "intraday_swing" else "短線"
                return f"動態止盈_{mode_zh}"

        # 2. Time decay
        open_seconds = (current_time - trade.open_date_utc).total_seconds()
        open_candles = open_seconds / 900  # 15m candles
        decay_limit = style["time_decay_candles"]

        if open_candles > decay_limit:
            if current_profit > 0.002:  # > 0.2%
                return "時間衰退_止盈"
            if open_candles > decay_limit * 1.5:
                return "時間衰退_強制"

        # 3. Confidence crash protection
        if confidence < 0.3 and current_profit > 0.003:
            return "信心驟降_保利"

        return False

    # =============================================
    # Trade confirmation & notifications
    # =============================================

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        """進場確認 — 極端行情熔斷 + Guard Pipeline + Telegram."""
        # === Extreme Market Circuit Breaker ===
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        is_reverse_short = False
        if len(dataframe) > 0:
            last = dataframe.iloc[-1]
            confidence = last.get("confidence", 0.5)
            is_reverse_short = (side == "short" and confidence < 0.20)

        if len(dataframe) > 96:  # 96 * 15m = 24h
            last = dataframe.iloc[-1]
            # 1. 24h crash > -10% → full stop (except reverse shorts)
            price_24h = dataframe["close"].pct_change(96).iloc[-1]
            if abs(price_24h) > 0.10 and not is_reverse_short:
                logger.warning("[Scalp] CIRCUIT BREAKER: 24h move %.1f%% — blocking entry", price_24h * 100)
                if _TG_AVAILABLE:
                    from market_monitor.telegram_zh import send_message
                    send_message(f"🚨 *熔斷機制啟動*\n24h 變動 {price_24h*100:.1f}%\n所有進場已暫停")
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_blocks")
                return False

            # 2. ATR spike > 3x average → extreme volatility
            atr = last.get("atr", 0)
            atr_ma = dataframe["atr"].rolling(50).mean().iloc[-1] if len(dataframe) > 50 else atr
            if atr > 0 and atr_ma > 0 and atr / atr_ma > 3.0 and not is_reverse_short:
                logger.warning("[Scalp] CIRCUIT BREAKER: ATR spike %.1fx — blocking entry", atr / atr_ma)
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_blocks")
                return False

            # 3. Confidence HIBERNATE → block longs only (allow reverse shorts)
            confidence = last.get("confidence", 0.5)
            if confidence < 0.15 and side == "long":
                logger.warning("[Scalp] CIRCUIT BREAKER: Confidence %.2f (HIBERNATE) — blocking long", confidence)
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_blocks")
                return False

        # === Guard Pipeline check (live/dry_run only) ===
        if self.config.get("runmode", {}).value in ("live", "dry_run"):
            try:
                import asyncio
                from guards.base import GuardContext
                from guards.pipeline import create_default_pipeline

                ctx = GuardContext(
                    symbol=pair,
                    side="short" if side == "short" else "long",
                    amount=amount * rate,
                    leverage=self.leverage_default,
                    account_balance=self.wallets.get_total("USDT") if self.wallets else 1000,
                )
                pipeline = create_default_pipeline()
                rejection = asyncio.get_event_loop().run_until_complete(pipeline.run(ctx))
                if rejection:
                    logger.warning("[Scalp] Guard rejected %s %s: %s", pair, side, rejection)
                    if _TG_AVAILABLE:
                        from market_monitor.telegram_zh import send_message
                        send_message(f"🛡️ *Guard 攔截*\n{pair} {side}\n原因: {rejection}")
                    if _STATE_AVAILABLE:
                        BotStateStore.increment("guard_rejections")
                    return False
            except Exception as e:
                logger.warning("[Scalp] Guard Pipeline error: %s", e)

        if _TG_AVAILABLE:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            confidence = 0.5
            details = {"htf_label": "1H"}  # Key difference from SMCTrend
            if len(dataframe) > 0:
                last = dataframe.iloc[-1]
                confidence = last.get("confidence", 0.5)
                style = self._get_trade_style(confidence)

                # Determine if this is a reverse confidence short
                is_reversal = (side == "short" and confidence < 0.20)

                # Crypto environment data
                crypto_env = {}
                base_symbol = pair.split("/")[0] if "/" in pair else pair[:3]
                if base_symbol in self._crypto_env_cache:
                    ce = self._crypto_env_cache[base_symbol]
                    crypto_env = {
                        "score": ce.get("score", 0),
                        "regime": ce.get("regime", "?"),
                    }

                # Funding rate
                funding_rate = float(last.get("funding_rate", 0)) if "funding_rate" in dataframe.columns else None

                # Volatility regime
                atr = last.get("atr", 0)
                atr_ma = dataframe["atr"].rolling(50).mean().iloc[-1] if len(dataframe) > 50 else atr
                vol_regime = "正常"
                if atr_ma > 0:
                    atr_ratio = atr / atr_ma
                    if atr_ratio > 2.0:
                        vol_regime = "極端"
                    elif atr_ratio > 1.5:
                        vol_regime = "擴張"
                    elif atr_ratio < 0.5:
                        vol_regime = "低迷"

                # Expected R:R
                atr_val = float(last.get("atr", 0))
                sl_dist = atr_val * self.atr_sl_mult.value
                tp_mult = style["atr_tp_mult"]
                tp_dist = atr_val * tp_mult
                expected_rr = round(tp_dist / sl_dist, 1) if sl_dist > 0 else 0

                details.update({
                    "htf_trend": last.get("htf_trend", 0),
                    "in_ob": bool(last.get("in_bullish_ob") or last.get("in_bearish_ob")),
                    "in_fvg": bool(last.get("in_bullish_fvg") or last.get("in_bearish_fvg")),
                    "confluence": bool(last.get("ob_fvg_confluence_bull") or last.get("ob_fvg_confluence_bear")),
                    "in_ote": bool(last.get("in_ote_long") or last.get("in_ote_short")),
                    "adam_bullish": last.get("adam_bullish"),
                    "adam_slope": last.get("adam_slope", 0),
                    "in_killzone": bool(last.get("in_killzone")),
                    "utc_hour": last.get("utc_hour", 0),
                    "htf_zone_aligned": bool(last.get("htf_zone_aligned")),
                    "trade_mode": style["mode"],
                    "is_reversal": is_reversal,
                    "funding_rate": funding_rate,
                    "volatility_regime": vol_regime,
                    "crypto_env": crypto_env,
                    "expected_rr": expected_rr,
                    "confidence_factors": {
                        "momentum": float(last.get("adam_slope", 0) > 0) * 0.7 + 0.3,
                        "trend": 0.7 if last.get("htf_trend", 0) != 0 else 0.3,
                        "volume": min(float(last.get("volume", 0)) / (float(dataframe["volume"].rolling(20).mean().iloc[-1]) + 1e-10) * 0.5, 1.0) if len(dataframe) > 20 else 0.5,
                        "volatility": 0.6,
                        "health": confidence,
                    },
                    "missing_sources": _get_missing_sources(),
                })

            lev = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)
            # Reverse confidence shorts: cap leverage at 80% of max
            if is_reverse_short:
                lev = min(lev, self.max_leverage.value * 0.8)
            notify_entry(
                pair=pair, side=side, rate=rate,
                stake=amount * rate, leverage=round(lev, 1),
                confidence=confidence, details=details,
            )
        return True

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime,
                           **kwargs) -> bool:
        """出場確認 — 績效追蹤 + 繁體中文 Telegram 通知."""
        profit_pct = trade.calc_profit_ratio(rate) * 100
        profit_usdt = trade.calc_profit(rate)
        duration = str(current_time - trade.open_date_utc).split(".")[0]
        side = "short" if trade.is_short else "long"

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        confidence = 0.5
        if len(dataframe) > 0:
            confidence = dataframe.iloc[-1].get("confidence", 0.5)

        # === Performance Tracking ===
        win_rate = None
        drawdown = None
        equity = None
        consecutive = 0
        try:
            closed_trades = Trade.get_trades_proxy(is_open=False)
            if closed_trades:
                wins = sum(1 for t in closed_trades if t.calc_profit_ratio(t.close_rate or t.open_rate) > 0)
                win_rate = round(wins / len(closed_trades) * 100, 1)
                equity = sum(t.calc_profit(t.close_rate or t.open_rate) for t in closed_trades)
                # Max drawdown approximation
                running = 0
                peak = 0
                max_dd = 0
                for t in closed_trades:
                    running += t.calc_profit(t.close_rate or t.open_rate)
                    if running > peak:
                        peak = running
                    dd = peak - running
                    if dd > max_dd:
                        max_dd = dd
                drawdown = round(max_dd, 2)
                # Consecutive wins/losses
                for t in reversed(closed_trades):
                    p = t.calc_profit_ratio(t.close_rate or t.open_rate)
                    if profit_pct > 0 and p > 0:
                        consecutive += 1
                    elif profit_pct <= 0 and p <= 0:
                        consecutive += 1
                    else:
                        break
        except Exception:
            pass

        reason_zh = {
            "exit_signal": "📊 結構反轉 (CHoCH)",
            "stop_loss": "🛑 觸發止損",
            "trailing_stop_loss": "📈 追蹤止損",
            "force_exit": "⚡ 強制出場",
            "動態止盈_波段": "🎯 動態止盈（波段模式）",
            "動態止盈_短線": "🎯 動態止盈（短線模式）",
            "時間衰退_止盈": "⏰ 時間衰退止盈",
            "時間衰退_強制": "⏰ 時間衰退強制出場",
            "信心驟降_保利": "⚠️ 信心驟降保利",
        }.get(exit_reason, exit_reason)

        if _TG_AVAILABLE:
            if "stop_loss" in exit_reason:
                notify_stoploss(pair, side, profit_pct, profit_usdt)
            else:
                notify_exit(
                    pair=pair, side=side, profit_pct=profit_pct,
                    profit_usdt=profit_usdt, exit_reason=reason_zh,
                    duration=duration, confidence=confidence,
                    win_rate=win_rate, drawdown=drawdown,
                    equity=equity, consecutive=consecutive,
                )

        # Audit log
        logger.info(
            "TRADE_AUDIT: %s %s %s | Entry:%.2f Exit:%.2f | P&L:%.2f%% ($%.2f) | "
            "Duration:%s | Reason:%s | Confidence:%.2f | WR:%s DD:%s",
            pair, side, exit_reason, trade.open_rate, rate,
            profit_pct, profit_usdt, duration, exit_reason, confidence,
            f"{win_rate}%" if win_rate is not None else "N/A",
            f"${drawdown}" if drawdown is not None else "N/A",
        )
        return True

    # =============================================
    # Leverage & Position Sizing
    # =============================================

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Confidence-squared leverage scaling + reverse confidence for shorts."""
        if self._live_confidence is not None:
            confidence = self._live_confidence
        else:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(dataframe) == 0:
                return 1.0
            confidence = dataframe.iloc[-1].get("confidence", 0.5)

        max_lev = self.max_leverage.value

        # Reverse confidence short: low confidence = moderate leverage (capped at 80%)
        if side == "short" and confidence < 0.20:
            lev = 1.0 + (max_lev * 0.8 - 1.0) * 0.5  # Fixed moderate leverage
            return min(max(lev, 1.0), max_leverage)

        lev = 1.0 + (max_lev - 1.0) * (confidence ** 2)
        return min(max(lev, 1.0), max_leverage)

    def custom_stake_amount(self, current_time, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str,
                            **kwargs) -> float:
        """Position sizing by confidence × activity. Low-confidence mode gets 20% reduction.
        Reverse confidence shorts in HIBERNATE get conservative sizing."""
        pair = kwargs.get("pair", "")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        activity = last.get("activity_mult", 0.5)
        style = self._get_trade_style(confidence)

        # Reverse confidence short: conservative sizing (50% of proposed)
        if side == "short" and confidence < 0.20:
            adjusted = proposed_stake * 0.5
            if min_stake is not None:
                adjusted = max(adjusted, min_stake)
            return min(adjusted, max_stake)

        scale = 0.3 + 0.9 * confidence

        if activity >= 1.0:
            scale *= 1.1
        elif activity < 0.3:
            scale *= 0.8

        # Scalping mode: extra 20% reduction (more trades, smaller size)
        if style["mode"] == "scalping":
            scale *= 0.8

        adjusted = proposed_stake * scale
        if min_stake is not None:
            adjusted = max(adjusted, min_stake)
        return min(adjusted, max_stake)

    # =============================================
    # Pyramid (confidence-gated)
    # =============================================

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> float | None:
        """Pyramid add-on — only in swing mode, lower profit threshold than SMCTrend."""
        # Block pyramid for reverse confidence shorts
        if trade.is_short and self._live_confidence is not None and self._live_confidence < 0.20:
            return None

        # Need at least 2% profit (vs SMCTrend's 5%)
        if current_profit < 0.02:
            return None

        filled_entries = trade.nr_of_successful_entries
        if filled_entries >= 2:
            return None  # Max 1 add-on (2 total)

        pair = trade.pair
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        htf_trend = last.get("htf_trend", 0)
        style = self._get_trade_style(confidence)

        # Only pyramid in swing mode
        if not style["allow_pyramid"]:
            return None

        if confidence < 0.7:
            return None

        if trade.is_short and htf_trend > 0:
            return None
        if not trade.is_short and htf_trend < 0:
            return None

        # Single add-on: 50% of original, scaled by confidence
        addon_ratio = 0.5 * confidence

        try:
            stake = trade.stake_amount * addon_ratio
        except Exception:
            return None

        if min_stake is not None and stake < min_stake:
            return None
        if stake > max_stake:
            stake = max_stake

        logger.info(
            "[Scalp] Pyramid #%d for %s: +%.2f USDT (profit=%.1f%%, conf=%.2f, mode=%s)",
            filled_entries, pair, stake, current_profit * 100, confidence, style["mode"]
        )

        if _TG_AVAILABLE:
            notify_pyramid(pair, filled_entries, stake, current_profit * 100, confidence)

        return stake


# =============================================
# Helper functions
# =============================================

def _get_missing_sources() -> list[str]:
    """Check which data sources are unavailable."""
    missing = []
    import os
    if not os.environ.get("FRED_API_KEY"):
        missing.append("FRED NFCI/M2 (API Key 未設定)")
    try:
        import yfinance as yf
        df = yf.Ticker("DX-Y.NYB").history(period="1d")
        if df.empty:
            missing.append("DXY 美元指數 (yfinance 無法取得)")
    except Exception:
        missing.append("DXY 美元指數 (yfinance 錯誤)")
    return missing


def _compute_trend(df: DataFrame, bos_col: str, choch_col: str) -> pd.Series:
    """Compute running trend direction from BOS/CHoCH signals."""
    trend = pd.Series(0, index=df.index, dtype=int)
    current = 0
    for i in range(len(df)):
        bos_val = df[bos_col].iloc[i]
        choch_val = df[choch_col].iloc[i]
        if not pd.isna(bos_val):
            current = int(bos_val)
        elif not pd.isna(choch_val):
            current = int(choch_val)
        trend.iloc[i] = current
    return trend


def _add_premium_discount(df: DataFrame, window: int = 24) -> DataFrame:
    """Add premium/discount zone based on recent swing range."""
    df["range_high"] = df["high"].rolling(window).max()
    df["range_low"] = df["low"].rolling(window).min()
    df["equilibrium"] = (df["range_high"] + df["range_low"]) / 2

    df["in_premium"] = df["close"] > df["equilibrium"]
    df["in_discount"] = df["close"] < df["equilibrium"]

    range_size = df["range_high"] - df["range_low"]
    df["ote_top"] = df["range_high"] - (range_size * 0.618)
    df["ote_bottom"] = df["range_high"] - (range_size * 0.79)

    return df


def _detect_active_zones(df: DataFrame, ob_lifetime: int = 16,
                         fvg_lifetime: int = 12) -> DataFrame:
    """Detect active OB/FVG zones with configurable lifetimes."""
    df["in_bullish_ob"] = False
    df["in_bearish_ob"] = False
    df["in_bullish_fvg"] = False
    df["in_bearish_fvg"] = False
    df["ob_fvg_confluence_bull"] = False
    df["ob_fvg_confluence_bear"] = False

    # Track active order blocks
    active_obs = []
    for i in range(len(df)):
        ob_val = df["ob"].iloc[i]
        close = df["close"].iloc[i]

        if not pd.isna(ob_val) and ob_val != 0:
            top = df["ob_top"].iloc[i]
            bottom = df["ob_bottom"].iloc[i]
            if not pd.isna(top) and not pd.isna(bottom):
                active_obs.append({
                    "type": int(ob_val),
                    "top": top,
                    "bottom": bottom,
                    "created": i,
                })

        remaining = []
        for ob_zone in active_obs:
            if ob_zone["type"] == 1 and close < ob_zone["bottom"]:
                continue
            if ob_zone["type"] == -1 and close > ob_zone["top"]:
                continue
            if i - ob_zone["created"] > ob_lifetime:
                continue
            remaining.append(ob_zone)
            if ob_zone["bottom"] <= close <= ob_zone["top"]:
                if ob_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_ob"] = True
                else:
                    df.at[df.index[i], "in_bearish_ob"] = True
        active_obs = remaining

    # Track active FVGs
    active_fvgs = []
    for i in range(len(df)):
        fvg_val = df["fvg"].iloc[i]
        close = df["close"].iloc[i]

        if not pd.isna(fvg_val) and fvg_val != 0:
            top = df["fvg_top"].iloc[i]
            bottom = df["fvg_bottom"].iloc[i]
            if not pd.isna(top) and not pd.isna(bottom):
                active_fvgs.append({
                    "type": int(fvg_val),
                    "top": top,
                    "bottom": bottom,
                    "created": i,
                })

        remaining = []
        for fvg_zone in active_fvgs:
            if i - fvg_zone["created"] > fvg_lifetime:
                continue
            if fvg_zone["type"] == 1 and close < fvg_zone["bottom"]:
                continue
            if fvg_zone["type"] == -1 and close > fvg_zone["top"]:
                continue
            remaining.append(fvg_zone)
            if fvg_zone["bottom"] <= close <= fvg_zone["top"]:
                if fvg_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_fvg"] = True
                else:
                    df.at[df.index[i], "in_bearish_fvg"] = True
        active_fvgs = remaining

    df["ob_fvg_confluence_bull"] = df["in_bullish_ob"] & df["in_bullish_fvg"]
    df["ob_fvg_confluence_bear"] = df["in_bearish_ob"] & df["in_bearish_fvg"]

    return df


def _calculate_confidence_scalp(df: DataFrame) -> DataFrame:
    """Calculate confidence score tuned for 15m timeframe.

    Same 6-factor architecture as SMCTrend but with adjusted periods:
    - ROC: 4/16/48 candles (1hr/4hr/12hr instead of 6hr/24hr/72hr)
    - EMA smooth: span=12 (3hr instead of 5hr)
    """
    n = len(df)
    close_s = pd.Series(df["close"].values, index=df.index)
    atr_s = pd.Series(df.get("atr", pd.Series(np.zeros(n))).values, index=df.index)
    volume_s = pd.Series(df["volume"].values, index=df.index)
    htf_trend = df.get("htf_trend", pd.Series(np.zeros(n), index=df.index)).values

    # 1. MOMENTUM (25%) — adjusted ROC periods for 15m
    roc_4 = close_s.pct_change(4)     # 1hr momentum
    roc_16 = close_s.pct_change(16)   # 4hr momentum
    roc_48 = close_s.pct_change(48)   # 12hr momentum

    mom_4 = np.clip(0.5 + roc_4.fillna(0) * 10, 0.05, 0.95)
    mom_16 = np.clip(0.5 + roc_16.fillna(0) * 6, 0.05, 0.95)
    mom_48 = np.clip(0.5 + roc_48.fillna(0) * 4, 0.05, 0.95)

    all_positive = (roc_4 > 0) & (roc_16 > 0) & (roc_48 > 0)
    all_negative = (roc_4 < 0) & (roc_16 < 0) & (roc_48 < 0)
    alignment_bonus = np.where(all_positive | all_negative, 0.15, 0.0)

    momentum_score = np.clip(mom_4 * 0.4 + mom_16 * 0.35 + mom_48 * 0.25 + alignment_bonus, 0, 1)

    # 2. TREND ALIGNMENT (25%)
    trend_present = np.where(htf_trend != 0, 0.7, 0.3)
    trend_momentum_agree = np.where(
        ((htf_trend > 0) & (roc_16.fillna(0) > 0)) |
        ((htf_trend < 0) & (roc_16.fillna(0) < 0)),
        0.3, 0.0
    )
    trend_score = np.clip(trend_present + trend_momentum_agree, 0, 1)

    # 3. VOLUME CONVICTION (12%)
    vol_ma20 = volume_s.rolling(20, min_periods=5).mean()
    vol_ratio = (volume_s / (vol_ma20 + 1e-10)).fillna(1)
    vol_score = np.clip(vol_ratio * 0.5, 0.1, 0.95)
    vol_trend_agree = np.where(
        (vol_ratio > 1.2) & (htf_trend != 0),
        0.15, 0.0
    )
    volume_score = np.clip(vol_score + vol_trend_agree, 0, 1)

    # 4. VOLATILITY QUALITY (13%)
    if n > 50:
        atr_ma50 = atr_s.rolling(50, min_periods=10).mean()
        atr_ratio = (atr_s / (atr_ma50 + 1e-10)).fillna(1)
        trending = htf_trend != 0
        vol_expanding = atr_ratio > 1.2
        vol_contracting = atr_ratio < 0.7

        vol_quality = np.where(
            trending & vol_expanding, 0.85,
            np.where(
                vol_contracting, 0.65,
                np.where(
                    ~trending & vol_expanding, 0.25,
                    0.55
                )
            )
        )
    else:
        vol_quality = np.full(n, 0.5)
    volatility_score = np.clip(vol_quality, 0, 1)

    # 5. MARKET HEALTH (13%)
    ema50 = close_s.ewm(span=50, min_periods=20).mean()
    ema200 = close_s.ewm(span=200, min_periods=50).mean()
    bull_structure = (close_s > ema50) & (ema50 > ema200)
    bear_structure = (close_s < ema50) & (ema50 < ema200)
    price_above_ema50 = close_s > ema50

    health_score = np.where(
        bull_structure, 0.85,
        np.where(
            bear_structure, 0.15,
            np.where(price_above_ema50, 0.65, 0.35)
        )
    )

    if "funding_rate" in df.columns:
        fr = df["funding_rate"].fillna(0).values
        fr_penalty = np.where(np.abs(fr) > 0.0003, -0.15, 0.0)
        health_score = np.clip(health_score + fr_penalty, 0, 1)

    # 6. ACTIVITY REGIME (12%)
    if "activity_mult" in df.columns:
        activity_score = np.clip(df["activity_mult"].values / 1.5, 0.05, 1.0)
    else:
        activity_score = np.full(n, 0.5)

    # COMBINE
    raw_confidence = (
        0.25 * momentum_score
        + 0.25 * trend_score
        + 0.12 * volume_score
        + 0.13 * volatility_score
        + 0.13 * health_score
        + 0.12 * activity_score
    )

    # EMA smooth: span=12 (12 * 15m = 3hr, responsive but not noisy)
    conf_series = pd.Series(np.asarray(raw_confidence).flatten()).ewm(span=12, min_periods=1).mean()
    df["confidence"] = np.clip(conf_series.values, 0.0, 1.0)

    df["conf_regime"] = pd.cut(
        df["confidence"],
        bins=[0, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=["HIBERNATE", "DEFENSIVE", "CAUTIOUS", "NORMAL", "AGGRESSIVE"],
    )

    return df
