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

from fastapi import APIRouter, Body, HTTPException, Query

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


def _ft_post(path: str, body: dict, *, timeout: float = 10.0) -> Any:
    """POST freqtrade REST endpoint with JSON body, raises on HTTP error."""
    import json as _json
    import urllib.request
    headers = _ft_auth_headers()
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{_ft_api_url()}{path}",
        data=_json.dumps(body).encode(),
        headers=headers,
        method="POST",
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


def _latest_eval_per_pair(window_hours: float = 1.0) -> dict[str, dict]:
    """Build a {pair: latest EvaluationEvent} map from journal.

    Looks back `window_hours` (default 1h ≈ 4 candles at 15m). Returns
    empty dict on any error — caller falls back gracefully.
    """
    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return {}
    try:
        from strategies.journal import TradeJournal
    except ImportError:
        return {}
    try:
        journal = TradeJournal(journal_dir)
        rows = journal.read_range(
            from_date=datetime.now(timezone.utc) - timedelta(hours=window_hours),
        )
    except Exception:
        return {}
    latest: dict[str, dict] = {}
    for r in rows:
        if r.get("event_type") != "evaluation":
            continue
        p = r.get("pair", "")
        if not p:
            continue
        ts = r.get("timestamp", "")
        prev = latest.get(p)
        if prev is None or ts > prev.get("timestamp", ""):
            latest[p] = r
    return latest


def _closest_to_fire(eval_event: dict | None) -> dict[str, Any] | None:
    """From an EvaluationEvent, find the tier with fewest unmet conditions.

    Returns:
      tier            — confirmed / scout / pre_scout / None
      remaining       — list of failure reasons still unmet for that tier
      fire_distance   — len(remaining); 0 means tier just fired
      already_fired   — bool: was that tier's _fired flag True?

    R110: skip tiers whose ONLY failure is a "*_disabled_*" sentinel.
    Pre-R110, R87 confirmed_disabled tier always reported fire_distance=1
    even though it can never actually fire — masking the REAL closest
    tier (scout / pre_scout) and misleading the operator into "just one
    condition away" when the truth is "this tier is permanently dead".

    None when input is None (no recent evaluation for this pair).
    """
    if not eval_event:
        return None

    def _tier_is_disabled(fails: list[str]) -> bool:
        """A tier is treated as never-firing when its sole remaining
        failure is the R87/R93 sentinel `*_disabled_R*`."""
        return len(fails) == 1 and "_disabled_" in (fails[0] or "")

    candidates = []
    for tier in ("confirmed", "scout", "pre_scout"):
        if eval_event.get(f"{tier}_fired") is True:
            return {
                "tier": tier, "remaining": [],
                "fire_distance": 0, "already_fired": True,
            }
        fails = eval_event.get(f"{tier}_failures") or []
        if _tier_is_disabled(fails):
            continue   # R110: don't surface a dead tier as "closest"
        candidates.append((tier, len(fails), list(fails)))
    if not candidates:
        # All tiers either fired or are permanently disabled. Surface
        # this state explicitly so the operator can see "all live tiers
        # exhausted" rather than getting None which looks like missing data.
        return {
            "tier": None, "remaining": ["all_tiers_disabled_or_fired"],
            "fire_distance": 999, "already_fired": False,
        }
    # Closest = tier with fewest failures
    candidates.sort(key=lambda c: c[1])
    tier, n, fails = candidates[0]
    return {
        "tier": tier, "remaining": fails,
        "fire_distance": n, "already_fired": False,
    }


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

    # R71: prefetch latest EvaluationEvent per pair (1 journal scan vs N)
    eval_by_pair = _latest_eval_per_pair(window_hours=1.0)

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
        ctf = _closest_to_fire(eval_by_pair.get(pair))
        rows.append({
            "pair": pair,
            "alignment_count": _alignment_count(indicators),
            "likely_side": _likely_side(indicators),
            "closest_to_fire": ctf,
            **indicators,
        })

    # R71: primary sort = fire_distance asc (closest first), tiebreak by
    # alignment_count desc + |direction_score| desc. Pairs without
    # eval_event sink to the bottom (fire_distance treated as +inf).
    def _sort_key(r):
        ctf = r.get("closest_to_fire")
        dist = ctf.get("fire_distance", 999) if ctf else 999
        return (
            dist,
            -(r.get("alignment_count") or 0),
            -abs(r.get("direction_score") or 0.0),
        )
    rows.sort(key=_sort_key)
    out["pairs"] = rows

    # R71: top-of-response summary — top 5 pairs nearest to firing
    near = []
    for r in rows[:5]:
        ctf = r.get("closest_to_fire")
        if not ctf:
            continue
        near.append({
            "pair": r["pair"],
            "tier": ctf["tier"],
            "fire_distance": ctf["fire_distance"],
            "remaining": ctf["remaining"],
            "likely_side": r.get("likely_side"),
        })
    out["pairs_near_fire"] = near
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
    observed_span_hours: float = 0.0,
    expected_evals_per_hour_per_pair: float = 4.0,
) -> list[str]:
    """Compose the 'should I do something' list. Empty = all OK.

    Each alert is a short imperative string that points at a known
    failure mode the operator should investigate.

    R75: observed_span_hours = oldest→newest event timestamp span in
    the eval window (proxy for actual bot uptime within window).
    expected_evals_per_hour_per_pair = 4 by default (15m timeframe →
    4 candle closes per hour). Used by EVAL_RATE_LOW rule.
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
    # R108: now also include actionable advice mapping the dominant
    # failure reason to the corresponding tunable env.
    fires = eval_summary.get("tier_fired_count", {})
    n_fires = sum(fires.values()) if fires else 0
    n_evals = eval_summary.get("n_evaluations", 0)
    if n_fires == 0 and n_evals >= 50:
        top = next(iter((eval_summary.get("failures_top") or {}).items()), None)
        if top:
            advice = _suggest_for_failure(top[0])
            alerts.append(
                f"NO_FIRES_24H — {n_evals} evaluations, dominant blocker: "
                f"{top[0]} ({top[1]} hits). {advice}"
            )

        # R110: STRONG_TREND_NO_FIRES — detect the R87 stuck pattern.
        # When confirmed_disabled_R87 is among the top failures AND
        # multi-tf alignment failures (all_bullish/all_bearish=False,
        # *_just_formed=False) are NOT dominant, the strategy is
        # observing strong trends but unable to enter because:
        #   - confirmed tier disabled by R87
        #   - scout/pre_scout require "just_formed" edge — alignment
        #     long ago = no edge to fire on
        # → operator may want to temporarily SUPERTREND_DISABLE_CONFIRMED=0
        #   (accept R85 confirmed-tier P&L profile to avoid being stuck)
        #   or wait for chop regime to give scout a fresh edge.
        failures = eval_summary.get("failures_top") or {}
        confirmed_disabled_hits = failures.get("confirmed_disabled_R87", 0)
        # Threshold: confirmed_disabled appears more than the dominant
        # alignment-not-yet-formed failure → we're in strong trend
        not_formed_hits = max(
            failures.get("bull_just_formed=False", 0),
            failures.get("bear_just_formed=False", 0),
            failures.get("pair_bullish_2tf_just_formed=False", 0),
            failures.get("pair_bearish_2tf_just_formed=False", 0),
        )
        if confirmed_disabled_hits >= 50 and confirmed_disabled_hits > not_formed_hits * 0.3:
            alerts.append(
                f"STRONG_TREND_NO_FIRES — {confirmed_disabled_hits} evaluations "
                f"reached confirmed-tier alignment but were blocked by R87 "
                f"disable_confirmed. scout/pre_scout require edge-trigger "
                f"(*_just_formed) which doesn't fire in mid-trend. Options: "
                f"(a) temporarily SUPERTREND_DISABLE_CONFIRMED=0 — accepts R85 "
                f"confirmed -0.84%/48% WR but lets bot trade strong trends, "
                f"(b) wait for chop regime, (c) implement regime-aware R87 toggle. "
                f"See /api/supertrend/scanner for which pairs are stuck."
            )

    if recent_trades == 0 and n_evals == 0:
        alerts.append(
            "NO_PIPELINE_ACTIVITY — bot may not be running populate_entry_trend; "
            "verify SUPERTREND_EVAL_JOURNAL=1 + freqtrade INFO logs"
        )

    # R75: eval rate sanity. Catches "evaluation writes silently failing"
    # — distinguishable from legitimately low counts due to short uptime
    # because we measure expected against the ACTUAL observed event
    # timespan (proxy for uptime in window), not a fixed 24h baseline.
    # Need ≥0.5h sample to avoid false-fires during fresh container starts.
    if observed_span_hours >= 0.5 and n_pairs > 0:
        expected = observed_span_hours * n_pairs * expected_evals_per_hour_per_pair
        if expected > 0 and (n_evals / expected) < 0.5:
            ratio_pct = (n_evals / expected) * 100
            alerts.append(
                f"EVAL_RATE_LOW — {n_evals} evals over {observed_span_hours:.1f}h "
                f"with {n_pairs} pairs, expected ≈{int(expected)} "
                f"({ratio_pct:.0f}% of baseline). Possible silent journal "
                f"write failures or populate_entry_trend exceptions; "
                f"check freqtrade logs for swallowed errors."
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
    # NOTE: these reads come from the API container's process env. For
    # this to faithfully reflect what's in effect inside the freqtrade
    # container, the same env vars must be exported to BOTH services in
    # docker-compose.prod.yml. R94 added entry-gate vars (R87/R89/R91)
    # to make "did my deploy actually take effect?" verifiable from /ops.
    out["switchboard"] = {
        # Risk / sizing
        "regime_filter": os.environ.get("SUPERTREND_REGIME_FILTER", "1"),
        "kelly_mode": os.environ.get("SUPERTREND_KELLY_MODE", "three_stage"),
        "exit_mode": os.environ.get("SUPERTREND_EXIT_MODE", "weighted"),
        "fr_alpha": os.environ.get("SUPERTREND_FR_ALPHA", "0"),
        "orderbook_confirm": os.environ.get("SUPERTREND_ORDERBOOK_CONFIRM", "0"),
        "correlation_filter": os.environ.get("SUPERTREND_CORRELATION_FILTER", "0"),
        "eval_journal": os.environ.get("SUPERTREND_EVAL_JOURNAL", "1"),
        "live_mode": os.environ.get("SUPERTREND_LIVE", "0"),
        # Entry gates — R87/R89/R91 (R94: surfaced for deploy verification)
        "disable_confirmed": os.environ.get("SUPERTREND_DISABLE_CONFIRMED", "0"),
        "vol_mult": os.environ.get("SUPERTREND_VOL_MULT", "1.2"),
        "quality_min": os.environ.get("SUPERTREND_QUALITY_MIN", "0.5"),
        "adx_min": os.environ.get("SUPERTREND_ADX_MIN", "default"),
        "require_atr_rising": os.environ.get("SUPERTREND_REQUIRE_ATR_RISING", "1"),
        # R105: guards safety toggles
        "guards_enabled": os.environ.get("SUPERTREND_GUARDS_ENABLED", "1"),
        "guards_require_load": os.environ.get("SUPERTREND_GUARDS_REQUIRE_LOAD", "0"),
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
            # R101: break down skipped reasons by category. R97 entries
            # write reason="R97 guard: [L:layer] [GuardName] ...". Knowing
            # WHICH guard is rejecting tells the operator whether to lower
            # leverage, narrow whitelist, etc. Without this they'd only
            # see an integer count and have to dig in the journal.
            skip_reasons_breakdown: dict[str, int] = {}
            guard_rejections_breakdown: dict[str, int] = {}
            for r in rows:
                if r.get("event_type") not in ("skipped", "circuit_breaker"):
                    continue
                reason = str(r.get("reason") or "unknown")
                # Top-level bucket — first 40 chars or up to first ' — '
                tag = reason.split(" — ", 1)[0].strip()[:60]
                skip_reasons_breakdown[tag] = skip_reasons_breakdown.get(tag, 0) + 1
                # R97 guard sub-breakdown via [GuardName] pattern
                if "R97 guard:" in reason:
                    import re as _re
                    m = _re.search(r"\[(\w+Guard)\]", reason)
                    gname = m.group(1) if m else "Unknown"
                    guard_rejections_breakdown[gname] = (
                        guard_rejections_breakdown.get(gname, 0) + 1
                    )
            if rows:
                last_event_ts = max(r.get("timestamp", "") for r in rows)
                first_event_ts = min(r.get("timestamp", "") for r in rows)
                health = {
                    "ok": True, "last_event_ts": last_event_ts,
                    "events_in_window": len(rows),
                }
                # R75: span between oldest and newest event ≈ uptime in window
                try:
                    first_dt = datetime.fromisoformat(
                        first_event_ts.replace("Z", "+00:00")
                    )
                    last_dt = datetime.fromisoformat(
                        last_event_ts.replace("Z", "+00:00")
                    )
                    eval_summary["observed_span_hours"] = round(
                        (last_dt - first_dt).total_seconds() / 3600.0, 2,
                    )
                except Exception:
                    eval_summary["observed_span_hours"] = 0.0
            else:
                health = {
                    "ok": False, "reason": f"no events in last {eval_window_days}d",
                }
                eval_summary["observed_span_hours"] = 0.0
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
        # R101 breakdowns (default to {} when journal empty / unread)
        "skip_reasons_top": dict(
            sorted(
                (skip_reasons_breakdown if 'skip_reasons_breakdown' in locals() else {}).items(),
                key=lambda kv: kv[1], reverse=True,
            )[:5]
        ),
        "guard_rejections_top": dict(
            sorted(
                (guard_rejections_breakdown if 'guard_rejections_breakdown' in locals() else {}).items(),
                key=lambda kv: kv[1], reverse=True,
            )[:5]
        ),
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
            # R95: per-pair productivity. R90 walk-forward found BTC=6/8
            # wins, ADA=2/8, ETH/SOL/XRP=0/0 — yet operator had no visible
            # signal that 14 of 17 prod pairs are dead weight. Surface top
            # pairs (sorted by n trades desc) so deny-list / curated
            # whitelist decisions are evidence-based.
            top_pairs = []
            for pair, gs in sorted(
                snap.by_pair.items(),
                key=lambda kv: kv[1].n,
                reverse=True,
            )[:20]:
                top_pairs.append({
                    "pair": pair,
                    "n_trades": gs.n,
                    "wins": gs.wins,
                    "losses": gs.losses,
                    "win_rate": round(gs.win_rate, 4),
                    "avg_pnl_pct": round(gs.avg_pnl_pct, 4),
                    "sum_pnl_usd": round(gs.sum_pnl_usd, 4),
                })
            # Pairs in the whitelist that produced ZERO trades in window —
            # the most actionable signal: candidates for removal.
            try:
                wl_resp = _ft_get("/api/v1/whitelist", timeout=3.0)
                wl_pairs = set((wl_resp or {}).get("whitelist", []) or [])
            except Exception:
                wl_pairs = set()
            traded = set(snap.by_pair.keys())
            silent_pairs = sorted(wl_pairs - traded)

            out["performance"] = {
                "window_days": perf_window_days,
                "n_trades": snap.n_trades,
                "win_rate": snap.win_rate,
                "sum_pnl_usd": snap.sum_pnl_usd,
                "max_drawdown_pct": snap.max_drawdown_pct,
                # R95
                "top_pairs": top_pairs,
                "silent_pairs": silent_pairs,
                "silent_pair_count": len(silent_pairs),
                "active_pair_count": len(traded),
            }
        except Exception as e:
            out["errors"]["performance"] = str(e)

    # ---- guard state (R98) ---- #
    # R97 wired the guard pipeline into supertrend.confirm_trade_entry.
    # Without surfacing the state, an operator wouldn't know if guards
    # had paused trading (consecutive losses), or were about to pause
    # (near daily-loss ceiling). Read-only snapshot via get_state_summary.
    guards_block: dict[str, Any] = {"available": False}
    try:
        from guards.pipeline import get_state_summary as _guard_state_summary
        guards_block = {"available": True, **_guard_state_summary()}
    except Exception as e:
        guards_block = {"available": False, "error": str(e)}
    out["guards"] = guards_block

    # ---- composed actionable alerts ---- #
    out["alerts"] = _build_ops_alerts(
        bot_state=bot_state,
        n_pairs=n_pairs if n_pairs >= 0 else 0,
        eval_summary=eval_summary,
        health=health,
        recent_trades=n_recent_trades,
        journal_ok=journal_ok,
        observed_span_hours=eval_summary.get("observed_span_hours", 0.0),
    )
    # R98: append guard-state alerts
    out["alerts"].extend(_build_guard_alerts(guards_block))
    # R101: append per-guard rejection-rate alerts
    out["alerts"].extend(_build_guard_rejection_alerts(
        out["pipeline"].get("guard_rejections_top") or {},
        eval_window_days,
    ))
    # R105: GUARDS_NEVER_FIRED — silent failure detector (lesson from R104)
    out["alerts"].extend(_build_guards_never_fired_alert(
        out["pipeline"].get("recent_skipped") or 0,
        out["pipeline"].get("guard_rejections_top") or {},
        eval_window_days,
    ))
    out["alert_count"] = len(out["alerts"])
    out["status"] = "ok" if not out["alerts"] else "degraded"
    return out


# R102: guards whose heavy-rejection is ALREADY covered by a more
# specific alert. Suppressing them here avoids double-alerting the
# operator about the same condition under a different name.
_GUARD_REJECTION_SUPPRESS = frozenset({
    # ConsecutiveLossGuard rejections during pause are by-design — every
    # entry attempt during the pause window will reject, which would
    # spam GUARD_REJECTING_HEAVILY when GUARD_PAUSED (R98) already says
    # exactly what's wrong + when it'll lift.
    "ConsecutiveLossGuard",
    # DailyLossGuard rejections during cap-hit are by-design — same logic.
    # GUARD_NEAR_DAILY_LIMIT (R98) already warns at 80%; once the cap is
    # hit, the same guard will reject every entry until tomorrow, which
    # is correct but doesn't need a second alert.
    "DailyLossGuard",
})


def _build_guard_rejection_alerts(
    guard_rejections_top: dict[str, int], window_days: int,
) -> list[str]:
    """R101: alert when a single guard is rejecting >=5 entries in window.

    Catches the R99-leverage / R97-MaxPosition interaction: now that
    leverage() returns 1.5–5x, MaxPositionGuard might silently start
    rejecting entries that would have passed under the broken 1x default.
    Operator needs visible signal to consider lowering leverage clamp
    or raising MaxPositionGuard cap.

    R102: skip guards already covered by R98 state alerts (otherwise
    the operator gets the same condition reported twice with conflicting
    "what to do" guidance).
    """
    alerts: list[str] = []
    for gname, n in guard_rejections_top.items():
        if gname in _GUARD_REJECTION_SUPPRESS:
            continue
        if n >= 5:
            alerts.append(
                f"GUARD_REJECTING_HEAVILY — {gname} blocked {n} entries in "
                f"the last {window_days}d. Either tighten upstream "
                f"(strategy proposing too-aggressive size/leverage) or loosen "
                f"the guard (raise its limit if the rejections are correct "
                f"but still want the trades). Inspect /api/supertrend/skipped "
                f"for the per-event detail."
            )
    return alerts


def _suggest_for_failure(failure_text: str) -> str:
    """R108: map a R66 EvaluationEvent dominant failure reason to actionable
    advice. Each suggestion names the env var that controls that gate
    (R87/R89/R91), preferred fallback step, and any caveat.

    The matching is loose-prefix because R93 dynamically formats the text
    from current env (e.g. vol_mult=1.0 → "vol<=1*ma", vol_mult=1.2 →
    "vol<=1.2*ma"). We don't need exact equality — just to identify the
    gate family.
    """
    f = failure_text.lower()
    # Volume gate (R89)
    if f.startswith("vol<=") and "*ma" in f:
        if f.startswith("vol<=1*ma") or f.startswith("vol<=0"):
            return (
                "Volume gate already at minimum effective level (R89 vol_mult=1.0). "
                "Backtests showed 0.8 brings no extra alpha, so this is signal "
                "of genuine market chop — wait for regime shift, no env tweak helps."
            )
        return (
            "Loosen via SUPERTREND_VOL_MULT (R89): default 1.2 → try 1.0. "
            "R89 backtest validated 8/8 wins at 1.0 vs 5/5 at 1.2 (6 months). "
            "See docs/reports/r89_vol_mult_findings.md before deploying."
        )
    # Quality gate (R91)
    if f.startswith("quality<="):
        return (
            "Loosen via SUPERTREND_QUALITY_MIN (R91): default 0.5 → try 0.4. "
            "Run A/B backtest matrix in docs/reports/r91_quality_gates_design.md "
            "first; bar is WR ≥ 87.5% on R89 6-month window."
        )
    # ADX gate (R91)
    if f.startswith("adx<="):
        return (
            "Loosen via SUPERTREND_ADX_MIN (R91): default 25 → try 20. "
            "Lower ADX = weaker trend, higher chop risk; backtest carefully."
        )
    # ATR rising (R91)
    if "atr_not_rising" in f:
        return (
            "Disable via SUPERTREND_REQUIRE_ATR_RISING=0 (R91). CAUTION: "
            "ATR rising is a trend-confirmation gate; disabling raises false "
            "positive risk in choppy regimes. Backtest before deploying."
        )
    # Multi-tf alignment failures — strategy waiting for market alignment
    if any(s in f for s in (
        "all_bullish=false", "all_bearish=false",
        "bull_just_formed=false", "bear_just_formed=false",
        "pair_bullish_2tf_just_formed=false", "pair_bearish_2tf_just_formed=false",
        "st_buy=false", "st_sell=false",
        "st_trend!=", "st_1h_already_aligned",
    )):
        return (
            "Multi-timeframe alignment not satisfied — this is the strategy "
            "WAITING for a setup, not a bug. No env tweak will help; this "
            "fires when 1d/4h/1h/15m haven't lined up. Wait for trend regime."
        )
    # Funding rate filter
    if f.startswith("fr_blocks_"):
        return (
            "Funding contra-signal blocking. Default OFF; if SUPERTREND_FR_ALPHA=1 "
            "is set, consider unsetting to disable this filter. R57 was opt-in "
            "alpha — not required for core strategy."
        )
    # Direction concentration / R48 regime / R58 correlation / CB
    if "regime:" in f or "direction_concentration" in f or "R58" in f:
        return (
            "Portfolio-level guard active (regime / concentration / correlation). "
            "Working as designed; check confidence and BTC regime via /api/supertrend/regime."
        )
    if "CB tripped" in failure_text:
        return (
            "Account-level circuit breaker tripped (R48). Check most recent "
            "exits — strategy auto-pauses after consecutive losses."
        )
    if "confirmed_disabled_R87" in failure_text:
        return (
            "R87 disable_confirmed=1 active — confirmed tier intentionally off. "
            "scout/pre_scout still fireable. No action needed."
        )
    # Fallback
    return (
        "See /api/supertrend/evaluations for full breakdown. Likely market "
        "regime mismatch or strategy waiting for alignment, not a code bug."
    )


def _build_guards_never_fired_alert(
    recent_skipped: int,
    guard_rejections_top: dict[str, int],
    window_days: int,
    *,
    min_skips_for_signal: int = 10,
) -> list[str]:
    """R105: detect the R104-class silent failure pattern.

    Symptom: many SkippedEvents in the window (so the pipeline IS rejecting
    things) BUT zero of them came from R97 guards. R104 was exactly this —
    `from guards.base import` failed in the freqtrade container, R97's
    fail-open path returned None, every entry attempt slipped through the
    guard layer silently for over a week.

    Heuristic:
      * recent_skipped >= min_skips_for_signal — pipeline is alive enough
        to skip things, so absence of guard skips is meaningful
      * guard_rejections_top is empty — NO guard-attributed skip in window

    False-positive scenarios (operator should still investigate):
      * 100% of skips were R57 / regime / direction-concentration —
        no guard was even invoked. Possible but unlikely over many skips.
      * SUPERTREND_GUARDS_ENABLED=0 — guards intentionally off. Caller
        could add that check, but env-mismatch between API and freqtrade
        container makes this hard to be sure of (per R104 root cause).

    Conservative: emit alert + tell operator to verify with `docker exec`.
    """
    if recent_skipped < min_skips_for_signal:
        return []
    if guard_rejections_top:
        return []   # guards are firing, pipeline OK
    return [
        f"GUARDS_NEVER_FIRED — {recent_skipped} skipped events in last "
        f"{window_days}d but ZERO attributed to R97 guards. This is the "
        f"R104 silent-failure pattern (guards.* import fails in freqtrade "
        f"container → fail-open returns None → no protection). VERIFY: "
        f"`docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/"
        f"strategies && python3 -c \"from guards.pipeline import "
        f"create_default_pipeline; print(len(create_default_pipeline()."
        f"guards))\"'` should print a positive integer (e.g. 9). If it "
        f"errors, set SUPERTREND_GUARDS_REQUIRE_LOAD=1 + redeploy."
    ]


def _build_guard_alerts(guards: dict) -> list[str]:
    """R98: actionable alerts from guard state snapshot.

    GUARD_PAUSED — ConsecutiveLossGuard tripped, trading paused until X.
                   Operator must investigate the loss streak before unpausing.
    GUARD_NEAR_DAILY_LIMIT — DailyLossGuard at >80% of cap. Next loss may pause.
    """
    if not guards or not guards.get("available"):
        return []
    alerts: list[str] = []
    paused_until = guards.get("paused_until") or 0
    import time as _t
    now = _t.time()
    if paused_until > now:
        remaining_h = (paused_until - now) / 3600
        alerts.append(
            f"GUARD_PAUSED — trading paused for {remaining_h:.1f}h after "
            f"{guards.get('consecutive_losses', '?')} consecutive losses. "
            "Review recent exits before manually unpausing."
        )
    daily_loss = guards.get("daily_loss") or 0
    daily_cap_pct = guards.get("daily_loss_limit_pct") or 0
    if daily_loss > 0 and daily_cap_pct > 0:
        # Need account balance to compute %; use peak_equity as proxy
        # (DrawdownGuard tracks it). Fall back to "we know it's nonzero".
        peak = guards.get("drawdown_peak_equity") or 0
        if peak > 0:
            cap_usd = peak * (daily_cap_pct / 100)
            if cap_usd > 0 and daily_loss / cap_usd > 0.8:
                pct_of_cap = (daily_loss / cap_usd) * 100
                alerts.append(
                    f"GUARD_NEAR_DAILY_LIMIT — daily loss ${daily_loss:.2f} "
                    f"is {pct_of_cap:.0f}% of {daily_cap_pct:.0f}% cap "
                    f"(${cap_usd:.2f}). Next material loss likely pauses trading."
                )
    return alerts


# =================================================================== #
# /force_entry — R70 — manual end-to-end smoke test
# =================================================================== #
def _verify_entry_in_journal(pair: str, timeout_sec: float = 10.0,
                              poll_interval: float = 1.0) -> dict[str, Any] | None:
    """Poll journal for an EntryEvent matching pair, written within
    `timeout_sec` of now. Returns the row dict or None if not found in time."""
    import time as _time

    journal_dir = _resolve_journal_dir()
    if not journal_dir.exists():
        return None

    try:
        from strategies.journal import TradeJournal
    except ImportError:
        return None

    journal = TradeJournal(journal_dir)
    deadline = _time.monotonic() + timeout_sec
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)

    while _time.monotonic() < deadline:
        try:
            rows = journal.read_range(from_date=cutoff)
            for r in reversed(rows):   # newest first
                if r.get("event_type") == "entry" and r.get("pair") == pair:
                    return r
        except Exception:
            pass
        _time.sleep(poll_interval)
    return None


@router.post("/force_entry")
def supertrend_force_entry(
    pair: str = Query(..., description="Pair to force-enter, e.g. BTC/USDT:USDT"),
    side: str = Query("long", pattern="^(long|short)$"),
    stake_amount: float | None = Query(
        None, description="Override stake (USDT). Default: use strategy logic.",
    ),
    verify_journal: bool = Query(
        True, description="Poll journal up to 10s for the EntryEvent",
    ),
) -> dict[str, Any]:
    """R70 — Manually trigger an entry to smoke-test the full execution chain.

    Wraps freqtrade's POST /api/v1/forceenter. Default DISABLED via
    SUPERTREND_FORCE_ENTRY_ENABLED env (must be set to "1" to allow).
    Even when enabled, only meaningful in dry-run mode — production
    operators should NEVER set both LIVE=1 AND FORCE_ENTRY=1 simultaneously.

    Returns:
      forceenter_response  — raw freqtrade reply
      journal_entry        — dict (if verify_journal=True and found)
      verified             — bool whether journal write succeeded
      duration_ms          — round-trip including journal verification
    """
    import time as _time

    if os.environ.get("SUPERTREND_FORCE_ENTRY_ENABLED", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail=(
                "force_entry disabled — set SUPERTREND_FORCE_ENTRY_ENABLED=1 "
                "in .env and restart the api container to enable. "
                "Use only in dry-run mode for end-to-end smoke testing."
            ),
        )

    # SAFETY: refuse if bot is in live-trading mode
    try:
        cfg = _ft_get("/api/v1/show_config", timeout=3.0)
        if cfg and cfg.get("dry_run") is False:
            raise HTTPException(
                status_code=403,
                detail=(
                    "REFUSED: bot is in LIVE mode (dry_run=False). "
                    "force_entry is dry-run-only. Set SUPERTREND_LIVE=0 "
                    "or do not invoke this endpoint in production."
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        # Continue if probe failed — bot likely OK, log decision
        logger.warning("force_entry pre-flight show_config failed: %s", e)

    started = _time.monotonic()
    body: dict[str, Any] = {"pair": pair, "side": side}
    if stake_amount is not None:
        body["stakeamount"] = stake_amount

    out: dict[str, Any] = {
        "pair": pair, "side": side,
        "stake_override": stake_amount,
    }
    try:
        resp = _ft_post("/api/v1/forceenter", body, timeout=15.0)
        out["forceenter_response"] = resp
    except Exception as e:
        out["error"] = f"freqtrade /forceenter failed: {e}"
        out["verified"] = False
        out["duration_ms"] = int((_time.monotonic() - started) * 1000)
        return out

    # Verify the entry chain wrote to journal (R46 EntryEvent)
    if verify_journal:
        ev = _verify_entry_in_journal(pair, timeout_sec=10.0)
        out["journal_entry"] = ev
        out["verified"] = ev is not None
    else:
        out["verified"] = None

    out["duration_ms"] = int((_time.monotonic() - started) * 1000)
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
