"""SMC + Trend + ATR Strategy for crypto futures.

Combines Smart Money Concepts (ICT methodology) with trend following and ATR risk:
- 4H: BOS/CHoCH determines trend direction
- 1H: Order Block + FVG for entry zones
- ATR: Dynamic stop loss & take profit (volatility-adjusted)
- 1R Breakeven: When profit reaches 1× risk, move stop to entry price
- Killzone time filter for high-volume sessions
- Premium/Discount zone filter

Designed for USDT perpetual futures on OKX via Freqtrade.

References:
- smartmoneyconcepts package: https://github.com/joshyattridge/smart-money-concepts
- ICT methodology: Order Blocks, Fair Value Gaps, Break of Structure
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

# Add paths for imports (guards, indicators, market_monitor, agent)
_strategy_dir = str(Path(__file__).resolve().parent)
_proj_root = str(Path(__file__).resolve().parent.parent)
for _p in [_strategy_dir, _proj_root]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from indicators.adam_projection import adam_projection

try:
    from market_monitor.telegram_zh import (
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


class SMCTrend(IStrategy):
    """Smart Money Concepts + Trend Following strategy."""

    INTERFACE_VERSION = 3

    # --- Strategy settings ---
    timeframe = "15m"
    can_short = True
    stoploss = -0.05  # 5% hard stop (absolute maximum, last resort)
    use_custom_stoploss = True

    # --- Protections ---
    # Note: candle counts adjusted for 15m (4x more candles per hour than 1h)
    @property
    def protections(self):
        return [
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 192,  # 48 hours × 4 candles/h
                "trade_limit": 10,
                "stop_duration_candles": 48,      # 12 hours × 4
                "max_allowed_drawdown": 0.15,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 96,   # 24 hours × 4
                "trade_limit": 4,
                "stop_duration_candles": 24,      # 6 hours × 4
                "only_per_pair": False,
            },
        ]

    # --- Live macro data cache ---
    _live_confidence: float | None = None
    _live_confidence_time: datetime | None = None
    _crypto_env: dict[str, float] = {}  # symbol -> score (0.0-1.0)
    trailing_stop = False

    # --- Signal audit tracking ---
    _signal_audit: dict = {}
    _stale_counter: int = 0
    _confidence_fetch_failures: int = 0
    _crypto_env_cache: dict = {}

    # Pyramid: add to winning positions when confidence supports it
    position_adjustment_enable = True
    max_entry_position_adjustment = 2  # Up to 2 add-ons (3 total entries)

    startup_candle_count = 400  # 400 × 15m = ~4 days warmup

    # --- Hyperparameters ---
    # Defaults from WFO Segment 4 (best OOS: +49.63%, 20 trades)
    swing_length = IntParameter(5, 20, default=12, space="buy",
                                optimize=True)
    htf_swing_length = IntParameter(10, 30, default=14, space="buy",
                                    optimize=True)
    use_killzone = IntParameter(0, 1, default=1, space="buy",
                                optimize=True)
    ob_strength_min = DecimalParameter(0.1, 0.8, default=0.3, space="buy",
                                      optimize=True)

    # ATR-based risk management
    atr_period = IntParameter(10, 20, default=14, space="buy", optimize=True)
    # Seg 4 optimal: wider stop (less whipsaw), tighter TP (more achievable)
    atr_sl_mult = DecimalParameter(1.0, 3.0, default=1.87, space="buy",
                                   optimize=True)
    atr_tp_mult = DecimalParameter(2.0, 5.0, default=3.0, space="sell",
                                   optimize=True)

    # Adam Theory projection
    adam_lookback = IntParameter(10, 40, default=20, space="buy", optimize=True)
    use_adam_filter = IntParameter(0, 1, default=1, space="buy", optimize=True)

    # --- Confidence Engine settings ---
    max_leverage = DecimalParameter(1.0, 5.0, default=3.0, space="buy", optimize=True)

    # --- Futures settings ---
    leverage_default = 3.0

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """Fetch live macro data from Global Confidence Engine every hour.

        Only runs in live/dry_run mode. Throttled to 1 call per hour.
        Results cached on self._live_confidence for use in leverage/sizing.
        """
        if self.config.get("runmode", {}).value not in ("live", "dry_run"):
            return

        # Throttle: fetch every 60 minutes
        if self._live_confidence_time is not None:
            try:
                elapsed = (current_time - self._live_confidence_time).total_seconds()
            except TypeError:
                # Handle naive vs aware datetime mismatch
                elapsed = 0
            if elapsed < 3600:
                return

        try:
            import os
            os.environ.setdefault("FRED_API_KEY", os.environ.get("FRED_API_KEY", ""))
            from market_monitor.confidence_engine import GlobalConfidenceEngine
            engine = GlobalConfidenceEngine()
            result = engine.calculate()  # Use engine's own timestamp
            self._live_confidence = result["score"]
            self._live_confidence_time = datetime.now()

            # Notify on regime change
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
                "Confidence Engine: %.2f (%s) | Macro=%.2f Sentiment=%.2f",
                result["score"], result["regime"],
                result["sandboxes"]["macro"], result["sandboxes"]["sentiment"]
            )
            if _STATE_AVAILABLE:
                BotStateStore.update(
                    last_confidence_fetch=datetime.now().isoformat(),
                    last_confidence_score=result["score"],
                    last_confidence_regime=result["regime"],
                )
                self._confidence_fetch_failures = 0
        except Exception as e:
            logger.warning("Confidence Engine fetch failed: %s", e)
            self._confidence_fetch_failures += 1
            if self._confidence_fetch_failures >= 3 and _TG_AVAILABLE:
                from market_monitor.telegram_zh import send_message
                send_message(f"🚨 *信心引擎連續失敗*\n已連續 {self._confidence_fetch_failures} 次無法取得信心分數")
            # SAFETY: If confidence data is stale (>3 hours), degrade to DEFENSIVE
            if self._live_confidence_time is not None:
                try:
                    stale_hours = (datetime.now() - self._live_confidence_time).total_seconds() / 3600
                    if stale_hours > 3:
                        logger.warning("Confidence data stale %.1fh — degrading to DEFENSIVE (0.25)", stale_hours)
                        self._live_confidence = 0.25  # DEFENSIVE level
                except Exception:
                    pass

        # === Crypto Environment Engine (observation mode) ===
        try:
            from market_monitor.crypto_environment import CryptoEnvironmentEngine
            cg_key = os.environ.get("COINGLASS_API_KEY")
            crypto_engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
            # Monitor all whitelisted symbols
            monitored_syms = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
            for sym in monitored_syms:
                cr = crypto_engine.calculate(sym)
                self._crypto_env[sym] = cr["score"]
                logger.info(
                    "Crypto Env %s: %.2f (%s) | Deriv=%.2f Chain=%.2f Sent=%.2f | %s",
                    sym, cr["score"], cr["regime"],
                    cr["sandboxes"]["derivatives"],
                    cr["sandboxes"]["onchain"],
                    cr["sandboxes"]["sentiment"],
                    " | ".join(
                        f.get("signal", "")
                        for f in cr.get("factors", {}).values()
                        if f.get("signal") and f["signal"] not in ("neutral", "stable", "no data")
                    ),
                )
                self._crypto_env_cache[sym] = {"score": cr["score"], "regime": cr["regime"]}
                if _STATE_AVAILABLE:
                    BotStateStore.update_crypto_env(sym, cr["score"], cr["regime"])
        except Exception as e:
            logger.warning("Crypto Environment fetch failed: %s", e)

    def _audit_signals(self, dataframe: DataFrame, metadata: dict) -> None:
        """信號審計 — 追蹤指標變化，偵測數據停滯。"""
        if len(dataframe) == 0:
            return
        pair = metadata["pair"]
        last = dataframe.iloc[-1]

        # 需要追蹤的關鍵指標
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
            if old:  # Only count stale if we have previous data
                self._stale_counter += 1
                if self._stale_counter >= 10:
                    logger.warning("STALE_DATA: %s 指標已 %d 個週期未變化", pair, self._stale_counter)
                    if self._stale_counter == 10 and _TG_AVAILABLE:
                        from market_monitor.telegram_zh import send_message
                        send_message(f"⚠️ *數據停滯警告*\n{pair} 指標已 {self._stale_counter} 小時未變化")
                    if _STATE_AVAILABLE:
                        BotStateStore.increment("stale_data_alerts")

        self._signal_audit[pair] = audit_keys

    def informative_pairs(self):
        """Pull 4H data for HTF trend bias."""
        pairs = self.dp.current_whitelist()
        return [(pair, "4h") for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate all SMC indicators."""
        pair = metadata["pair"]

        # =============================================
        # 1H (current timeframe) SMC indicators
        # =============================================
        sl = self.swing_length.value

        # Swing highs & lows
        swing_hl = smc.swing_highs_lows(dataframe, swing_length=sl)
        dataframe["swing_hl"] = swing_hl["HighLow"]
        dataframe["swing_level"] = swing_hl["Level"]

        # BOS / CHoCH (trend structure)
        bos_choch = smc.bos_choch(dataframe, swing_hl, close_break=True)
        dataframe["bos"] = bos_choch["BOS"]
        dataframe["choch"] = bos_choch["CHOCH"]
        dataframe["bos_level"] = bos_choch["Level"]

        # Order Blocks
        ob = smc.ob(dataframe, swing_hl, close_mitigation=True)
        dataframe["ob"] = ob["OB"]
        dataframe["ob_top"] = ob["Top"]
        dataframe["ob_bottom"] = ob["Bottom"]
        dataframe["ob_volume"] = ob.get("OBVolume", np.nan)
        dataframe["ob_pct"] = ob.get("Percentage", np.nan)
        dataframe["ob_mitigated"] = ob.get("MitigatedIndex", np.nan)

        # Fair Value Gaps
        fvg = smc.fvg(dataframe)
        dataframe["fvg"] = fvg["FVG"]
        dataframe["fvg_top"] = fvg["Top"]
        dataframe["fvg_bottom"] = fvg["Bottom"]
        dataframe["fvg_mitigated"] = fvg.get("MitigatedIndex", np.nan)

        # Liquidity levels
        liq = smc.liquidity(dataframe, swing_hl)
        dataframe["liquidity"] = liq["Liquidity"]
        dataframe["liq_level"] = liq["Level"]
        dataframe["liq_swept"] = liq.get("Swept", np.nan)

        # Retracements — validates actual pullback depth from last swing
        try:
            retrace = smc.retracements(dataframe, swing_hl)
            dataframe["retrace_pct"] = retrace.get("CurrentRetracement%", 0.5)
            dataframe["retrace_dir"] = retrace.get("Direction", 0)
        except Exception:
            dataframe["retrace_pct"] = 0.5
            dataframe["retrace_dir"] = 0

        # Valid retracement zone: 38.2%-78.6% (Fibonacci sweet spot)
        dataframe["valid_retrace"] = (
            (dataframe["retrace_pct"] >= 0.382) & (dataframe["retrace_pct"] <= 0.786)
        )

        # VWAP — volume-weighted average price (institutional fair value)
        typical_price = (dataframe["high"] + dataframe["low"] + dataframe["close"]) / 3
        cum_vol = dataframe["volume"].cumsum().replace(0, np.nan)
        dataframe["vwap"] = (typical_price * dataframe["volume"]).cumsum() / cum_vol
        dataframe["above_vwap"] = dataframe["close"] > dataframe["vwap"]

        # =============================================
        # 4H (HTF) trend bias via informative pairs
        # =============================================
        htf_df = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        if len(htf_df) > 0:
            # Diagnostic: verify HTF data is actually 4h, not 15m
            if len(htf_df) > 1:
                td = (htf_df["date"].iloc[-1] - htf_df["date"].iloc[-2]).total_seconds()
                logger.info("HTF dataframe %s: %d candles, interval=%.0fs (expect 14400 for 4h)", pair, len(htf_df), td)

            htf_sl = self.htf_swing_length.value
            htf_swing = smc.swing_highs_lows(htf_df, swing_length=htf_sl)
            htf_bos = smc.bos_choch(htf_df, htf_swing, close_break=True)

            # 4H BOS/CHoCH for trend
            htf_df["htf_bos"] = htf_bos["BOS"]
            htf_df["htf_choch"] = htf_bos["CHOCH"]

            # 4H Order Blocks — detect OB zones on HTF
            htf_ob = smc.ob(htf_df, htf_swing, close_mitigation=True)
            htf_df["htf_ob"] = htf_ob["OB"]
            htf_df["htf_ob_top"] = htf_ob["Top"]
            htf_df["htf_ob_bottom"] = htf_ob["Bottom"]

            # 4H FVG — detect FVG zones on HTF
            htf_fvg = smc.fvg(htf_df)
            htf_df["htf_fvg"] = htf_fvg["FVG"]
            htf_df["htf_fvg_top"] = htf_fvg["Top"]
            htf_df["htf_fvg_bottom"] = htf_fvg["Bottom"]

            # Forward-fill latest 4H OB/FVG zones onto 1H
            # Keep the most recent OB/FVG levels active
            htf_df["htf_ob_top"] = htf_df["htf_ob_top"].ffill()
            htf_df["htf_ob_bottom"] = htf_df["htf_ob_bottom"].ffill()
            htf_df["htf_ob"] = htf_df["htf_ob"].ffill()
            htf_df["htf_fvg_top"] = htf_df["htf_fvg_top"].ffill()
            htf_df["htf_fvg_bottom"] = htf_df["htf_fvg_bottom"].ffill()

            # Forward-fill BOS/CHoCH so merge_asof picks up the latest signal
            # Without this, most 4H candles have NaN for BOS/CHoCH and
            # merge_asof drops them, resulting in htf_trend=0 permanently
            htf_df["htf_bos"] = htf_df["htf_bos"].ffill()
            htf_df["htf_choch"] = htf_df["htf_choch"].ffill()

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

            # Compute running trend from latest BOS
            dataframe["htf_trend"] = _compute_trend(dataframe, "htf_bos", "htf_choch")

            # Signal age decay: neutralize trend if last BOS/CHoCH is too old
            # Use _compute_trend's own state changes (trend flips) as fresh signal markers
            _trend_changed = (dataframe["htf_trend"] != dataframe["htf_trend"].shift()).astype(bool)
            _trend_groups = _trend_changed.cumsum()
            dataframe["htf_signal_age"] = dataframe.groupby(_trend_groups).cumcount()
            # On 15m: >48 candles (12h) old → trend goes neutral
            # 12h is generous — allows 3 full 4H candles before expiry
            dataframe.loc[dataframe["htf_signal_age"] > 48, "htf_trend"] = 0

            # Diagnostic: how many HTF signals exist?
            n_htf_bos = dataframe["htf_bos"].notna().sum()
            n_htf_choch = dataframe["htf_choch"].notna().sum()
            # Also check pre-merge signals on raw HTF dataframe
            n_raw_bos = htf_df["htf_bos"].notna().sum()
            n_raw_choch = htf_df["htf_choch"].notna().sum()
            htf_trend_last = dataframe["htf_trend"].iloc[-1] if len(dataframe) > 0 else 0
            logger.info(
                "HTF %s: raw=%d BOS %d CHoCH | merged=%d BOS %d CHoCH | trend=%d | 4h=%d 15m=%d",
                pair, n_raw_bos, n_raw_choch, n_htf_bos, n_htf_choch,
                htf_trend_last, len(htf_df), len(dataframe)
            )

            # 4H zone alignment: is 1H price within a 4H OB or FVG zone?
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
            dataframe["htf_zone_aligned"] = True  # Default: don't filter

        # =============================================
        # Premium / Discount zones
        # =============================================
        dataframe = _add_premium_discount(dataframe)

        # =============================================
        # ATR for dynamic stop loss & take profit
        # =============================================
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period.value)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"] * 100

        # ATR-based stop and target levels (stored for custom_stoploss)
        sl_mult = self.atr_sl_mult.value
        tp_mult = self.atr_tp_mult.value
        dataframe["atr_sl_dist"] = dataframe["atr"] * sl_mult  # Distance in price
        dataframe["atr_tp_dist"] = dataframe["atr"] * tp_mult

        # =============================================
        # Adam Theory double reflection projection
        # =============================================
        dataframe = adam_projection(dataframe, lookback=self.adam_lookback.value)

        # =============================================
        # Killzone + MiroFish Activity Multiplier (UTC)
        # =============================================
        # Inspired by MiroFish agent activity model:
        # Real-world market participation follows predictable
        # hourly patterns. Weight each hour by expected activity.
        dataframe["utc_hour"] = dataframe["date"].dt.hour

        # Activity multiplier per UTC hour (crypto-calibrated)
        # Based on combined US/EU/Asia trading patterns
        activity_map = {
            0: 0.3,  1: 0.2,  2: 0.15, 3: 0.10,  # Asia wind-down
            4: 0.10, 5: 0.15, 6: 0.3,  7: 0.7,    # London pre-market
            8: 0.9,  9: 1.0, 10: 0.9,              # London open peak
            11: 0.7, 12: 0.9, 13: 1.2, 14: 1.5,    # NY open overlap
            15: 1.3, 16: 1.0, 17: 0.8,              # London close / Silver Bullet
            18: 0.6, 19: 0.5, 20: 0.7, 21: 0.8,    # US afternoon + Asia wake
            22: 0.5, 23: 0.4,                        # US close
        }
        dataframe["activity_mult"] = dataframe["utc_hour"].map(activity_map).fillna(0.3)

        # Killzone = high activity hours (multiplier >= 0.7)
        dataframe["in_killzone"] = dataframe["activity_mult"] >= 0.7

        # =============================================
        # Active OB/FVG zone detection
        # =============================================
        dataframe = _detect_active_zones(dataframe)

        # =============================================
        # NEW: Funding Rate filter (contrarian)
        # =============================================
        if "funding_rate" in dataframe.columns:
            fr = dataframe["funding_rate"].fillna(0)
            # Extreme positive funding → avoid longs (crowd overleveraged long)
            # Extreme negative funding → avoid shorts
            dataframe["fr_ok_long"] = fr < 0.0005   # < 0.05%/8h
            dataframe["fr_ok_short"] = fr > -0.0005  # > -0.05%/8h
        else:
            dataframe["fr_ok_long"] = True
            dataframe["fr_ok_short"] = True

        # =============================================
        # NEW: ATR volatility regime
        # =============================================
        atr_ma50 = dataframe["atr"].rolling(50).mean()
        dataframe["vol_regime_ok"] = (
            (dataframe["atr"] > atr_ma50 * 0.5)   # Not dead market
            & (dataframe["atr"] < atr_ma50 * 3.0)  # Not extreme chaos
        )

        # =============================================
        # NEW: OTE zone (61.8%-79% retracement)
        # =============================================
        dataframe["in_ote_long"] = (
            (dataframe["close"] >= dataframe["ote_bottom"])
            & (dataframe["close"] <= dataframe["ote_top"])
            & (dataframe["in_discount"])
        )
        dataframe["in_ote_short"] = (
            (dataframe["close"] >= dataframe["range_high"] - (dataframe["range_high"] - dataframe["range_low"]) * 0.382)
            & (dataframe["in_premium"])
        )

        # =============================================
        # NEW: Recent liquidity sweep detection
        # =============================================
        # Check if liquidity was swept in the last 5 candles
        dataframe["recent_liq_sweep"] = False
        if "liq_swept" in dataframe.columns:
            for lookback in range(1, 6):
                swept = dataframe["liq_swept"].shift(lookback)
                dataframe["recent_liq_sweep"] = dataframe["recent_liq_sweep"] | (swept == 1)

        # =============================================
        # CONFIDENCE ENGINE (backtest-compatible)
        # =============================================
        # Builds a 0.0-1.0 confidence score from data available in the dataframe.
        # In live mode, the full GlobalConfidenceEngine supplements this.
        dataframe = _calculate_confidence(dataframe)

        # === Signal Audit ===
        self._audit_signals(dataframe, metadata)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Define entry conditions using SMC confluence."""

        killzone_filter = (
            (dataframe["in_killzone"]) | (self.use_killzone.value == 0)
        )

        # Adam Theory directional confluence filter (with slope threshold)
        adam_long_filter = (
            (dataframe["adam_bullish"].fillna(False))
            & (dataframe["adam_slope"] > 0.03)
        ) | (self.use_adam_filter.value == 0)
        adam_short_filter = (
            (~dataframe["adam_bullish"].fillna(True))
            & (dataframe["adam_slope"] < -0.03)
        ) | (self.use_adam_filter.value == 0)

        # Confidence gates: Grade A (strong confluence) has lower threshold
        # Grade A: confidence > 0.1 — strong SMC structure, trade even in DEFENSIVE
        # Grade B: confidence > 0.35 — weaker structure needs more macro confirmation
        confidence_ok_a = dataframe["confidence"] > 0.1
        confidence_ok_b = dataframe["confidence"] > 0.35

        # ===== LONG ENTRY =====
        # Grade A: OB+FVG confluence (strongest structure signal)
        # Grade B: OB or FVG alone (requires higher confidence)
        zone_long_a = dataframe["ob_fvg_confluence_bull"].fillna(False)
        zone_long_b = (
            (dataframe["in_bullish_ob"] | dataframe["in_bullish_fvg"])
        )

        # 4H zone alignment: Grade A always, Grade B requires 4H zone
        htf_zone = dataframe.get("htf_zone_aligned", True)

        # VWAP direction filter: long above VWAP, short below (reduces ~30% false signals)
        vwap_long = dataframe["above_vwap"].fillna(True)
        vwap_short = (~dataframe["above_vwap"]).fillna(True)

        # Retracement validation: 38.2%-78.6% Fibonacci zone
        retrace_ok = dataframe["valid_retrace"].fillna(True)

        dataframe.loc[
            (
                (dataframe["htf_trend"] > 0)                    # 4H bullish
                & (dataframe["in_ote_long"])             # OTE zone (discount)
                & (
                    (zone_long_a & confidence_ok_a)               # Grade A: conf > 0.1
                    | (zone_long_b & htf_zone & confidence_ok_b)  # Grade B: conf > 0.35 + 4H zone
                )
                & adam_long_filter                                # Adam projection up
                & vwap_long                                      # Above VWAP
                & retrace_ok                                     # Valid Fib retracement
                & (dataframe["fr_ok_long"])              # Funding rate OK
                & (dataframe["vol_regime_ok"])           # Volatility normal
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== SHORT ENTRY =====
        zone_short_a = dataframe["ob_fvg_confluence_bear"].fillna(False)
        zone_short_b = (
            (dataframe["in_bearish_ob"] | dataframe["in_bearish_fvg"])
        )

        dataframe.loc[
            (
                (dataframe["htf_trend"] < 0)                    # 4H bearish
                & (dataframe["in_ote_short"])            # Premium zone OTE
                & (
                    (zone_short_a & confidence_ok_a)              # Grade A: conf > 0.1
                    | (zone_short_b & htf_zone & confidence_ok_b) # Grade B: conf > 0.35 + 4H zone
                )
                & adam_short_filter                               # Adam projection down
                & vwap_short                                     # Below VWAP
                & retrace_ok                                     # Valid Fib retracement
                & (dataframe["fr_ok_short"])             # Funding rate OK
                & (dataframe["vol_regime_ok"])           # Volatility normal
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        # ===== REVERSE CONFIDENCE SHORT (低信心反轉做空) =====
        # When confidence < 0.2 (HIBERNATE), treat as shorting opportunity
        # Requires: bearish HTF + bearish SMC structure
        reverse_conf = dataframe["confidence"] < 0.20
        reverse_short_conf = 1.0 - dataframe["confidence"]  # Invert for sizing

        zone_rev_short_a = dataframe["ob_fvg_confluence_bear"].fillna(False)
        zone_rev_short_b = (
            (dataframe["in_bearish_ob"] | dataframe["in_bearish_fvg"])
            & (reverse_short_conf >= 0.5)
        )

        dataframe.loc[
            (
                reverse_conf                                         # Low confidence (HIBERNATE)
                & (dataframe["htf_trend"] < 0)                      # 4H bearish confirmed
                & (dataframe["in_ote_short"])                         # Premium zone
                & (
                    zone_rev_short_a                                  # Grade A
                    | (zone_rev_short_b & htf_zone)                  # Grade B + 4H zone
                )
                & adam_short_filter                                   # Adam projection down
                & (dataframe["fr_ok_short"])                          # Funding rate OK
                & (dataframe["vol_regime_ok"])                        # Volatility normal
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            ["enter_short", "enter_tag"],
        ] = [1, "reverse_confidence_short"]

        # === Signal diagnostics ===
        n_long = int(dataframe.get("enter_long", 0).sum())
        n_short = int(dataframe.get("enter_short", 0).sum())
        conf_last = dataframe["confidence"].iloc[-1] if len(dataframe) > 0 else 0
        htf_last = dataframe["htf_trend"].iloc[-1] if len(dataframe) > 0 else 0
        logger.info(
            "Signals %s: %d long, %d short | conf=%.2f htf=%d | candles=%d",
            metadata.get("pair", "?"), n_long, n_short, conf_last, htf_last, len(dataframe)
        )

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit signals are handled by custom_exit() with minimum hold time."""
        # CHoCH exits moved to custom_exit() to enforce 2-hour minimum hold time.
        # This prevents rapid entry/exit cycling on 15m candles.
        return dataframe

    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        """R-multiple take-profit + CHoCH exit with minimum hold time.

        Exit priorities:
        1. R-multiple take-profit: full exit at 3R (let custom_stoploss trail handle partials)
        2. CHoCH exit: structure break after minimum 2-hour hold
        Stoploss and custom_stoploss still apply normally for risk management.
        """
        # Minimum hold time: 8 candles × timeframe minutes
        _tf_minutes = {"1h": 60, "15m": 15, "5m": 5}.get(self.timeframe, 15)
        min_hold_minutes = 8 * _tf_minutes  # 2 hours on 15m, 8 hours on 1h
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60

        if trade_duration < min_hold_minutes:
            return None  # Too early — let stoploss handle risk

        # Calculate R-multiple for take-profit
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        atr = last.get("atr", 0)
        if atr > 0:
            atr_sl_pct = (atr * self.atr_sl_mult.value) / trade.open_rate
            if atr_sl_pct > 0:
                r_multiple = current_profit / atr_sl_pct
                # Full take-profit at 3R — exceptional trade, lock in gains
                if r_multiple >= 3.0:
                    return "take_profit_3R"

        # CHoCH exit: structure break after hold time
        if trade.is_short and last.get("choch") == 1:
            return "choch_bullish_reversal"
        elif not trade.is_short and last.get("choch") == -1:
            return "choch_bearish_reversal"

        return None

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        """進場確認 — 極端行情熔斷 + Guard Pipeline + Telegram."""
        # === Extreme Market Circuit Breaker ===
        # Block ALL entries during market crashes/panics
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        # Candles per 24h depends on timeframe
        _candles_24h = {"1h": 24, "15m": 96, "5m": 288}.get(self.timeframe, 24)
        if len(dataframe) > _candles_24h:
            last = dataframe.iloc[-1]
            # 1. BTC 24h crash > -10% → full stop
            btc_24h = dataframe["close"].pct_change(_candles_24h).iloc[-1]
            if abs(btc_24h) > 0.10:
                logger.warning("CIRCUIT BREAKER: BTC 24h move %.1f%% — blocking entry", btc_24h * 100)
                if _TG_AVAILABLE:
                    from market_monitor.telegram_zh import send_message
                    send_message(f"🚨 *熔斷機制啟動*\nBTC 24h 變動 {btc_24h*100:.1f}%\n所有進場已暫停")
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_activations")
                return False

            # 2. ATR spike > 3x average → extreme volatility
            atr = last.get("atr", 0)
            atr_ma = dataframe["atr"].rolling(50).mean().iloc[-1] if len(dataframe) > 50 else atr
            if atr > 0 and atr_ma > 0 and atr / atr_ma > 3.0:
                logger.warning("CIRCUIT BREAKER: ATR spike %.1fx — blocking entry", atr / atr_ma)
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_activations")
                return False

            # 3. Confidence HIBERNATE → block LONGS only (shorts use reverse confidence)
            confidence = last.get("confidence", 0.5)
            if confidence < 0.15 and side != "short":
                logger.warning("CIRCUIT BREAKER: Confidence %.2f (HIBERNATE) — blocking long entry", confidence)
                if _STATE_AVAILABLE:
                    BotStateStore.increment("circuit_breaker_activations")
                return False

        # === Crypto Environment Filter ===
        # Block entries when crypto environment is HOSTILE for this token
        if self._crypto_env and self.config.get("runmode", {}).value in ("live", "dry_run"):
            # Extract base symbol from pair (e.g., "BTC/USDT:USDT" → "BTC")
            base_sym = pair.split("/")[0] if "/" in pair else pair[:3]
            env_score = self._crypto_env.get(base_sym, 0.5)
            if env_score < 0.25:
                logger.warning(
                    "CRYPTO ENV BLOCK: %s env=%.2f (HOSTILE) — blocking %s entry",
                    base_sym, env_score, side,
                )
                if _TG_AVAILABLE:
                    from market_monitor.telegram_zh import send_message
                    send_message(f"🔗 *Crypto Env 攔截*\n{pair} {side}\n{base_sym} 環境: {env_score:.2f} (不利)")
                return False

        # === Guard Pipeline check (live/dry_run only) ===
        # CRITICAL: On guard error, REJECT the trade (fail-safe).
        # Never allow a trade to proceed without risk checks.
        if self.config.get("runmode", {}).value in ("live", "dry_run"):
            try:
                # Ensure guards/ is importable (Docker path may differ)
                _sdir = str(Path(__file__).resolve().parent)
                if _sdir not in sys.path:
                    sys.path.insert(0, _sdir)
                from guards.base import GuardContext
                from guards.pipeline import create_default_pipeline

                # Use actual computed leverage, not the default
                confidence = 0.5
                dataframe_tmp, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if len(dataframe_tmp) > 0:
                    confidence = dataframe_tmp.iloc[-1].get("confidence", 0.5)
                actual_leverage = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)

                # Build open positions map for TotalExposureGuard
                open_pos = {}
                if hasattr(self, 'dp') and self.dp:
                    for t in Trade.get_trades_proxy(is_open=True):
                        open_pos[t.pair] = {"value": t.stake_amount * t.leverage}

                ctx = GuardContext(
                    symbol=pair,
                    side="short" if side == "short" else "long",
                    amount=amount * rate,
                    leverage=actual_leverage,
                    account_balance=self.wallets.get_total("USDT") if self.wallets else 1000,
                    open_positions=open_pos,
                )
                pipeline = create_default_pipeline()

                # Update DrawdownGuard equity tracking before running checks
                from guards.guards import DrawdownGuard
                for g in pipeline.guards:
                    if isinstance(g, DrawdownGuard):
                        g.update_equity(ctx.account_balance)
                        break

                rejection = pipeline.run(ctx)  # Synchronous — no async fragility
                if rejection:
                    logger.warning("Guard rejected %s %s: %s", pair, side, rejection)
                    if _TG_AVAILABLE:
                        from market_monitor.telegram_zh import send_message
                        send_message(f"🛡️ *Guard 攔截*\n{pair} {side}\n原因: {rejection}")
                    if _STATE_AVAILABLE:
                        BotStateStore.increment("guard_rejections_today")
                    return False
            except Exception as e:
                # FAIL-SAFE: Guard error = REJECT trade (never trade unguarded)
                logger.error("Guard Pipeline CRITICAL error — BLOCKING trade: %s", e)
                return False

        if _TG_AVAILABLE:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            confidence = 0.5
            details = {}
            if len(dataframe) > 0:
                last = dataframe.iloc[-1]
                confidence = last.get("confidence", 0.5)

                # Build detailed entry reasons
                details = {
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
                    "ob_range": f"{last.get('ob_bottom', 0):,.0f}-{last.get('ob_top', 0):,.0f}" if last.get("ob_top") else None,
                    "fvg_range": f"{last.get('fvg_bottom', 0):,.0f}-{last.get('fvg_top', 0):,.0f}" if last.get("fvg_top") else None,
                    # Confidence factor breakdown (approximate from raw)
                    "confidence_factors": {
                        "momentum": float(last.get("adam_slope", 0) > 0) * 0.7 + 0.3,
                        "trend": 0.7 if last.get("htf_trend", 0) != 0 else 0.3,
                        "volume": min(float(last.get("volume", 0)) / (float(dataframe["volume"].rolling(20).mean().iloc[-1]) + 1e-10) * 0.5, 1.0) if len(dataframe) > 20 else 0.5,
                        "volatility": 0.6,  # Approximate
                        "health": confidence,
                    },
                    "missing_sources": _get_missing_sources(),
                }

            # Determine if reverse confidence short
            is_reversal = (side == "short" and confidence < 0.20)

            # Calculate enriched data
            _funding_rate = None
            _vol_regime = None
            _crypto_env_score = None
            _crypto_env_regime = None
            _expected_rr = None

            if len(dataframe) > 0:
                last = dataframe.iloc[-1]
                # Volatility regime
                atr_val = last.get("atr", 0)
                atr_ma_val = dataframe["atr"].rolling(50).mean().iloc[-1] if len(dataframe) > 50 else atr_val
                if atr_ma_val > 0:
                    atr_ratio = atr_val / atr_ma_val
                    if atr_ratio > 1.5:
                        _vol_regime = "擴張"
                    elif atr_ratio < 0.5:
                        _vol_regime = "低迷"
                    else:
                        _vol_regime = "正常"

                # Crypto environment from cache
                base_sym = pair.split("/")[0] if "/" in pair else pair[:3]
                env_data = self._crypto_env_cache.get(base_sym, {})
                if env_data:
                    _crypto_env_score = env_data.get("score")
                    _crypto_env_regime = env_data.get("regime")

                # Expected risk-reward ratio
                atr_sl = last.get("atr_sl_dist", 0)
                atr_tp = last.get("atr_tp_dist", 0)
                if atr_sl > 0:
                    _expected_rr = atr_tp / atr_sl

            # Use reverse confidence for leverage calculation if applicable
            if is_reversal:
                effective_conf = 1.0 - confidence
                lev = 1.0 + (self.max_leverage.value - 1.0) * (effective_conf ** 2)
                # Cap reverse short leverage at 80% of max
                lev = min(lev, self.max_leverage.value * 0.8)
            else:
                lev = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)

            notify_entry(
                pair=pair, side=side, rate=rate,
                stake=amount * rate, leverage=round(lev, 1),
                confidence=confidence,
                details=details,
                funding_rate=_funding_rate,
                volatility_regime=_vol_regime,
                crypto_env_score=_crypto_env_score,
                crypto_env_regime=_crypto_env_regime,
                expected_rr=_expected_rr,
                is_reversal=is_reversal,
            )
        return True

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime,
                           **kwargs) -> bool:
        """出場確認 — 滑點追蹤 + 繁體中文 Telegram 通知."""
        profit_pct = trade.calc_profit_ratio(rate) * 100
        profit_usdt = trade.calc_profit(rate)
        duration = str(current_time - trade.open_date_utc).split(".")[0]
        side = "short" if trade.is_short else "long"

        # === Slippage Tracking (Execution Quality) ===
        if hasattr(trade, 'open_rate') and trade.open_rate:
            # For exit, track the exit rate vs current market
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(dataframe) > 0:
                market_price = dataframe.iloc[-1]["close"]
                if market_price > 0:
                    exit_slippage_pct = abs(rate - market_price) / market_price * 100
                    if exit_slippage_pct > 0.5:  # > 0.5% slippage is noteworthy
                        logger.warning(
                            "SLIPPAGE: %s exit %.4f vs market %.4f (%.2f%%)",
                            pair, rate, market_price, exit_slippage_pct
                        )

        confidence = 0.5
        if len(dataframe) > 0:
            confidence = dataframe.iloc[-1].get("confidence", 0.5)

        # Calculate enriched exit data
        _win_rate = None
        _drawdown = None
        _equity = None
        _consecutive = None

        try:
            trades = Trade.get_trades_proxy(is_open=False)
            if trades:
                wins = sum(1 for t in trades if t.profit_ratio and t.profit_ratio > 0)
                total = len(trades)
                _win_rate = (wins / total * 100) if total > 0 else None
        except Exception:
            pass

        try:
            if self.wallets:
                _equity = self.wallets.get_total("USDT")
        except Exception:
            pass

        # Update consecutive tracking via state store
        if _STATE_AVAILABLE:
            state = BotStateStore.read()
            if profit_pct >= 0:
                consec_w = state.get("consecutive_wins", 0) + 1
                BotStateStore.update(consecutive_wins=consec_w, consecutive_losses=0)
                if consec_w >= 2:
                    _consecutive = f"{consec_w}連勝 🔥"
            else:
                consec_l = state.get("consecutive_losses", 0) + 1
                BotStateStore.update(consecutive_losses=consec_l, consecutive_wins=0)
                if consec_l >= 2:
                    _consecutive = f"{consec_l}連敗"

        reason_zh = {
            "exit_signal": "📊 結構反轉 (CHoCH)",
            "stop_loss": "🛑 觸發止損",
            "trailing_stop_loss": "📈 追蹤止損",
            "force_exit": "⚡ 強制出場",
        }.get(exit_reason, exit_reason)

        if _TG_AVAILABLE:
            if "stop_loss" in exit_reason:
                notify_stoploss(pair, side, profit_pct, profit_usdt)
            else:
                notify_exit(
                    pair=pair, side=side, profit_pct=profit_pct,
                    profit_usdt=profit_usdt, exit_reason=reason_zh,
                    duration=duration, confidence=confidence,
                    win_rate=_win_rate,
                    running_drawdown=_drawdown,
                    equity_after=_equity,
                    consecutive_result=_consecutive,
                )

        # === Update Guard Pipeline State ===
        # CRITICAL: Guards must track losses to enforce DailyLoss and ConsecutiveLoss limits
        try:
            from guards.pipeline import get_guard
            from guards.guards import CooldownGuard, DailyLossGuard, ConsecutiveLossGuard

            # Record cooldown
            cooldown = get_guard(CooldownGuard)
            if cooldown:
                cooldown.record_trade(pair)

            # Record win/loss for consecutive loss tracking
            consec = get_guard(ConsecutiveLossGuard)
            if consec:
                consec.record_result(is_win=(profit_usdt > 0))

            # Record daily loss
            if profit_usdt < 0:
                daily = get_guard(DailyLossGuard)
                if daily:
                    daily.record_loss(abs(profit_usdt))

            # Persist to disk (survives restart)
            from guards.pipeline import save_state
            save_state()
        except Exception as e:
            logger.warning("Guard state update failed: %s", e)

        # Audit log entry
        logger.info(
            "TRADE_AUDIT: %s %s %s | Entry:%.2f Exit:%.2f | P&L:%.2f%% ($%.2f) | "
            "Duration:%s | Reason:%s | Confidence:%.2f",
            pair, side, exit_reason, trade.open_rate, rate,
            profit_pct, profit_usdt, duration, exit_reason, confidence
        )
        return True

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Continuous leverage scaling by confidence.

        leverage = 1.0 + (max_leverage - 1.0) × confidence²
        Quadratic curve: confidence must be HIGH to get full leverage.
          conf=0.3 → 1.18x
          conf=0.5 → 1.5x
          conf=0.7 → 1.98x
          conf=0.9 → 2.62x
          conf=1.0 → 3.0x (max)
        """
        # Use live macro confidence if available, else fall back to dataframe
        if self._live_confidence is not None:
            confidence = self._live_confidence
        else:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(dataframe) == 0:
                return 1.0
            confidence = dataframe.iloc[-1].get("confidence", 0.5)

        max_lev = self.max_leverage.value

        # Reverse confidence mode: low confidence + short = high short confidence
        if side == "short" and confidence < 0.20:
            short_conf = 1.0 - confidence
            lev = 1.0 + (max_lev - 1.0) * (short_conf ** 2)
            # Cap at 80% of max leverage for reverse shorts (more conservative)
            return min(max(lev, 1.0), max_lev * 0.8, max_leverage)

        # Normal mode: quadratic scaling
        lev = 1.0 + (max_lev - 1.0) * (confidence ** 2)
        return min(max(lev, 1.0), max_leverage)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float | None:
        """ATR-based dynamic stop loss with 1R breakeven.

        Phase 1 (initial): ATR × sl_mult from entry price
        Phase 2 (1R profit): Move stop to breakeven (entry price)
        Phase 3 (2R profit): Trail at 1R below current high

        Returns negative float (distance from current rate) or None to keep current.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None  # Keep default stoploss

        last = dataframe.iloc[-1]
        atr = last.get("atr", 0)
        if atr <= 0:
            return None

        sl_mult = self.atr_sl_mult.value  # Default 1.5

        # ATR-based risk distance
        atr_sl_dist = atr * sl_mult
        atr_sl_pct = atr_sl_dist / trade.open_rate  # As fraction of entry price

        # Calculate profit in R-multiples (1R = initial risk)
        if atr_sl_pct > 0:
            r_multiple = current_profit / atr_sl_pct
        else:
            r_multiple = 0

        if r_multiple >= 2.0:
            # Phase 3: Trail at 1R below — lock in at least 1R profit
            # Return stoploss relative to current_rate
            trail_dist = atr_sl_pct  # Trail by 1R
            new_sl = -(trail_dist)
            return max(new_sl, -0.01)  # Never tighter than 1%

        elif r_multiple >= 1.0:
            # Phase 2: Breakeven — move stop to entry price
            # Stoploss relative to current_rate: need to protect entry
            # current_profit is already the distance from entry
            # Return a value that puts stop at entry (slight buffer for fees)
            breakeven_sl = -(current_profit - 0.002)  # 0.2% buffer for fees
            return min(breakeven_sl, -0.002)  # At least 0.2% from current

        else:
            # Phase 1: ATR-based initial stop
            return -atr_sl_pct

    def custom_stake_amount(self, current_time, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str,
                            **kwargs) -> float:
        """Continuous position sizing by confidence × activity regime.

        base_scale = 0.3 + 0.9 × confidence  (range: 0.3x to 1.2x)
        final_scale = base_scale × activity_boost

        Activity boost (MiroFish): peak hours get 10% extra,
        dead hours get 20% reduction.
        """
        pair = kwargs.get("pair", "")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        activity = last.get("activity_mult", 0.5)

        # Reverse confidence for shorts in HIBERNATE
        if side == "short" and confidence < 0.20:
            confidence = 1.0 - confidence

        # Base scaling by confidence (widened range for stronger conviction trades)
        scale = 0.2 + 1.3 * confidence  # range 0.2x-1.5x

        # Anti-Martingale: increase after wins, decrease after losses
        # Read streak from BotStateStore (persisted across restarts)
        wins = 0
        losses = 0
        if _STATE_AVAILABLE:
            state = BotStateStore.read()
            wins = state.get("consecutive_wins", 0)
            losses = state.get("consecutive_losses", 0)
        if losses > 0:
            anti_mart = max(1.0 - losses * 0.20, 0.40)  # -20% per loss, floor 40%
        else:
            anti_mart = 1.0 + min(wins * 0.15, 0.45)  # +15% per win, cap +45%
        scale *= anti_mart

        # Activity boost: peak hours (>1.0) get up to +10%
        # Dead hours (<0.3) get -20% (thinner liquidity = smaller size)
        if activity >= 1.0:
            scale *= 1.1
        elif activity < 0.3:
            scale *= 0.8

        adjusted = proposed_stake * scale

        if min_stake is not None:
            adjusted = max(adjusted, min_stake)
        return min(adjusted, max_stake)

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> float | None:
        """Partial profit-taking at R-multiples + pyramid into winners.

        Partial exits (negative return):
        - At 1R profit: sell 33% of position
        - At 2R profit: sell another 33%
        - Remaining 34% rides with trailing stop

        Pyramid adds (positive return):
        - At 5%+ profit with confidence >= 0.5
        """
        pair = trade.pair
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        atr = last.get("atr", 0)

        # === PARTIAL PROFIT-TAKING (before pyramid logic) ===
        if atr > 0 and trade.stake_amount > 0:
            atr_sl_pct = (atr * self.atr_sl_mult.value) / trade.open_rate
            if atr_sl_pct > 0:
                r_multiple = current_profit / atr_sl_pct

                # Track partials via trade custom_info (survives restarts)
                trade_info = trade.get_custom_data("partials") if hasattr(trade, "get_custom_data") else None
                partials_done = int(trade_info) if trade_info else 0

                # 1R → sell 33%
                if r_multiple >= 1.0 and partials_done < 1:
                    if hasattr(trade, "set_custom_data"):
                        trade.set_custom_data("partials", 1)
                    partial_amount = trade.stake_amount * 0.33
                    if min_stake and partial_amount < min_stake:
                        return None
                    logger.info("Partial exit 1R for %s: -%.2f USDT (R=%.1f)", pair, partial_amount, r_multiple)
                    return -partial_amount

                # 2R → sell another 33%
                if r_multiple >= 2.0 and partials_done < 2:
                    if hasattr(trade, "set_custom_data"):
                        trade.set_custom_data("partials", 2)
                    partial_amount = trade.stake_amount * 0.33
                    if min_stake and partial_amount < min_stake:
                        return None
                    logger.info("Partial exit 2R for %s: -%.2f USDT (R=%.1f)", pair, partial_amount, r_multiple)
                    return -partial_amount

        # === PYRAMID ADDS ===
        if current_profit < 0.05:
            return None  # Not enough profit separation

        # Check how many times we've already added
        filled_entries = trade.nr_of_successful_entries
        if filled_entries >= 3:
            return None  # Already at max pyramid

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        htf_trend = last.get("htf_trend", 0)

        # Confidence gate: CAUTIOUS or above (lowered from 0.7 to enable more pyramids)
        if confidence < 0.5:
            return None

        # Trend must still agree with position direction
        if trade.is_short and htf_trend > 0:
            return None  # Short but trend turned bullish
        if not trade.is_short and htf_trend < 0:
            return None  # Long but trend turned bearish

        # Progressive add-on sizing (increased from 0.5/0.3 for stronger compounding)
        if filled_entries == 1:
            addon_ratio = 0.6
        elif filled_entries == 2:
            addon_ratio = 0.4
        else:
            return None

        # Scale by confidence: higher confidence = larger add-on
        addon_ratio *= confidence

        try:
            stake = trade.stake_amount * addon_ratio
        except Exception:
            return None

        if min_stake is not None and stake < min_stake:
            return None
        if stake > max_stake:
            stake = max_stake

        logger.info(
            "Pyramid add-on #%d for %s: +%.2f USDT (profit=%.1f%%, confidence=%.2f)",
            filled_entries, pair, stake, current_profit * 100, confidence
        )

        if _TG_AVAILABLE:
            notify_pyramid(pair, filled_entries, stake, current_profit * 100, confidence)

        return stake


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


# =============================================
# Helper functions
# =============================================

def _compute_trend(df: DataFrame, bos_col: str, choch_col: str) -> pd.Series:
    """Compute running trend direction from BOS/CHoCH signals.

    Returns: Series of 1 (bullish), -1 (bearish), 0 (neutral)
    """
    trend = pd.Series(0, index=df.index, dtype=int)
    current = 0
    for i in range(len(df)):
        bos_val = df[bos_col].iloc[i]
        choch_val = df[choch_col].iloc[i]

        if not pd.isna(bos_val):
            current = int(bos_val)  # 1 = bullish BOS, -1 = bearish BOS
        elif not pd.isna(choch_val):
            current = int(choch_val)  # CHoCH reverses trend

        trend.iloc[i] = current
    return trend


def _add_premium_discount(df: DataFrame) -> DataFrame:
    """Add premium/discount zone based on recent swing range."""
    # Find recent swing high and low (rolling window)
    window = 50
    df["range_high"] = df["high"].rolling(window).max()
    df["range_low"] = df["low"].rolling(window).min()
    df["equilibrium"] = (df["range_high"] + df["range_low"]) / 2

    # Premium = above equilibrium, Discount = below
    df["in_premium"] = df["close"] > df["equilibrium"]
    df["in_discount"] = df["close"] < df["equilibrium"]

    # OTE zone (50% - 79% retracement) — widened from 61.8% to capture more reversals
    range_size = df["range_high"] - df["range_low"]
    df["ote_top"] = df["range_high"] - (range_size * 0.50)
    df["ote_bottom"] = df["range_high"] - (range_size * 0.79)

    return df


def _detect_active_zones(df: DataFrame) -> DataFrame:
    """Detect if current price is within an active (unmitigated) OB or FVG.

    Also detects OB+FVG confluence (Grade A zones):
    - Direct overlap: OB and FVG price ranges intersect
    - Proximity: distance between OB and FVG edges < 0.5 × ATR
    """
    df["in_bullish_ob"] = False
    df["in_bearish_ob"] = False
    df["in_bullish_fvg"] = False
    df["in_bearish_fvg"] = False
    df["ob_fvg_confluence_bull"] = False  # Grade A: OB+FVG overlap
    df["ob_fvg_confluence_bear"] = False

    # Track active (unmitigated) order blocks
    active_obs = []  # list of (type, top, bottom, index)

    for i in range(len(df)):
        ob_val = df["ob"].iloc[i]
        close = df["close"].iloc[i]

        # Register new order blocks
        if not pd.isna(ob_val) and ob_val != 0:
            top = df["ob_top"].iloc[i]
            bottom = df["ob_bottom"].iloc[i]
            if not pd.isna(top) and not pd.isna(bottom):
                active_obs.append({
                    "type": int(ob_val),  # 1=bullish, -1=bearish
                    "top": top,
                    "bottom": bottom,
                    "created": i,
                })

        # Check if price is in any active OB
        remaining = []
        for ob_zone in active_obs:
            # Check mitigation (price closed through the OB)
            if ob_zone["type"] == 1 and close < ob_zone["bottom"]:
                continue  # Mitigated bullish OB
            if ob_zone["type"] == -1 and close > ob_zone["top"]:
                continue  # Mitigated bearish OB
            # Remove old OBs (older than 72 candles = 3 days)
            if i - ob_zone["created"] > 72:
                continue

            remaining.append(ob_zone)

            # Check if price is within OB zone
            if ob_zone["bottom"] <= close <= ob_zone["top"]:
                if ob_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_ob"] = True
                else:
                    df.at[df.index[i], "in_bearish_ob"] = True

        active_obs = remaining

    # Track active FVGs (simpler — just check if current price fills gap)
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
            # Remove old FVGs (48h = 2 days for crypto speed)
            if i - fvg_zone["created"] > 48:
                continue

            # Mitigated: price closed through the FVG (filled the gap)
            if fvg_zone["type"] == 1 and close < fvg_zone["bottom"]:
                continue  # Bullish FVG broken below → mitigated
            if fvg_zone["type"] == -1 and close > fvg_zone["top"]:
                continue  # Bearish FVG broken above → mitigated

            remaining.append(fvg_zone)

            # Check if price is within active FVG zone
            if fvg_zone["bottom"] <= close <= fvg_zone["top"]:
                if fvg_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_fvg"] = True
                else:
                    df.at[df.index[i], "in_bearish_fvg"] = True

        active_fvgs = remaining

    # ===== OB+FVG Confluence (Grade A) =====
    # Price is in both OB and FVG simultaneously = strongest entry zone
    df["ob_fvg_confluence_bull"] = df["in_bullish_ob"] & df["in_bullish_fvg"]
    df["ob_fvg_confluence_bear"] = df["in_bearish_ob"] & df["in_bearish_fvg"]

    return df


def _calculate_confidence(df: DataFrame) -> DataFrame:
    """Calculate confidence score — optimized to capture favorable environments.

    Six factors designed to detect and ride momentum windows:

    1. Momentum (25%): Multi-period price momentum — catches the wind
    2. Trend alignment (25%): HTF trend direction + strength — confirms direction
    3. Volume conviction (12%): Volume expanding with trend — smart money participating
    4. Volatility quality (13%): ATR expanding in trend = good; ATR spiking in chop = bad
    5. Market health (13%): Price vs MA structure — overall regime
    6. Activity regime (12%): MiroFish-inspired hourly participation model

    Key design: FAST response to regime changes, AGGRESSIVE when aligned.
    """
    n = len(df)
    close_s = pd.Series(df["close"].values, index=df.index)
    atr_s = pd.Series(df.get("atr", pd.Series(np.zeros(n))).values, index=df.index)
    volume_s = pd.Series(df["volume"].values, index=df.index)
    htf_trend = df.get("htf_trend", pd.Series(np.zeros(n), index=df.index)).values

    # ==========================================================
    # 1. MOMENTUM SANDBOX (25%) — "Is the wind blowing?"
    # ==========================================================
    # Multi-period ROC: 6h, 24h, 72h momentum (weighted toward longer-term)
    roc_6 = close_s.pct_change(6)    # 6-hour momentum
    roc_24 = close_s.pct_change(24)  # 1-day momentum
    roc_72 = close_s.pct_change(72)  # 3-day momentum

    # Positive momentum across all timeframes = strong wind
    # Convert each to 0-1 score: +5% = 0.8, -5% = 0.2
    mom_6 = np.clip(0.5 + roc_6.fillna(0) * 8, 0.05, 0.95)
    mom_24 = np.clip(0.5 + roc_24.fillna(0) * 5, 0.05, 0.95)
    mom_72 = np.clip(0.5 + roc_72.fillna(0) * 3, 0.05, 0.95)

    # Multi-period alignment bonus: all three agree = extra boost
    all_positive = (roc_6 > 0) & (roc_24 > 0) & (roc_72 > 0)
    all_negative = (roc_6 < 0) & (roc_24 < 0) & (roc_72 < 0)
    alignment_bonus = np.where(all_positive | all_negative, 0.15, 0.0)

    # Weight longer-term ROC more heavily to reduce whipsaws on 15m candles
    # ROC-6 (1.5h): noise-prone on 15m → reduced to 20%
    # ROC-72 (3d): stable trend signal → increased to 45%
    momentum_score = (mom_6 * 0.20 + mom_24 * 0.35 + mom_72 * 0.45 + alignment_bonus)
    momentum_score = np.clip(momentum_score, 0, 1)

    # ==========================================================
    # 2. TREND ALIGNMENT (25%) — "Is the structure confirmed?"
    # ==========================================================
    # HTF trend direction (instant, not streak-based)
    # Trend present = 0.7 base; no trend = 0.3
    trend_present = np.where(htf_trend != 0, 0.7, 0.3)

    # Trend agrees with short-term momentum = extra confidence
    trend_momentum_agree = np.where(
        ((htf_trend > 0) & (roc_24.fillna(0) > 0)) |
        ((htf_trend < 0) & (roc_24.fillna(0) < 0)),
        0.3, 0.0
    )

    trend_score = np.clip(trend_present + trend_momentum_agree, 0, 1)

    # ==========================================================
    # 3. VOLUME CONVICTION (15%) — "Is smart money participating?"
    # ==========================================================
    vol_ma20 = volume_s.rolling(20, min_periods=5).mean()
    vol_ratio = (volume_s / (vol_ma20 + 1e-10)).fillna(1)

    # Volume expanding = conviction; volume dying = distribution
    # ratio > 1.5 = strong, 1.0 = normal, < 0.5 = dead
    vol_score = np.clip(vol_ratio * 0.5, 0.1, 0.95)

    # Volume expanding WITH trend = extra good
    vol_trend_agree = np.where(
        (vol_ratio > 1.2) & (htf_trend != 0),
        0.15, 0.0
    )
    volume_score = np.clip(vol_score + vol_trend_agree, 0, 1)

    # ==========================================================
    # 4. VOLATILITY QUALITY (15%) — "Is it the right kind of vol?"
    # ==========================================================
    if n > 50:
        atr_ma50 = atr_s.rolling(50, min_periods=10).mean()
        atr_ratio = (atr_s / (atr_ma50 + 1e-10)).fillna(1)

        # KEY INSIGHT: ATR expanding in a trend = GOOD (momentum)
        #              ATR expanding without trend = BAD (chaos)
        #              ATR contracting = preparing (neutral-to-good)
        trending = htf_trend != 0
        vol_expanding = atr_ratio > 1.2
        vol_contracting = atr_ratio < 0.7

        vol_quality = np.where(
            trending & vol_expanding, 0.85,        # Trend + expanding = excellent
            np.where(
                vol_contracting, 0.65,             # Contracting = squeeze building
                np.where(
                    ~trending & vol_expanding, 0.25, # No trend + expanding = chaos
                    0.55                             # Normal
                )
            )
        )
    else:
        vol_quality = np.full(n, 0.5)

    volatility_score = np.clip(vol_quality, 0, 1)

    # ==========================================================
    # 5. MARKET HEALTH (15%) — "Is the environment supportive?"
    # ==========================================================
    # Price vs EMA50 and EMA200
    ema50 = close_s.ewm(span=50, min_periods=20).mean()
    ema200 = close_s.ewm(span=200, min_periods=50).mean()

    # Bullish structure: price > EMA50 > EMA200
    bull_structure = (close_s > ema50) & (ema50 > ema200)
    bear_structure = (close_s < ema50) & (ema50 < ema200)
    price_above_ema50 = close_s > ema50

    health_score = np.where(
        bull_structure, 0.85,
        np.where(
            bear_structure, 0.15,  # Clear downtrend
            np.where(
                price_above_ema50, 0.65,  # Mixed but above 50
                0.35  # Mixed but below 50
            )
        )
    )

    # Funding rate overlay (if available)
    if "funding_rate" in df.columns:
        fr = df["funding_rate"].fillna(0).values
        # Extreme funding warns of overleverage
        fr_penalty = np.where(np.abs(fr) > 0.0003, -0.15, 0.0)  # > 0.03%/8h
        health_score = np.clip(health_score + fr_penalty, 0, 1)

    # ==========================================================
    # 6. ACTIVITY REGIME (12%) — MiroFish-inspired participation model
    # ==========================================================
    # Higher activity hours = more market participants = cleaner signals
    if "activity_mult" in df.columns:
        activity_score = df["activity_mult"].values
        # Normalize: 1.5 (peak) → 1.0, 0.1 (dead) → 0.07
        activity_score = np.clip(activity_score / 1.5, 0.05, 1.0)
    else:
        activity_score = np.full(n, 0.5)

    # ==========================================================
    # COMBINE — weighted sum (6 factors)
    # ==========================================================
    raw_confidence = (
        0.25 * momentum_score
        + 0.25 * trend_score
        + 0.12 * volume_score
        + 0.13 * volatility_score
        + 0.13 * health_score
        + 0.12 * activity_score
    )

    # Liquidity sweep bonus: recent sweep = momentum confirmation
    if "recent_liq_sweep" in df.columns:
        liq_bonus = np.where(df["recent_liq_sweep"].values, 0.08, 0.0)
        raw_confidence = raw_confidence + liq_bonus

    # EMA smoothing — span=3 on 15m = ~45 minutes response time
    conf_series = pd.Series(np.asarray(raw_confidence).flatten()).ewm(span=3, min_periods=1).mean()
    df["confidence"] = np.clip(conf_series.values, 0.0, 1.0)

    # Regime labels
    df["conf_regime"] = pd.cut(
        df["confidence"],
        bins=[0, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=["HIBERNATE", "DEFENSIVE", "CAUTIOUS", "NORMAL", "AGGRESSIVE"],
    )

    return df
