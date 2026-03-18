"""
Hourly Scanner - 每小時 BTC/ETH 60分K技術分析掃描與模擬交易
Run: cd apps/api && python -m src.jobs.hourly_scanner
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ambmh.scanner")

# Add src/ to path so that strategy modules (which use `from strategy.xxx`)
# resolve correctly.
_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

try:
    # Add project root to path for market_monitor import
    _project_root = str(Path(__file__).resolve().parents[4])
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from market_monitor.telegram_zh import notify_entry, notify_exit, notify_stoploss
    _TG_AVAILABLE = True
except ImportError:
    _TG_AVAILABLE = False

try:
    from src.services.trade_store import TradeStore
    _trade_store = TradeStore()
except ImportError:
    _trade_store = None

import ccxt
import pandas as pd

from strategy.enums import MarketState, SignalDirection, StrategyName
from strategy.layer1_market_structure.swing_detector import (
    detect_swing_highs,
    detect_swing_lows,
)
from strategy.layer1_market_structure.structure_analyzer import (
    classify_market_state,
    detect_choch,
)
from strategy.layer2_signal_engine.trend_indicators import (
    compute_ema_stack,
    compute_adx,
    evaluate_trend_signals,
)
from strategy.layer2_signal_engine.volatility_indicators import (
    compute_atr,
    compute_bollinger_bands,
    compute_bb_squeeze,
    detect_squeeze_release,
    evaluate_volatility_signals,
)
from strategy.layer3_strategies.strategy_b_squeeze import BBSqueezeStrategy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAME = "1h"
INITIAL_CAPITAL = 300.0
LEVERAGE = 1  # No leverage
DATA_DIR = Path(__file__).resolve().parents[4] / "data"  # D:/All-money-back-me-home/data/
STATE_FILE = DATA_DIR / "paper_trades.json"
MAX_RISK_PER_TRADE = 0.02  # 2% risk per trade
MAX_POSITIONS = 2  # One per symbol max


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load previous state from JSON file."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "open_positions": [],
        "closed_trades": [],
        "scan_history": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state: dict) -> None:
    """Save state to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
) -> pd.DataFrame:
    """Fetch OHLCV data from Binance."""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(
        ohlcv, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    )
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="ms", utc=True)
    df.set_index("Timestamp", inplace=True)
    return df


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def check_stop_loss_take_profit(
    position: dict,
    high: float,
    low: float,
    close: float,
) -> tuple[bool, str, float]:
    """Check if a position should be closed due to SL or TP hit.

    Uses candle High/Low for realistic intra-bar detection.
    When both SL and TP could trigger on the same bar, SL takes priority
    (conservative / anti-fragile).

    Returns (should_close, reason, exit_price).
    """
    direction = position["direction"]
    sl = position["stop_loss"]
    tp_levels = position.get("take_profit_levels", [])

    if direction == "long":
        # SL priority: check if low breached stop loss
        if low <= sl:
            return True, "止損觸發", sl
        if tp_levels and high >= tp_levels[0]:
            return True, f"止盈觸發 (目標 {tp_levels[0]:.2f})", tp_levels[0]
    elif direction == "short":
        # SL priority: check if high breached stop loss
        if high >= sl:
            return True, "止損觸發", sl
        if tp_levels and low <= tp_levels[0]:
            return True, f"止盈觸發 (目標 {tp_levels[0]:.2f})", tp_levels[0]

    return False, "", 0.0


def close_position(
    state: dict,
    position: dict,
    exit_price: float,
    reason: str,
) -> dict:
    """Close a position and record the trade."""
    entry = position["entry_price"]
    size = position["position_size_usd"]
    direction = position["direction"]

    if direction == "long":
        pnl_pct = (exit_price - entry) / entry
    else:
        pnl_pct = (entry - exit_price) / entry

    pnl_usd_gross = size * pnl_pct
    # Deduct commission (0.1% per side, both open and close)
    commission = size * 0.001 * 2
    pnl_usd = pnl_usd_gross - commission

    trade = {
        **position,
        "exit_price": round(exit_price, 8),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason,
        "pnl_usd": round(pnl_usd, 4),
        "pnl_pct": round(pnl_pct * 100, 4),
        "status": "closed",
    }

    state["capital"] = round(state["capital"] + pnl_usd, 4)
    state["closed_trades"].append(trade)
    state["open_positions"] = [
        p for p in state["open_positions"] if p["id"] != position["id"]
    ]

    # Persist close to Supabase
    if _trade_store and _trade_store.has_supabase and position.get("db_id"):
        try:
            _trade_store.close_trade(position["db_id"], trade)
        except Exception as e:
            log.warning("Supabase 儲存平倉失敗: %s", e)

    return trade


def open_position(
    state: dict,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit_levels: list[float],
    strategy: str,
    reason: str,
    confidence: float,
) -> dict | None:
    """Open a new paper trade position."""
    # Check if already have position in this symbol
    existing = [p for p in state["open_positions"] if p["symbol"] == symbol]
    if existing:
        return None

    # Check max positions
    if len(state["open_positions"]) >= MAX_POSITIONS:
        return None

    # Calculate position size based on risk
    if direction == "long":
        risk_per_unit = abs(entry_price - stop_loss) / entry_price
    else:
        risk_per_unit = abs(stop_loss - entry_price) / entry_price

    if risk_per_unit <= 0:
        return None

    risk_amount = state["capital"] * MAX_RISK_PER_TRADE
    # Max 50% capital per trade
    position_size_usd = min(risk_amount / risk_per_unit, state["capital"] * 0.5)

    position = {
        "id": f"{symbol.replace('/', '')}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "symbol": symbol,
        "direction": direction,
        "strategy": strategy,
        "entry_price": round(entry_price, 8),
        "stop_loss": round(stop_loss, 8),
        "take_profit_levels": [round(tp, 8) for tp in take_profit_levels],
        "position_size_usd": round(position_size_usd, 4),
        "leverage": LEVERAGE,
        "confidence": round(confidence, 4),
        "reason": reason,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "source": "bb_squeeze",
    }

    state["open_positions"].append(position)

    # Persist to Supabase
    if _trade_store and _trade_store.has_supabase:
        try:
            saved = _trade_store.save_open_trade(position)
            position["db_id"] = saved.get("db_id")
        except Exception as e:
            log.warning("Supabase 儲存開倉失敗: %s", e)

    return position


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------

def run_scan() -> None:
    """Main scan logic -- runs once per invocation."""
    now = datetime.now(timezone.utc)
    log.info("開始掃描 [%s]", now.strftime("%Y-%m-%d %H:%M:%S UTC"))

    # Load previous state
    state = load_state()
    log.info(
        "狀態 — 資金: $%.2f | 開倉: %d | 已平倉: %d",
        state["capital"], len(state["open_positions"]), len(state["closed_trades"]),
    )

    # Initialize exchange (public endpoints only -- no API key needed)
    exchange = ccxt.binance({"enableRateLimit": True})

    # Initialize strategy
    squeeze_strategy = BBSqueezeStrategy()

    scan_log: dict = {
        "time": now.isoformat(),
        "capital": round(state["capital"], 4),
        "scans": [],
        "actions": [],
    }

    for symbol in SYMBOLS:
        log.info("--- %s ---", symbol)

        try:
            # Fetch data
            df = fetch_ohlcv(exchange, symbol, TIMEFRAME, limit=200)
            current_price = float(df["Close"].iloc[-1])
            log.info("  %s 當前價格: $%,.2f", symbol, current_price)

            # ----------------------------------------------------------
            # Layer 1: Market Structure
            # ----------------------------------------------------------
            swing_highs = detect_swing_highs(df, lookback=5)
            swing_lows = detect_swing_lows(df, lookback=5)
            structure = classify_market_state(swing_highs, swing_lows)

            state_zh = {
                "TRENDING_UP": "上升趨勢",
                "TRENDING_DOWN": "下降趨勢",
                "RANGING": "區間震盪",
            }
            log.info(
                "  市場結構: %s (信心: %.0f%%)",
                state_zh.get(structure.state.value, structure.state.value),
                structure.confidence * 100,
            )
            if structure.choch_detected:
                log.warning("  !! CHoCH 偵測: %s", structure.choch_direction)

            # ----------------------------------------------------------
            # Layer 2: Indicators
            # ----------------------------------------------------------
            trend_signals = evaluate_trend_signals(df)
            vol_signals = evaluate_volatility_signals(df)

            for sig in trend_signals:
                if sig.name == "ADX" and sig.value is not None:
                    log.debug("  ADX: %.1f", sig.value)
                elif sig.name == "EMA_Stack":
                    dir_zh = {
                        "long": "多頭排列",
                        "short": "空頭排列",
                        "neutral": "無明確排列",
                    }
                    log.debug("  EMA: %s", dir_zh.get(sig.signal.value, sig.signal.value))

            for sig in vol_signals:
                if sig.name in ("BB_Squeeze_Active", "BB_Squeeze_Release"):
                    if sig.name == "BB_Squeeze_Active":
                        log.info("  BB Squeeze: 擠壓中")
                    else:
                        log.info("  BB Squeeze: 已釋放")

            # ----------------------------------------------------------
            # Check existing positions for SL/TP
            # ----------------------------------------------------------
            candle_high = float(df["High"].iloc[-1])
            candle_low = float(df["Low"].iloc[-1])

            for pos in list(state["open_positions"]):
                if pos["symbol"] == symbol:
                    should_close, reason, exit_price = check_stop_loss_take_profit(
                        pos, high=candle_high, low=candle_low, close=current_price,
                    )
                    if should_close:
                        trade = close_position(state, pos, exit_price, reason)
                        action = (
                            f"平倉 {symbol} ({reason}) | "
                            f"PnL: ${trade['pnl_usd']:+.2f} ({trade['pnl_pct']:+.2f}%)"
                        )
                        log.info("  >> %s", action)
                        scan_log["actions"].append(action)
                        if _TG_AVAILABLE:
                            if "止損" in reason:
                                notify_stoploss(
                                    pair=symbol,
                                    side=trade["direction"],
                                    loss_pct=abs(trade["pnl_pct"]),
                                    loss_usdt=abs(trade["pnl_usd"]),
                                )
                            else:
                                notify_exit(
                                    pair=symbol,
                                    side=trade["direction"],
                                    profit_pct=trade["pnl_pct"],
                                    profit_usdt=trade["pnl_usd"],
                                    exit_reason=reason,
                                    duration="N/A",
                                    confidence=trade.get("confidence", 0.5),
                                )

            # ----------------------------------------------------------
            # Layer 3: Run BB Squeeze Strategy
            # ----------------------------------------------------------
            signal = None
            try:
                signal = squeeze_strategy.evaluate(df)
            except Exception as exc:
                log.error("  策略錯誤: %s", exc)

            if signal and signal.direction != SignalDirection.NEUTRAL:
                dir_zh = "做多" if signal.direction == SignalDirection.LONG else "做空"
                log.info("  >> 策略訊號: %s (信心: %.0f%%)", dir_zh, signal.confidence * 100)
                log.info("     原因: %s", signal.reason_zh)

                pos = open_position(
                    state=state,
                    symbol=symbol,
                    direction=signal.direction.value,
                    entry_price=signal.entry_price or current_price,
                    stop_loss=signal.stop_loss or current_price * 0.97,
                    take_profit_levels=signal.take_profit_levels,
                    strategy=signal.strategy.value,
                    reason=signal.reason_zh,
                    confidence=signal.confidence,
                )

                if pos:
                    action = (
                        f"開倉 {symbol} {dir_zh} @ ${pos['entry_price']:,.2f} | "
                        f"SL: ${pos['stop_loss']:,.2f} | "
                        f"倉位: ${pos['position_size_usd']:.2f}"
                    )
                    log.info("  >> %s", action)
                    scan_log["actions"].append(action)
                    if _TG_AVAILABLE:
                        notify_entry(
                            pair=symbol,
                            side=pos["direction"],
                            rate=pos["entry_price"],
                            stake=pos["position_size_usd"],
                            leverage=pos.get("leverage", 1),
                            confidence=pos.get("confidence", 0.5),
                            reason=pos.get("reason", "BB Squeeze 突破"),
                        )
                else:
                    log.info("  已有持倉或達最大倉位限制，跳過")
            else:
                log.debug("  無策略訊號")

            scan_log["scans"].append(
                {
                    "symbol": symbol,
                    "price": round(current_price, 8),
                    "structure": structure.state.value,
                    "confidence": round(structure.confidence, 4),
                    "choch": structure.choch_detected,
                    "signal": signal.direction.value if signal else None,
                    "signal_confidence": round(signal.confidence, 4) if signal else None,
                }
            )

        except Exception as e:
            log.error("  %s 掃描失敗: %s", symbol, e)
            scan_log["scans"].append({"symbol": symbol, "error": str(e)})

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    state["scan_history"].append(scan_log)
    # Keep only last 168 scans (1 week of hourly)
    state["scan_history"] = state["scan_history"][-168:]

    total_pnl = state["capital"] - state["initial_capital"]
    total_pnl_pct = (total_pnl / state["initial_capital"]) * 100
    total_trades = len(state["closed_trades"])
    wins = sum(1 for t in state["closed_trades"] if t["pnl_usd"] > 0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    log.info(
        "摘要 — 資金: $%.2f (初始: $%.2f) | 損益: $%+.2f (%+.2f%%) | 開倉: %d | 已平倉: %d (勝率: %.0f%%)",
        state["capital"], state["initial_capital"],
        total_pnl, total_pnl_pct,
        len(state["open_positions"]), total_trades, win_rate,
    )
    for p in state["open_positions"]:
        dir_zh = "多" if p["direction"] == "long" else "空"
        log.info(
            "  持倉: %s %s @ $%,.2f | SL: $%,.2f",
            p["symbol"], dir_zh, p["entry_price"], p["stop_loss"],
        )

    # Save state (JSON)
    save_state(state)
    log.info("狀態已儲存至 %s", STATE_FILE)

    # Save capital snapshot to Supabase
    if _trade_store and _trade_store.has_supabase:
        try:
            _trade_store.save_capital_snapshot(
                capital=state["capital"],
                equity=state["capital"],
                open_positions=len(state["open_positions"]),
                source="scanner",
            )
            log.info("資本快照已儲存至 Supabase")
        except Exception as e:
            log.warning("Supabase 儲存快照失敗: %s", e)


if __name__ == "__main__":
    run_scan()
