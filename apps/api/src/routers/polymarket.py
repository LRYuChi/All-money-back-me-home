"""Polymarket Phase 1 儀表板 API — 純讀取，資料來源 SQLite + status.json.

資料路徑（皆為 api container 內）：
    /app/data/polymarket.db
    /app/data/reports/polymarket_pipeline_status.json

所有端點皆以 30 秒 TTL 快取，避免每秒重新查 SQLite。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/polymarket", tags=["polymarket"])

# 路徑與快取
# DB 在 shared docker 命名 volume，status.json 在 host 端由 wrapper 寫入後
# 透過 docker-compose 的 bind mount 暴露給 api。兩條路徑分開是刻意的。
_DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
_DB_PATH = _DATA_DIR / "polymarket.db"

_STATUS_FILENAME = "polymarket_pipeline_status.json"
_STATUS_HOST_MOUNT = Path("/app/polymarket_status") / _STATUS_FILENAME  # bind mount
_STATUS_DATA_DIR = _DATA_DIR / "reports" / _STATUS_FILENAME              # legacy / in-volume
_STATUS_CANDIDATES = [_STATUS_HOST_MOUNT, _STATUS_DATA_DIR]

_CACHE_TTL = 30.0  # seconds
_cache: dict[str, tuple[Any, float]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    value, expires_at = hit
    if time.time() > expires_at:
        return None
    return value


def _cache_set(key: str, value: Any, ttl: float = _CACHE_TTL) -> None:
    _cache[key] = (value, time.time() + ttl)


def _connect() -> sqlite3.Connection:
    if not _DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"polymarket DB not found at {_DB_PATH} (pipeline hasn't run yet?)",
        )
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _parse_json_field(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/status
# ─────────────────────────────────────────────────────────────────────
@router.get("/status")
def get_status() -> dict:
    """Pipeline 最近一次運行狀態（來自 polymarket_pipeline_status.json）."""
    if cached := _cache_get("status"):
        return cached

    status_path = next((p for p in _STATUS_CANDIDATES if p.exists()), None)
    if status_path is None:
        return {
            "last_run_start": None,
            "last_run_end": None,
            "duration_seconds": None,
            "result": "never_run",
            "exit_code": None,
            "mode": None,
            "markets_limit": None,
            "wallets_cap": None,
        }
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"status file unreadable: {exc}") from exc

    _cache_set("status", data, ttl=10.0)  # short TTL so freshness indicator updates quickly
    return data


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/overview
# ─────────────────────────────────────────────────────────────────────
@router.get("/overview")
def get_overview() -> dict:
    """聚合概覽：tier 分布 + 總計 + 最近 24h 活動."""
    if cached := _cache_get("overview"):
        return cached

    with _connect() as conn:
        tier_rows = conn.execute(
            "SELECT tier, COUNT(*) AS count FROM whale_stats GROUP BY tier"
        ).fetchall()
        tier_distribution = {r["tier"]: r["count"] for r in tier_rows}

        total_markets = conn.execute("SELECT COUNT(*) AS c FROM markets").fetchone()["c"]
        active_markets = conn.execute(
            "SELECT COUNT(*) AS c FROM markets WHERE active=1 AND closed=0"
        ).fetchone()["c"]
        total_trades = conn.execute("SELECT COUNT(*) AS c FROM trades").fetchone()["c"]
        trades_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM trades WHERE match_time >= datetime('now', '-24 hours')"
        ).fetchone()["c"]
        total_whales = conn.execute("SELECT COUNT(*) AS c FROM whale_stats").fetchone()["c"]
        alerts_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM whale_trade_alerts WHERE alerted_at >= datetime('now', '-24 hours')"
        ).fetchone()["c"]

        # 最近一次 tier 變動
        latest_change_row = conn.execute(
            "SELECT wallet_address, from_tier, to_tier, changed_at, reason "
            "FROM whale_tier_history ORDER BY id DESC LIMIT 1"
        ).fetchone()

    overview = {
        "tier_distribution": tier_distribution,
        "totals": {
            "markets": total_markets,
            "active_markets": active_markets,
            "whales": total_whales,
            "trades": total_trades,
        },
        "activity_24h": {
            "trades": trades_24h,
            "alerts": alerts_24h,
        },
        "latest_tier_change": dict(latest_change_row) if latest_change_row else None,
    }
    _cache_set("overview", overview)
    return overview


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/whales?tier=A,B,C&limit=50&order_by=cumulative_pnl
# ─────────────────────────────────────────────────────────────────────
@router.get("/whales")
def list_whales(
    tier: str | None = Query(
        default=None,
        description="Comma-separated tiers (A,B,C,volatile,excluded). Default: A,B,C",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    order_by: str = Query(
        default="cumulative_pnl",
        description="cumulative_pnl | trade_count_90d | win_rate | last_trade_at",
    ),
) -> dict:
    allowed_orders = {"cumulative_pnl", "trade_count_90d", "win_rate", "last_trade_at"}
    if order_by not in allowed_orders:
        raise HTTPException(status_code=400, detail=f"order_by must be one of {sorted(allowed_orders)}")

    tiers = [t.strip() for t in (tier or "A,B,C").split(",") if t.strip()]
    if not tiers:
        raise HTTPException(status_code=400, detail="at least one tier required")

    cache_key = f"whales:{','.join(sorted(tiers))}:{limit}:{order_by}"
    if cached := _cache_get(cache_key):
        return cached

    placeholders = ",".join("?" * len(tiers))
    sql = (
        f"SELECT wallet_address, tier, trade_count_90d, win_rate, cumulative_pnl, "
        f"avg_trade_size, segment_win_rates, stability_pass, resolved_count, "
        f"last_trade_at, last_computed_at "
        f"FROM whale_stats WHERE tier IN ({placeholders}) "
        f"ORDER BY {order_by} DESC LIMIT ?"
    )
    with _connect() as conn:
        rows = conn.execute(sql, (*tiers, limit)).fetchall()

    whales = []
    for r in rows:
        whales.append(
            {
                "wallet_address": r["wallet_address"],
                "tier": r["tier"],
                "trade_count_90d": r["trade_count_90d"],
                "win_rate": r["win_rate"],
                "cumulative_pnl": r["cumulative_pnl"],
                "avg_trade_size": r["avg_trade_size"],
                "segment_win_rates": _parse_json_field(r["segment_win_rates"], []),
                "stability_pass": bool(r["stability_pass"]),
                "resolved_count": r["resolved_count"],
                "last_trade_at": r["last_trade_at"],
                "last_computed_at": r["last_computed_at"],
            }
        )

    result = {"count": len(whales), "whales": whales}
    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/alerts?hours=24&limit=50&tier=A,B,C
# ─────────────────────────────────────────────────────────────────────
@router.get("/alerts")
def list_alerts(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=50, ge=1, le=500),
    tier: str | None = Query(default=None, description="Filter by tier"),
) -> dict:
    cache_key = f"alerts:{hours}:{limit}:{tier}"
    if cached := _cache_get(cache_key):
        return cached

    params: list[Any] = [f"-{hours} hours"]
    tier_clause = ""
    if tier:
        tiers = [t.strip() for t in tier.split(",") if t.strip()]
        if tiers:
            placeholders = ",".join("?" * len(tiers))
            tier_clause = f" AND tier IN ({placeholders})"
            params.extend(tiers)
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT wallet_address, tx_hash, event_index, tier, condition_id, "
            f"market_question, side, outcome, size, price, notional, match_time, alerted_at "
            f"FROM whale_trade_alerts "
            f"WHERE match_time >= datetime('now', ?)"
            f"{tier_clause} "
            f"ORDER BY match_time DESC LIMIT ?",
            params,
        ).fetchall()

    result = {"count": len(rows), "alerts": _rows_to_dicts(rows), "window_hours": hours}
    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/markets?active=true&limit=20
# ─────────────────────────────────────────────────────────────────────
@router.get("/markets")
def list_markets(
    active: bool = Query(default=True),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    cache_key = f"markets:{active}:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    sql = (
        "SELECT m.condition_id, m.question, m.market_slug, m.category, m.end_date_iso, "
        "m.active, m.closed, m.minimum_tick_size, "
        "(SELECT COUNT(*) FROM trades t WHERE t.condition_id = m.condition_id "
        " AND t.match_time >= datetime('now', '-24 hours')) AS trades_24h "
        "FROM markets m "
    )
    params: list[Any] = []
    if active:
        sql += "WHERE m.active=1 AND m.closed=0 "
    sql += "ORDER BY trades_24h DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        markets: list[dict] = []
        for r in rows:
            token_rows = conn.execute(
                "SELECT token_id, outcome, price FROM tokens WHERE condition_id=?",
                (r["condition_id"],),
            ).fetchall()
            markets.append(
                {
                    "condition_id": r["condition_id"],
                    "question": r["question"],
                    "market_slug": r["market_slug"],
                    "category": r["category"],
                    "end_date_iso": r["end_date_iso"],
                    "active": bool(r["active"]),
                    "closed": bool(r["closed"]),
                    "tokens": _rows_to_dicts(token_rows),
                    "trades_24h": r["trades_24h"],
                }
            )

    result = {"count": len(markets), "markets": markets}
    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/pipeline/history?limit=20
# ─────────────────────────────────────────────────────────────────────
@router.get("/pipeline/history")
def pipeline_history(limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """tier 變動歷史——讓使用者看到鯨魚層級隨時間的演化."""
    cache_key = f"history:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    with _connect() as conn:
        rows = conn.execute(
            "SELECT wallet_address, from_tier, to_tier, changed_at, reason "
            "FROM whale_tier_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = {"count": len(rows), "changes": _rows_to_dicts(rows)}
    _cache_set(cache_key, result)
    return result
