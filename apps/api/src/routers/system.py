"""System-level endpoints — data pipeline liveness + health aggregation.

一站式回報三條資料管線的新鮮度，供 Overview 頁的 Data Health 元件使用。

設計原則：
  - 所有 probe 都是 best-effort；失敗不拋 500，改在對應欄位標 unreachable
  - 用 indexed 查詢（避免表 seq-scan）
  - 整體 endpoint 目標 < 500ms (p95)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
_POLY_DB_PATH = _DATA_DIR / "polymarket.db"

_CACHE_TTL = 15.0
_cache: dict[str, tuple[Any, float]] = {}


def _age_seconds(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds()
    except Exception:
        return None


def _probe_polymarket() -> dict:
    """Polymarket pipeline liveness via SQLite (read-only)."""
    result: dict[str, Any] = {
        "name": "polymarket",
        "configured": True,
        "expected_cadence_s": 300,  # 5-min cron
        "last_data_at": None,
        "last_compute_at": None,
        "age_seconds": None,
        "health": "unknown",
        "trades_24h": 0,
        "trades_1h": 0,
        "trades_5m": 0,
        "alerts_24h": 0,
        "paper_trades_total": 0,
    }

    if not _POLY_DB_PATH.exists():
        result["configured"] = False
        result["health"] = "red"
        result["error"] = "polymarket.db not found"
        return result

    try:
        conn = sqlite3.connect(f"file:{_POLY_DB_PATH}?mode=ro", uri=True, timeout=3)
        try:
            latest_trade = conn.execute(
                "SELECT MAX(fetched_at) FROM trades"
            ).fetchone()[0]
            latest_compute = conn.execute(
                "SELECT MAX(last_computed_at) FROM whale_stats"
            ).fetchone()[0]
            trades_24h = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE fetched_at > datetime('now','-24 hours')"
            ).fetchone()[0]
            trades_1h = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE fetched_at > datetime('now','-1 hour')"
            ).fetchone()[0]
            trades_5m = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE fetched_at > datetime('now','-5 minutes')"
            ).fetchone()[0]
            alerts_24h = conn.execute(
                "SELECT COUNT(*) FROM whale_trade_alerts "
                "WHERE alerted_at > datetime('now','-24 hours')"
            ).fetchone()[0]
            paper_total = conn.execute(
                "SELECT COUNT(*) FROM paper_trades"
            ).fetchone()[0]
        finally:
            conn.close()

        age = _age_seconds(latest_trade)
        # Health bands tuned to 5-min cadence:
        #   <= 2× cadence (10 min) = green
        #   <= 4× cadence (20 min) = yellow
        #   else red
        if age is None:
            health = "red"
        elif age <= 600:
            health = "green"
        elif age <= 1200:
            health = "yellow"
        else:
            health = "red"

        result.update(
            {
                "last_data_at": latest_trade,
                "last_compute_at": latest_compute,
                "age_seconds": age,
                "health": health,
                "trades_24h": int(trades_24h or 0),
                "trades_1h": int(trades_1h or 0),
                "trades_5m": int(trades_5m or 0),
                "alerts_24h": int(alerts_24h or 0),
                "paper_trades_total": int(paper_total or 0),
            }
        )
    except Exception as e:
        logger.warning("polymarket probe failed: %s", e)
        result["health"] = "red"
        result["error"] = str(e)[:200]
    return result


def _probe_smart_money() -> dict:
    """Smart Money (Supabase sm_*) liveness."""
    result: dict[str, Any] = {
        "name": "smart_money",
        "configured": False,
        "expected_cadence_s": None,  # manual scan, not cron-driven
        "last_data_at": None,
        "age_seconds": None,
        "health": "unknown",
        "wallets_total": 0,
        "trades_total": 0,
        "latest_snapshot_date": None,
        "scan_cadence": "manual",
    }

    try:
        from src.services.supabase_client import get_supabase
    except ImportError:
        result["error"] = "supabase client unavailable"
        result["health"] = "red"
        return result

    client = get_supabase()
    if client is None:
        result["error"] = "Supabase not configured"
        result["health"] = "red"
        return result
    result["configured"] = True

    try:
        # Use indexed last_active_at on sm_wallets (cheap) as liveness proxy.
        recent = (
            client.table("sm_wallets")
            .select("last_active_at")
            .order("last_active_at", desc=True)
            .limit(1)
            .execute()
        )
        if recent.data:
            result["last_data_at"] = recent.data[0]["last_active_at"]

        wallets_cnt = (
            client.table("sm_wallets").select("*", count="exact").limit(1).execute()
        )
        result["wallets_total"] = wallets_cnt.count or 0

        # Latest snapshot date
        rankings = (
            client.table("sm_rankings")
            .select("snapshot_date")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        if rankings.data:
            result["latest_snapshot_date"] = rankings.data[0]["snapshot_date"]

        # trades_total — 需要 014 migration 的 ts index 才快。
        # 未加 index 前 COUNT(*) 會 seq-scan 3.2M rows → 57014 timeout。
        # 跳過這個查詢避免拖慢 data-health probe 5+ seconds。
        # 加 index 後可改回直接 count (留個 TODO)。
        # TODO(sm-perf): 套用 014_smart_money_ts_index.sql 後改回直接 count
        result["trades_total"] = None

        age = _age_seconds(result["last_data_at"])
        # Smart Money is manual scan — treat anything < 7d as acceptable
        if age is None:
            health = "red"
        elif age <= 86400 * 1:
            health = "green"
        elif age <= 86400 * 7:
            health = "yellow"
        else:
            health = "red"
        result["age_seconds"] = age
        result["health"] = health

    except Exception as e:
        logger.warning("smart_money probe failed: %s", e)
        result["health"] = "red"
        result["error"] = str(e)[:200]
    return result


def _probe_freqtrade() -> dict:
    """Freqtrade REST API liveness + Supertrend signal state."""
    import base64
    import json
    import urllib.request

    result: dict[str, Any] = {
        "name": "freqtrade_supertrend",
        "configured": True,
        "expected_cadence_s": 900,  # 15m timeframe
        "last_data_at": None,
        "age_seconds": None,
        "health": "unknown",
        "state": None,
        "dry_run": None,
        "strategy": None,
        "pairs_count": 0,
        "trades_total": 0,
        "open_trades": 0,
        "profit": 0.0,
    }

    user = os.environ.get("FT_USER", "freqtrade")
    pw = os.environ.get("FT_PASS", "freqtrade")
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()

    # freqtrade container is on the same docker network; hostname = service name
    base_hosts = [
        os.environ.get("FREQTRADE_URL", "http://freqtrade:8080"),
        "http://freqtrade:8080",
    ]

    def _get(base: str, path: str) -> dict | None:
        try:
            req = urllib.request.Request(
                f"{base}/api/v1/{path}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read())
        except Exception:
            return None

    # Try endpoints until one works
    cfg = None
    used_base = None
    for base in base_hosts:
        cfg = _get(base, "show_config")
        if cfg:
            used_base = base
            break

    if not cfg:
        result["health"] = "red"
        result["error"] = "freqtrade unreachable"
        return result

    result["state"] = cfg.get("state")
    result["dry_run"] = cfg.get("dry_run")
    result["strategy"] = cfg.get("strategy")

    wl = _get(used_base, "whitelist") or {}
    pairs = wl.get("whitelist", []) if isinstance(wl, dict) else []
    result["pairs_count"] = len(pairs)

    pf = _get(used_base, "profit") or {}
    result["trades_total"] = int(pf.get("trade_count", 0) or 0)
    result["open_trades"] = result["trades_total"] - int(pf.get("closed_trade_count", 0) or 0)
    result["profit"] = float(pf.get("profit_all_coin", 0) or 0)

    # Liveness: last candle age (uses first pair)
    if pairs:
        candles = _get(used_base, f"pair_candles?pair={pairs[0]}&timeframe=15m&limit=1")
        if candles and candles.get("data"):
            last_ts = candles["data"][-1][0]
            result["last_data_at"] = last_ts
            age = _age_seconds(last_ts)
            result["age_seconds"] = age
            # 15m timeframe: <= 2 candle gaps (30m) green, <= 4 (60m) yellow
            if age is None:
                result["health"] = "red"
            elif age <= 1800:
                result["health"] = "green"
            elif age <= 3600:
                result["health"] = "yellow"
            else:
                result["health"] = "red"
        else:
            result["health"] = "yellow"
    else:
        result["health"] = "yellow"

    # If bot is not in running state override health
    if result["state"] != "running":
        result["health"] = "red"

    return result


@router.get("/data-health")
def get_data_health() -> dict:
    """匯聚三條資料管線的 liveness。目標 p95 < 500ms.

    三個 probe 平行跑（ThreadPoolExecutor）— 不然 sequential 時
    freqtrade REST 3s timeout + supabase 5s timeout 會把延遲推到 8+s。
    """
    import concurrent.futures as _cf

    cached = _cache.get("data_health")
    if cached and time.time() < cached[1]:
        return cached[0]

    t0 = time.time()
    probes = {
        "polymarket": _probe_polymarket,
        "smart_money": _probe_smart_money,
        "freqtrade": _probe_freqtrade,
    }
    results: dict[str, dict] = {}
    with _cf.ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {pool.submit(fn): name for name, fn in probes.items()}
        for fut in _cf.as_completed(futures, timeout=10):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                results[name] = {
                    "name": name,
                    "health": "red",
                    "error": str(exc)[:200],
                }

    # Preserve ordering (polymarket → smart_money → freqtrade)
    pipelines = [results.get(n, {"name": n, "health": "unknown"}) for n in probes.keys()]
    elapsed_ms = int((time.time() - t0) * 1000)

    # Aggregate overall health (worst of the three)
    order = {"green": 0, "yellow": 1, "red": 2, "unknown": 2}
    worst = max(pipelines, key=lambda p: order.get(p.get("health", "unknown"), 2))

    result = {
        "overall_health": worst.get("health", "unknown"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "pipelines": pipelines,
    }
    _cache["data_health"] = (result, time.time() + _CACHE_TTL)
    return result
