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
        ExitEvent,
        MultiTfState,
        PartialExitEvent,
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

    stoploss = -0.05
    trailing_stop = False
    use_custom_stoploss = False

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

        return dataframe

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

        # 1D trend reversal → force exit (strongest signal)
        daily_against = (is_long and last.get("st_1d") == -1) or (not is_long and last.get("st_1d") == 1)
        if daily_against and bars > 8:
            return "daily_reversal_exit"

        # 1H + 15m both flipped against → exit
        # But if we already took partial profits, let the tail ride until 1D reversal
        if trade.nr_of_successful_exits > 0:
            pass  # Only 1D reversal (above) can close remaining position
        else:
            st_against = (is_long and last["st_trend"] == -1) or (not is_long and last["st_trend"] == 1)
            hourly_against = (is_long and last.get("st_1h") == -1) or (not is_long and last.get("st_1h") == 1)
            if st_against and hourly_against and bars > 8:
                return "multi_tf_exit"

        # Time decay: 200+ bars (~50h) with tiny profit
        if bars > 200 and 0 < current_profit < 0.005:
            return "time_decay"

        return None

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
        return elapsed < self._CB_COOLDOWN_HOURS * 3600

    def custom_stake_amount(self, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str, **kwargs) -> float:
        """Rolling Kelly × trend quality. Scout = 25%, Confirmed = 75%.

        P0-4: bail out early if account-level circuit breaker is tripped.
        Returning 0 causes Freqtrade to skip this entry attempt.
        """
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

        # Scout gets 25% of Kelly, confirmed gets 75%
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
        if trade.enter_tag == "scout" and entries_done == 1:
            # 15m just flipped in our direction?
            is_15m_confirmed = (
                (is_long and last.get("st_trend") == 1)
                or (not is_long and last.get("st_trend") == -1)
            )
            if is_15m_confirmed and current_profit > -0.02:
                # Add 2x the original scout stake (25% → total 75%)
                addon = trade.stake_amount * 2.0
                return addon

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
        full reasoning, planned SL/TP plan, and multi-TF state."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_row = dataframe.iloc[-1] if len(dataframe) > 0 else None
        state = _snapshot_state(last_row)

        leverage = float(kwargs.get("leverage") or 1.0)
        notional_usd = amount * rate
        stake_usd = notional_usd / leverage if leverage > 0 else notional_usd

        sl_plan = default_stoploss_plan(side)
        tp_plan = default_take_profit_plan()

        kelly_full = self._calc_rolling_kelly()
        # Tag-conditioned scaling that mirrors custom_stake_amount logic
        kelly_scaled = kelly_full * (0.25 if entry_tag == "scout" else 0.75)
        quality_scale = 0.7 + state.trend_quality * 0.6

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
                note=f"Entry {entry_tag} {side} @ {rate:.4f}",
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
            f"策略: Supertrend 4L Scout (R46)"
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
