"""Strategy API router — market structure, signals, and full analysis pipeline.

Provides endpoints for crypto market structure analysis, strategy signal
generation, and the multi-layer analysis pipeline (Phase 1: Layer 1 + Layer 2
+ BB Squeeze).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("ambmh.strategy")
import pandas as pd

from src.fetchers.crypto import CryptoFetcher
from strategy.layer1_market_structure import (
    classify_market_state,
    detect_swing_highs,
    detect_swing_lows,
)
from strategy.layer2_signal_engine.volatility_indicators import evaluate_volatility_signals
from strategy.layer2_signal_engine.trend_indicators import evaluate_trend_signals
from strategy.layer3_strategies.strategy_b_squeeze import BBSqueezeStrategy

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

_crypto_fetcher = CryptoFetcher()
_bb_squeeze = BBSqueezeStrategy()

# Supported timeframes for validation
_VALID_TIMEFRAMES = {"1h", "4h", "1d", "1wk", "1mo"}

# Map timeframe to a sensible default period for data fetching
_TIMEFRAME_PERIOD: dict[str, str] = {
    "1h": "1mo",
    "4h": "3mo",
    "1d": "6mo",
    "1wk": "1y",
    "1mo": "2y",
}


def _normalize_symbol(raw: str) -> str:
    """Normalize user-supplied symbol to ccxt format (e.g. BTC/USDT).

    Accepts formats like:
    - BTC-USDT  ->  BTC/USDT
    - BTCUSDT   ->  BTC/USDT
    - BTC/USDT  ->  BTC/USDT  (pass-through)
    """
    symbol = raw.strip().upper()

    # Already in ccxt format
    if "/" in symbol:
        return symbol

    # Dash-separated
    if "-" in symbol:
        parts = symbol.split("-", 1)
        return f"{parts[0]}/{parts[1]}"

    # Concatenated: try common quote currencies
    for quote in ("USDT", "USD", "BTC", "ETH", "BUSD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}/{quote}"

    # Fallback: assume USDT pair
    return f"{symbol}/USDT"


def _fetch_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV data, raising HTTPException on failure."""
    ccxt_symbol = _normalize_symbol(symbol)
    period = _TIMEFRAME_PERIOD.get(timeframe, "6mo")

    try:
        df = _crypto_fetcher.fetch_ohlcv(
            symbol=ccxt_symbol,
            interval=timeframe,
            period=period,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch data for {ccxt_symbol}: {exc}",
        ) from exc

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data returned for {ccxt_symbol} on timeframe {timeframe}",
        )

    return df


def _validate_timeframe(timeframe: str) -> None:
    """Raise HTTPException if timeframe is unsupported."""
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported timeframe '{timeframe}'. Choose from: {', '.join(sorted(_VALID_TIMEFRAMES))}",
        )


@router.get("/crypto/{symbol}/structure")
async def get_market_structure(
    symbol: str,
    timeframe: str = Query(default="1d", description="Timeframe: 1h, 4h, 1d, 1wk, 1mo"),
) -> dict[str, Any]:
    """Get market structure (Swing H/L, trend state, CHoCH) for a crypto symbol."""
    _validate_timeframe(timeframe)
    df = _fetch_ohlcv(symbol, timeframe)

    try:
        swing_highs = detect_swing_highs(df, lookback=5)
        swing_lows = detect_swing_lows(df, lookback=5)
        structure = classify_market_state(swing_highs, swing_lows)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Market structure analysis failed: {exc}",
        ) from exc

    return {
        "symbol": _normalize_symbol(symbol),
        "timeframe": timeframe,
        "state": structure.state.value,
        "choch_detected": structure.choch_detected,
        "choch_direction": structure.choch_direction.value if structure.choch_direction else None,
        "confidence": structure.confidence,
        "swing_highs": [sp.model_dump() for sp in structure.swing_highs],
        "swing_lows": [sp.model_dump() for sp in structure.swing_lows],
    }


@router.get("/crypto/{symbol}/signals")
async def get_strategy_signals(
    symbol: str,
    timeframe: str = Query(default="1d", description="Timeframe: 1h, 4h, 1d, 1wk, 1mo"),
) -> dict[str, Any]:
    """Get active strategy signals for a crypto symbol."""
    _validate_timeframe(timeframe)
    df = _fetch_ohlcv(symbol, timeframe)

    # Market structure context
    try:
        swing_highs = detect_swing_highs(df, lookback=5)
        swing_lows = detect_swing_lows(df, lookback=5)
        structure = classify_market_state(swing_highs, swing_lows)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Market structure analysis failed: {exc}",
        ) from exc

    # Run BB Squeeze strategy (Phase 1)
    signals: list[dict[str, Any]] = []
    try:
        bb_signal = _bb_squeeze.evaluate(df)
        if bb_signal is not None:
            signals.append(bb_signal.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("BB Squeeze evaluate failed for %s: %s", symbol, exc)

    return {
        "symbol": _normalize_symbol(symbol),
        "timeframe": timeframe,
        "market_state": structure.state.value,
        "signals": signals,
    }


@router.post("/crypto/{symbol}/analyze")
async def full_analysis(
    symbol: str,
    timeframe: str = Query(default="1d", description="Timeframe: 1h, 4h, 1d, 1wk, 1mo"),
) -> dict[str, Any]:
    """Run full 5-layer analysis pipeline for a crypto symbol.

    Phase 1 implementation covers:
    - Layer 1: Market structure (swing points, trend state, CHoCH)
    - Layer 2: Volatility + trend indicator signals
    - Layer 3: BB Squeeze strategy signal
    """
    _validate_timeframe(timeframe)
    df = _fetch_ohlcv(symbol, timeframe)
    ccxt_symbol = _normalize_symbol(symbol)

    # Layer 1: Market structure
    try:
        swing_highs = detect_swing_highs(df, lookback=5)
        swing_lows = detect_swing_lows(df, lookback=5)
        structure = classify_market_state(swing_highs, swing_lows)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Market structure analysis failed: {exc}",
        ) from exc

    structure_result = {
        "state": structure.state.value,
        "choch_detected": structure.choch_detected,
        "choch_direction": structure.choch_direction.value if structure.choch_direction else None,
        "confidence": structure.confidence,
        "swing_highs": [sp.model_dump() for sp in structure.swing_highs],
        "swing_lows": [sp.model_dump() for sp in structure.swing_lows],
    }

    # Layer 2: Indicator signals
    indicator_signals: list[dict[str, Any]] = []
    try:
        trend_signals = evaluate_trend_signals(df)
        indicator_signals.extend(s.model_dump() for s in trend_signals)
    except Exception as exc:
        logger.warning("Trend indicators failed for %s: %s", symbol, exc)

    try:
        vol_signals = evaluate_volatility_signals(df)
        indicator_signals.extend(s.model_dump() for s in vol_signals)
    except Exception as exc:
        logger.warning("Volatility indicators failed for %s: %s", symbol, exc)

    # Layer 3: Strategy signals (BB Squeeze for Phase 1)
    strategy_signals: list[dict[str, Any]] = []
    try:
        bb_signal = _bb_squeeze.evaluate(df)
        if bb_signal is not None:
            strategy_signals.append(bb_signal.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("BB Squeeze strategy failed for %s: %s", symbol, exc)

    return {
        "symbol": ccxt_symbol,
        "timeframe": timeframe,
        "structure": structure_result,
        "indicators": indicator_signals,
        "signals": strategy_signals,
    }


from src.services.trade_store import TradeStore

_trade_store = TradeStore()


@router.get("/trades/paper")
async def get_paper_trades() -> dict[str, Any]:
    """Get all paper trading data (Supabase-first, JSON-fallback)."""
    state = _trade_store.get_all_trades(source="scanner")
    capital = state.get("capital", 0)
    initial = state.get("initial_capital", 0) or 300.0
    total_pnl = capital - initial
    closed = state.get("closed_trades", [])
    wins = sum(1 for t in closed if (t.get("pnl_usd") or 0) > 0)
    total = len(closed)
    return {
        "capital": capital,
        "initial_capital": initial,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / initial * 100, 2) if initial else 0,
        "open_positions": state.get("open_positions", []),
        "closed_trades": closed,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "total_trades": total,
        "last_updated": state.get("last_updated"),
    }


@router.get("/trades/paper/open")
async def get_open_positions() -> list[dict]:
    """Get currently open paper positions."""
    return _trade_store.get_open_positions(source="scanner")


@router.get("/trades/paper/closed")
async def get_closed_trades() -> list[dict]:
    """Get closed paper trades."""
    return _trade_store.get_closed_trades(source="scanner")


@router.get("/trades/paper/equity-curve")
async def get_equity_curve() -> list[dict]:
    """Get equity curve data points."""
    return _trade_store.get_equity_curve(source="scanner")


@router.get("/trades/paper/performance")
async def get_performance() -> dict[str, Any]:
    """Get paper trading performance summary."""
    closed = _trade_store.get_closed_trades(source="scanner")

    if not closed:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "total_pnl_usd": 0,
            "total_pnl_pct": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "profit_factor": 0,
        }

    wins = [t for t in closed if (t.get("pnl_usd") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_usd") or 0) <= 0]
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 0

    return {
        "total_trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "total_pnl_usd": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / 300.0 * 100, 2),
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "best_trade": round(max(t.get("pnl_usd", 0) for t in closed), 2),
        "worst_trade": round(min(t.get("pnl_usd", 0) for t in closed), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
    }


@router.get("/backtest/runs")
async def get_backtest_runs(
    symbol: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Get recent backtest run results."""
    return _trade_store.get_backtest_runs(symbol=symbol, limit=limit)
