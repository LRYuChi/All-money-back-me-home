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
# /skipped — R61 — entries that didn't execute (filter activity audit)
# =================================================================== #
# Tracked event_types in the "skipped" family:
_SKIPPED_TYPES = ("skipped", "circuit_breaker")


def _categorize_skip_reason(reason: str) -> str:
    """Map a free-text reason into a small fixed bucket for grouping.
    Matches the prefixes the strategy emits in R57/R58/R48/P1-4/CB code paths."""
    r = (reason or "").lower()
    if r.startswith("r57") or "fr contra-signal" in r or "orderbook" in r:
        return "alpha_filter"
    if r.startswith("r58") or "correlation" in r:
        return "correlation"
    if r.startswith("regime:") or "regime" in r:
        return "regime"
    if "direction_concentration" in r:
        return "direction_concentration"
    if "circuit" in r or "breaker" in r or "cb" in r.split():
        return "circuit_breaker"
    return "other"


@router.get("/skipped")
def supertrend_skipped(
    limit: int = Query(50, ge=1, le=500),
    days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """Recent skipped/blocked entry attempts — operator visibility for
    "bot running but no trades, why?".

    Returns:
      events            — newest-first list (capped to `limit`)
      n_total_in_window — full count regardless of limit
      by_category       — counts grouped by bucketed reason
      by_pair           — counts grouped by pair (top 10)
      window_days       — echoed back
    """
    try:
        from strategies.journal import TradeJournal
    except ImportError as e:
        return {"error": str(e), "events": []}

    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return {"events": [], "error": f"journal missing: {journal_dir}"}

    try:
        journal = TradeJournal(journal_dir)
        now = datetime.now(timezone.utc)
        rows = journal.read_range(
            from_date=now - timedelta(days=days),
            to_date=now,
        )
        skipped = [
            r for r in rows
            if r.get("event_type") in _SKIPPED_TYPES
        ]
        skipped.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

        # Group by bucketed reason
        by_cat: dict[str, int] = {}
        by_pair: dict[str, int] = {}
        for r in skipped:
            # CB events have no `reason` field — categorize by event_type
            if r.get("event_type") == "circuit_breaker":
                cat = "circuit_breaker"
            else:
                cat = _categorize_skip_reason(r.get("reason", ""))
            by_cat[cat] = by_cat.get(cat, 0) + 1
            p = r.get("pair", "?")
            by_pair[p] = by_pair.get(p, 0) + 1

        # Top 10 pairs only — full list would be noisy
        top_pairs = dict(
            sorted(by_pair.items(), key=lambda kv: kv[1], reverse=True)[:10]
        )

        return {
            "events": skipped[:limit],
            "n_total_in_window": len(skipped),
            "by_category": by_cat,
            "by_pair": top_pairs,
            "window_days": days,
        }
    except Exception as e:
        logger.exception("supertrend_skipped failed")
        return {"error": str(e), "events": []}


# =================================================================== #
# /evaluations — R66 — aggregated entry-tier failure reasons
# =================================================================== #
@router.get("/evaluations")
def supertrend_evaluations(
    days: int = Query(1, ge=1, le=30),
    pair: str | None = Query(None, description="filter to single pair"),
    tier: str = Query("all", pattern="^(all|confirmed|scout|pre_scout)$"),
) -> dict[str, Any]:
    """Aggregate R66 EvaluationEvent failure reasons.

    Default 1-day window. Returns:
      n_evaluations    — total evaluation events in window
      n_pairs          — distinct pairs evaluated
      tier_fired_count — {confirmed: N, scout: N, pre_scout: N} (positive scans)
      failures_top     — {reason: count} for selected tier, sorted desc
      pairs_evaluated  — list of pairs seen (top 30 by event count)
      window_days      — echo

    Best-effort: empty + error field on failure, never 500.
    """
    try:
        from strategies.journal import TradeJournal
    except ImportError as e:
        return {"error": str(e), "n_evaluations": 0}

    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return {"n_evaluations": 0,
                "error": f"journal missing: {journal_dir}"}

    try:
        journal = TradeJournal(journal_dir)
        now = datetime.now(timezone.utc)
        rows = journal.read_range(
            from_date=now - timedelta(days=days), to_date=now,
        )
        evals = [r for r in rows if r.get("event_type") == "evaluation"]
        if pair:
            evals = [r for r in evals if r.get("pair") == pair]

        tier_fired = {"confirmed": 0, "scout": 0, "pre_scout": 0}
        failure_counter: dict[str, int] = {}
        pair_counter: dict[str, int] = {}

        tiers_to_count = (
            ["confirmed", "scout", "pre_scout"] if tier == "all" else [tier]
        )

        for ev in evals:
            for t in ("confirmed", "scout", "pre_scout"):
                if ev.get(f"{t}_fired"):
                    tier_fired[t] += 1
            for t in tiers_to_count:
                for f in (ev.get(f"{t}_failures") or []):
                    failure_counter[f] = failure_counter.get(f, 0) + 1
            p = ev.get("pair", "?")
            pair_counter[p] = pair_counter.get(p, 0) + 1

        failures_sorted = dict(
            sorted(failure_counter.items(), key=lambda kv: kv[1], reverse=True)[:30]
        )
        pairs_top = dict(
            sorted(pair_counter.items(), key=lambda kv: kv[1], reverse=True)[:30]
        )
        return {
            "n_evaluations": len(evals),
            "n_pairs": len(pair_counter),
            "tier_fired_count": tier_fired,
            "failures_top": failures_sorted,
            "pairs_evaluated": pairs_top,
            "tier_filter": tier,
            "pair_filter": pair,
            "window_days": days,
        }
    except Exception as e:
        logger.exception("supertrend_evaluations failed")
        return {"error": str(e), "n_evaluations": 0}


# =================================================================== #
# /scanner — R62 — per-pair signal proximity dashboard
# =================================================================== #
# Indicator columns we extract from each pair's last analyzed candle.
# All populated by SupertrendStrategy.populate_indicators in production.
_SCANNER_FIELDS = (
    "st_1d", "st_1d_duration", "dir_4h_score",
    "st_1h", "st_trend",
    "direction_score", "trend_quality",
    "adx", "atr", "funding_rate",
    "pair_bullish_2tf", "pair_bearish_2tf",
)


def _ft_auth_headers() -> dict[str, str]:
    """Build Basic-auth header for freqtrade REST."""
    import base64
    user = os.environ.get("FT_USER", "freqtrade")
    pwd = os.environ.get("FT_PASS", "freqtrade")
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _ft_api_url() -> str:
    return os.environ.get(
        "FREQTRADE_API_URL", "http://freqtrade:8080",
    ).rstrip("/")


def _ft_get(path: str, *, timeout: float = 5.0) -> Any:
    """GET freqtrade REST endpoint, raises on HTTP error. JSON-decoded."""
    import json as _json
    import urllib.request
    req = urllib.request.Request(
        f"{_ft_api_url()}{path}", headers=_ft_auth_headers(),
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode())


def _extract_last_row(candle_resp: dict) -> dict[str, Any]:
    """freqtrade /pair_candles returns {"data": [[...], ...], "columns": [...]}.
    Pull the last row, return a name→value dict for our SCANNER_FIELDS."""
    cols = candle_resp.get("columns") or []
    rows = candle_resp.get("data") or []
    if not rows or not cols:
        return {}
    last = rows[-1]
    out: dict[str, Any] = {}
    for f in _SCANNER_FIELDS:
        if f in cols:
            try:
                v = last[cols.index(f)]
                # Coerce to JSON-friendly primitives
                if isinstance(v, (int, float)) or v is None:
                    out[f] = v
                else:
                    out[f] = float(v) if v != "" else None
            except (ValueError, IndexError, TypeError):
                out[f] = None
    return out


def _alignment_count(state: dict) -> int:
    """Count how many of the 4 timeframes agree on direction.
    Returns 0..4. Used to surface near-firing pairs at the top."""
    st_1d = state.get("st_1d")
    st_4h = state.get("dir_4h_score")
    st_1h = state.get("st_1h")
    st_15m = state.get("st_trend")
    # 4h is a continuous score [-1, 1], discretize at ±0.25 (modest bias)
    sig_4h = 1 if (st_4h is not None and st_4h > 0.25) else (
        -1 if (st_4h is not None and st_4h < -0.25) else 0
    )
    longs = sum(1 for x in (st_1d, sig_4h, st_1h, st_15m) if x == 1)
    shorts = sum(1 for x in (st_1d, sig_4h, st_1h, st_15m) if x == -1)
    return max(longs, shorts)


def _likely_side(state: dict) -> str | None:
    """Direction the pair is leaning toward, or None if neutral."""
    ds = state.get("direction_score")
    if ds is None:
        return None
    if ds > 0.25:
        return "long"
    if ds < -0.25:
        return "short"
    return None


@router.get("/scanner")
def supertrend_scanner(
    timeframe: str = Query("15m", pattern="^(1m|5m|15m|1h|4h|1d)$"),
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    """Per-pair signal proximity — multi-tf state of every whitelist pair.

    Sorted by alignment_count desc, then |direction_score| desc, so the
    pairs closest to firing land at the top.

    Returns:
      pairs        — list of {pair, alignment_count, likely_side, ...indicators}
      n_pairs      — total whitelist size
      timeframe    — echoed back
      fetched_at   — ISO timestamp
      errors       — per-pair fetch failures (empty when all succeed)

    Best-effort: any failure returns empty list + error field, never 500.
    """
    out: dict[str, Any] = {
        "pairs": [],
        "timeframe": timeframe,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": {},
    }

    try:
        wl = _ft_get("/api/v1/whitelist")
    except Exception as e:
        return {**out, "error": f"freqtrade /whitelist unreachable: {e}"}

    pairs_raw = wl.get("whitelist") or []
    out["n_pairs"] = len(pairs_raw)

    # Fetch each pair's last candle. Sequential for simplicity — 30 pairs ×
    # ~50ms = 1.5s, acceptable for an ops endpoint refreshed manually.
    rows: list[dict[str, Any]] = []
    for pair in pairs_raw[:limit]:
        try:
            cresp = _ft_get(
                f"/api/v1/pair_candles?pair={pair}&timeframe={timeframe}&limit=1",
                timeout=3.0,
            )
            indicators = _extract_last_row(cresp)
        except Exception as e:
            out["errors"][pair] = str(e)
            continue
        if not indicators:
            out["errors"][pair] = "no candle data"
            continue
        rows.append({
            "pair": pair,
            "alignment_count": _alignment_count(indicators),
            "likely_side": _likely_side(indicators),
            **indicators,
        })

    # Surface near-firing pairs first
    rows.sort(
        key=lambda r: (
            r["alignment_count"],
            abs(r.get("direction_score") or 0.0),
        ),
        reverse=True,
    )
    out["pairs"] = rows
    return out


# =================================================================== #
# /operations — R68 — unified ops snapshot
# =================================================================== #
def _ft_show_config_state() -> dict:
    """Quick freqtrade /show_config probe — never raises."""
    try:
        return _ft_get("/api/v1/show_config", timeout=3.0)
    except Exception:
        return {}


def _ft_whitelist_size() -> int:
    try:
        return len((_ft_get("/api/v1/whitelist", timeout=3.0) or {}).get("whitelist", []))
    except Exception:
        return -1


def _build_ops_alerts(
    bot_state: str, n_pairs: int, eval_summary: dict,
    health: dict, recent_trades: int, journal_ok: bool,
) -> list[str]:
    """Compose the 'should I do something' list. Empty = all OK.

    Each alert is a short imperative string that points at a known
    failure mode the operator should investigate.
    """
    alerts: list[str] = []

    if bot_state and bot_state != "running":
        alerts.append(
            f"BOT_STATE={bot_state} — issue POST /api/v1/start (R60 autostart "
            f"sidecar should fix within 30s if container rebooted)"
        )

    if n_pairs == 0:
        alerts.append(
            "WHITELIST_EMPTY — VolumePairList init failed; check freqtrade "
            "logs for OKX-not-supported / refresh_period errors (R63)"
        )
    elif 0 < n_pairs < 5:
        alerts.append(
            f"WHITELIST_THIN ({n_pairs} pairs) — pairlist filters may be "
            "too aggressive; review AgeFilter / SpreadFilter / VolatilityFilter"
        )

    if not journal_ok:
        reason = health.get("reason", "unknown")
        alerts.append(f"JOURNAL_STALE — {reason}")

    # Pipeline pressure check: if 0 fires + lots of evaluations, flag the
    # dominant blocker so operator can see "is this market chop or strategy bug?"
    fires = eval_summary.get("tier_fired_count", {})
    n_fires = sum(fires.values()) if fires else 0
    n_evals = eval_summary.get("n_evaluations", 0)
    if n_fires == 0 and n_evals >= 50:
        top = next(iter((eval_summary.get("failures_top") or {}).items()), None)
        if top:
            alerts.append(
                f"NO_FIRES_24H — {n_evals} evaluations, dominant blocker: "
                f"{top[0]} ({top[1]} hits). Likely market regime mismatch, "
                "not a code bug — see /api/supertrend/evaluations for full breakdown"
            )

    if recent_trades == 0 and n_evals == 0:
        alerts.append(
            "NO_PIPELINE_ACTIVITY — bot may not be running populate_entry_trend; "
            "verify SUPERTREND_EVAL_JOURNAL=1 + freqtrade INFO logs"
        )

    return alerts


@router.get("/operations")
def supertrend_operations(
    eval_window_days: int = Query(1, ge=1, le=7),
    perf_window_days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """One-stop operations snapshot — composes outputs from the 6 other
    endpoints + computes actionable alerts.

    Single GET = full system status. Bookmark-friendly for ops dashboards.
    Best-effort end-to-end: any sub-component failure populates errors[]
    and the rest still renders.
    """
    out: dict[str, Any] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": {},
    }

    # ---- bot state (freqtrade /show_config) ---- #
    cfg = _ft_show_config_state()
    bot_state = str(cfg.get("state", "unknown"))
    out["bot"] = {
        "state": bot_state,
        "dry_run": cfg.get("dry_run"),
        "strategy": cfg.get("strategy"),
        "max_open_trades": cfg.get("max_open_trades"),
    }
    if not cfg:
        out["errors"]["bot"] = "freqtrade /show_config unreachable"

    # ---- whitelist size ---- #
    n_pairs = _ft_whitelist_size()
    out["whitelist"] = {"n_pairs": n_pairs if n_pairs >= 0 else None}
    if n_pairs < 0:
        out["errors"]["whitelist"] = "freqtrade /whitelist unreachable"

    # ---- env switchboard (read-only view of what's enabled) ---- #
    out["switchboard"] = {
        "regime_filter": os.environ.get("SUPERTREND_REGIME_FILTER", "1"),
        "kelly_mode": os.environ.get("SUPERTREND_KELLY_MODE", "three_stage"),
        "exit_mode": os.environ.get("SUPERTREND_EXIT_MODE", "weighted"),
        "fr_alpha": os.environ.get("SUPERTREND_FR_ALPHA", "0"),
        "orderbook_confirm": os.environ.get("SUPERTREND_ORDERBOOK_CONFIRM", "0"),
        "correlation_filter": os.environ.get("SUPERTREND_CORRELATION_FILTER", "0"),
        "eval_journal": os.environ.get("SUPERTREND_EVAL_JOURNAL", "1"),
        "live_mode": os.environ.get("SUPERTREND_LIVE", "0"),
    }

    # ---- pipeline activity from journal ---- #
    journal_dir = _resolve_journal_dir()
    journal_ok = journal_dir.exists()
    health: dict[str, Any] = {"ok": False}
    eval_summary: dict[str, Any] = {}
    n_recent_trades = 0
    n_recent_skipped = 0
    last_event_ts = None
    if journal_ok:
        try:
            from strategies.journal import TradeJournal
            journal = TradeJournal(journal_dir)
            now = datetime.now(timezone.utc)
            rows = journal.read_range(
                from_date=now - timedelta(days=eval_window_days), to_date=now,
            )
            evals = [r for r in rows if r.get("event_type") == "evaluation"]
            tier_fires = {"confirmed": 0, "scout": 0, "pre_scout": 0}
            failure_counter: dict[str, int] = {}
            for ev in evals:
                for t in ("confirmed", "scout", "pre_scout"):
                    if ev.get(f"{t}_fired"):
                        tier_fires[t] += 1
                    for f in (ev.get(f"{t}_failures") or []):
                        failure_counter[f] = failure_counter.get(f, 0) + 1
            eval_summary = {
                "n_evaluations": len(evals),
                "tier_fired_count": tier_fires,
                "failures_top": dict(
                    sorted(failure_counter.items(), key=lambda kv: kv[1],
                           reverse=True)[:5]
                ),
            }
            n_recent_trades = sum(
                1 for r in rows if r.get("event_type") == "exit"
            )
            n_recent_skipped = sum(
                1 for r in rows
                if r.get("event_type") in ("skipped", "circuit_breaker")
            )
            if rows:
                last_event_ts = max(r.get("timestamp", "") for r in rows)
                health = {
                    "ok": True, "last_event_ts": last_event_ts,
                    "events_in_window": len(rows),
                }
            else:
                health = {
                    "ok": False, "reason": f"no events in last {eval_window_days}d",
                }
        except Exception as e:
            out["errors"]["pipeline"] = str(e)
            health = {"ok": False, "reason": f"journal read failed: {e}"}
    else:
        health = {"ok": False, "reason": f"journal missing: {journal_dir}"}

    out["pipeline"] = {
        "journal_ok": journal_ok,
        "health": health,
        "evaluations": eval_summary,
        "recent_trades": n_recent_trades,
        "recent_skipped": n_recent_skipped,
    }

    # ---- performance snapshot (delegate via journal) ---- #
    if journal_ok:
        try:
            from strategies.journal import TradeJournal
            from strategies.performance import PerformanceAggregator
            journal = TradeJournal(journal_dir)
            agg = PerformanceAggregator(journal)
            now = datetime.now(timezone.utc)
            snap = agg.snapshot(
                from_date=now - timedelta(days=perf_window_days),
                to_date=now,
            )
            out["performance"] = {
                "window_days": perf_window_days,
                "n_trades": snap.n_trades,
                "win_rate": snap.win_rate,
                "sum_pnl_usd": snap.sum_pnl_usd,
                "max_drawdown_usd": snap.max_drawdown_usd,
            }
        except Exception as e:
            out["errors"]["performance"] = str(e)

    # ---- composed actionable alerts ---- #
    out["alerts"] = _build_ops_alerts(
        bot_state=bot_state,
        n_pairs=n_pairs if n_pairs >= 0 else 0,
        eval_summary=eval_summary,
        health=health,
        recent_trades=n_recent_trades,
        journal_ok=journal_ok,
    )
    out["alert_count"] = len(out["alerts"])
    out["status"] = "ok" if not out["alerts"] else "degraded"
    return out


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
