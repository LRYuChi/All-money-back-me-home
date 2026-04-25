"""Supertrend strategy dashboard endpoints — R55.

Reads the JSONL trade journal that SupertrendStrategy writes (round 46
+ subsequent rounds) and exposes current performance + recent trades
+ live regime state.

Endpoints (all read-only):

  GET /api/supertrend/snapshot?days=7
    → PerformanceSnapshot JSON for the window

  GET /api/supertrend/regime
    → Current MarketRegime classification + 3 indicator values

  GET /api/supertrend/trades?limit=50
    → Recent exit events as a list (most-recent first), full event
      payload including SL plan / TP plan / multi-TF state at exit

  GET /api/supertrend/health
    → Lightweight liveness for the strategy chain itself (last journal
      write timestamp + journal directory accessible)

All endpoints are best-effort: failures return graceful empty payloads
with `error` field rather than 500s — useful for keeping the dashboard
alive when the journal is empty / unmounted.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/supertrend", tags=["supertrend"])


def _resolve_journal_dir() -> Path:
    """Honor SUPERTREND_JOURNAL_DIR env, else default."""
    env = os.environ.get("SUPERTREND_JOURNAL_DIR", "").strip()
    if env:
        return Path(env)
    # Default matches strategies/supertrend.py SUPERTREND_JOURNAL_DIR fallback
    # In container deploys this maps to /freqtrade/trading_log/journal
    candidates = [
        Path("/freqtrade/trading_log/journal"),
        Path("trading_log/journal"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]   # default even if missing


# =================================================================== #
# /snapshot
# =================================================================== #
@router.get("/snapshot")
def supertrend_snapshot(
    days: int = Query(7, ge=1, le=365, description="Window in days"),
) -> dict[str, Any]:
    """Performance snapshot over last N days."""
    try:
        from strategies.journal import TradeJournal
        from strategies.performance import PerformanceAggregator
    except ImportError as e:
        return {"error": f"strategy modules not importable: {e}"}

    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return {
            "error": f"journal directory missing: {journal_dir}",
            "n_trades": 0,
        }

    try:
        journal = TradeJournal(journal_dir)
        agg = PerformanceAggregator(journal)
        now = datetime.now(timezone.utc)
        snap = agg.snapshot(
            from_date=now - timedelta(days=days),
            to_date=now,
        )
        return asdict(snap)
    except Exception as e:
        logger.exception("supertrend_snapshot failed")
        return {"error": str(e), "n_trades": 0}


# =================================================================== #
# /regime
# =================================================================== #
@router.get("/regime")
def supertrend_regime() -> dict[str, Any]:
    """Current market regime — calls into MarketRegimeDetector with
    BTC daily candles fetched via ccxt directly (we can't reuse Freqtrade's
    DataProvider from outside the strategy)."""
    try:
        import ccxt
        from strategies.market_regime import (
            MarketRegimeDetector,
            classify_regime,
            compute_adx_30d_median,
            compute_atr_price_ratio,
            compute_hurst_exponent,
        )
    except ImportError as e:
        return {"error": f"regime module not importable: {e}"}

    try:
        ex = ccxt.okx({"enableRateLimit": True})
        # 200 daily bars to give Hurst enough lookback
        ohlcv = ex.fetch_ohlcv("BTC/USDT:USDT", timeframe="1d", limit=200)
        if not ohlcv or len(ohlcv) < 50:
            return {"error": "insufficient OHLCV from OKX"}
        import pandas as pd
        df = pd.DataFrame(
            ohlcv, columns=["ts", "open", "high", "low", "close", "volume"],
        )
    except Exception as e:
        logger.warning("regime fetch BTC OHLCV failed: %s", e)
        return {"error": f"BTC fetch failed: {e}"}

    try:
        atr = compute_atr_price_ratio(df)
        adx = compute_adx_30d_median(df)
        hurst = compute_hurst_exponent(df)
        regime = classify_regime(atr, adx, hurst)
        return {
            "regime": regime.value,
            "atr_price_ratio": round(atr, 6),
            "adx_30d_median": round(adx, 2),
            "hurst_exponent": round(hurst, 3),
            "btc_price": float(df["close"].iloc[-1]),
            "sample_size_days": len(df),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.exception("regime computation failed")
        return {"error": str(e)}


# =================================================================== #
# /trades
# =================================================================== #
@router.get("/trades")
def supertrend_trades(
    limit: int = Query(50, ge=1, le=500),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Recent exit events (full payload, newest first)."""
    try:
        from strategies.journal import TradeJournal
    except ImportError as e:
        return {"error": str(e), "trades": []}

    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return {"trades": [], "error": f"journal missing: {journal_dir}"}

    try:
        journal = TradeJournal(journal_dir)
        now = datetime.now(timezone.utc)
        rows = journal.read_range(
            from_date=now - timedelta(days=days),
            to_date=now,
        )
        # Just exits, newest first
        exits = [r for r in rows if r.get("event_type") == "exit"]
        exits.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return {
            "trades": exits[:limit],
            "n_total_exits_in_window": len(exits),
            "window_days": days,
        }
    except Exception as e:
        logger.exception("supertrend_trades failed")
        return {"error": str(e), "trades": []}


# =================================================================== #
# /health
# =================================================================== #
@router.get("/health")
def supertrend_health() -> dict[str, Any]:
    """Liveness for the journal chain.

    Returns:
      ok=True if journal dir exists AND has events in last 24h
      ok=False with diagnostic info otherwise
    """
    journal_dir = _resolve_journal_dir()
    out: dict[str, Any] = {
        "journal_dir": str(journal_dir),
        "journal_dir_exists": journal_dir.exists(),
    }

    if not journal_dir.exists():
        out["ok"] = False
        out["reason"] = "journal directory does not exist"
        return out

    # Find newest event timestamp
    try:
        from strategies.journal import TradeJournal
        journal = TradeJournal(journal_dir)
        rows = journal.read_range(
            from_date=datetime.now(timezone.utc) - timedelta(days=7),
        )
        if not rows:
            out["ok"] = False
            out["reason"] = "no events in last 7 days"
            out["last_event_ts"] = None
            return out
        latest = max(r.get("timestamp", "") for r in rows)
        out["last_event_ts"] = latest
        out["events_last_7d"] = len(rows)

        # Stale check
        try:
            latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            stale = (datetime.now(timezone.utc) - latest_dt) > timedelta(hours=24)
            out["ok"] = not stale
            if stale:
                out["reason"] = "no events in last 24h"
        except Exception:
            out["ok"] = True   # we have events, ts unparseable but exists
        return out
    except Exception as e:
        out["ok"] = False
        out["reason"] = f"journal read failed: {e}"
        return out
