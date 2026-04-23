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
#
# 此端點 LEFT JOIN whale_stats (Phase 1 contract) 與 wallet_profiles
# (Phase 1.5+) 的最新 row，把 specialist + consistency 等 1.5b feature
# 一併回傳。當錢包尚無 wallet_profile 時，1.5b 欄位為 null/[]。
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
    sql = f"""
        SELECT
          ws.wallet_address, ws.tier, ws.trade_count_90d, ws.win_rate,
          ws.cumulative_pnl, ws.avg_trade_size, ws.segment_win_rates,
          ws.stability_pass, ws.resolved_count, ws.last_trade_at, ws.last_computed_at,
          wp.scanner_version AS wp_scanner_version,
          wp.features_json   AS wp_features_json,
          wp.archetypes_json AS wp_archetypes_json,
          wp.risk_flags_json AS wp_risk_flags_json,
          wp.scanned_at      AS wp_scanned_at
        FROM whale_stats ws
        LEFT JOIN (
            SELECT wp_inner.* FROM wallet_profiles wp_inner
            INNER JOIN (
                SELECT wallet_address, MAX(scanned_at) AS latest
                FROM wallet_profiles GROUP BY wallet_address
            ) latest
              ON wp_inner.wallet_address = latest.wallet_address
             AND wp_inner.scanned_at = latest.latest
        ) wp ON ws.wallet_address = wp.wallet_address
        WHERE ws.tier IN ({placeholders})
        ORDER BY ws.{order_by} DESC
        LIMIT ?
    """

    with _connect() as conn:
        rows = conn.execute(sql, (*tiers, limit)).fetchall()

    whales = []
    for r in rows:
        # 1.5b feature extraction
        features = _parse_json_field(r["wp_features_json"], {})
        cat = (features.get("category_specialization", {}) or {}).get("value") or {}
        ts = (features.get("time_slice_consistency", {}) or {}).get("value") or {}
        cat_conf = (features.get("category_specialization", {}) or {}).get("confidence")
        ts_conf = (features.get("time_slice_consistency", {}) or {}).get("confidence")

        is_consistent = ts.get("consistent") if ts_conf == "ok" else None
        win_rate_std = ts.get("win_rate_std") if ts_conf == "ok" else None

        whales.append(
            {
                # Phase 1 fields
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
                # 1.5b fields (null/[] when wallet_profile absent)
                "scanner_version": r["wp_scanner_version"],
                "primary_category": cat.get("primary_category"),
                "specialist_categories": cat.get("specialist_categories", []) or [],
                "category_count": int(cat.get("category_count", 0)),
                "is_consistent": is_consistent,
                "win_rate_std": win_rate_std,
                "valid_segments": int(ts.get("valid_segments", 0)),
                "archetypes": _parse_json_field(r["wp_archetypes_json"], []),
                "risk_flags": _parse_json_field(r["wp_risk_flags_json"], []),
                "features_confidence": {
                    "category_specialization": cat_conf,
                    "time_slice_consistency": ts_conf,
                },
            }
        )

    result = {"count": len(whales), "whales": whales}
    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/wallet/{address}
# 單一錢包完整詳情 — 合併 whale_stats + wallet_profiles 最新一筆 + features
# 供 dashboard /polymarket/wallet/[address] 頁面使用
# ─────────────────────────────────────────────────────────────────────
@router.get("/wallet/{address}")
def wallet_detail(address: str) -> dict:
    """完整錢包詳情：stats + features + equity curve + recent trades + tier history."""
    cache_key = f"wallet_detail:{address}"
    if cached := _cache_get(cache_key):
        return cached

    with _connect() as conn:
        ws_row = conn.execute(
            "SELECT * FROM whale_stats WHERE wallet_address=?", (address,)
        ).fetchone()

        wp_row = conn.execute(
            "SELECT scanner_version, scanned_at, tier, trade_count_90d, resolved_count, "
            "win_rate, cumulative_pnl, avg_trade_size, features_json, archetypes_json, "
            "risk_flags_json, sample_size_warning, passed_coarse_filter, coarse_filter_reasons "
            "FROM wallet_profiles WHERE wallet_address=? "
            "ORDER BY scanned_at DESC LIMIT 1",
            (address,),
        ).fetchone()

        if ws_row is None and wp_row is None:
            raise HTTPException(status_code=404, detail=f"wallet {address} not found in whale_stats or wallet_profiles")

        # 近期交易（maker/taker 都算）
        recent_trades = conn.execute(
            """
            SELECT id, condition_id, token_id, price, size, notional, side, match_time,
                   (SELECT question FROM markets WHERE condition_id=trades.condition_id) AS market_question,
                   (SELECT category FROM markets WHERE condition_id=trades.condition_id) AS market_category
            FROM trades
            WHERE (maker_address=? OR taker_address=?)
              AND match_time >= datetime('now', '-90 days')
            ORDER BY match_time DESC
            LIMIT 50
            """,
            (address, address),
        ).fetchall()

        # Tier 變動歷史
        tier_history = conn.execute(
            "SELECT from_tier, to_tier, changed_at, reason FROM whale_tier_history "
            "WHERE wallet_address=? ORDER BY id DESC LIMIT 20",
            (address,),
        ).fetchall()

    # 基礎 stats — 以 wallet_profiles 為主，fallback 到 whale_stats
    stats = _merge_stats(ws_row, wp_row)

    # Features 解包
    features = _parse_json_field(wp_row["features_json"], {}) if wp_row else {}
    steady_growth = _extract_feature(features, "steady_growth")
    category_spec = _extract_feature(features, "category_specialization")
    time_slice = _extract_feature(features, "time_slice_consistency")
    core_stats = _extract_feature(features, "core_stats")

    # Equity curve — 來自 steady_growth feature value.curve (v1.1+)
    sg_value = (steady_growth or {}).get("value") or {}
    curve = sg_value.get("curve") or []
    events = sg_value.get("events") or []

    result = {
        "wallet_address": address,
        "stats": stats,
        "scanner_version": wp_row["scanner_version"] if wp_row else None,
        "scanned_at": wp_row["scanned_at"] if wp_row else None,
        "passed_coarse_filter": bool(wp_row["passed_coarse_filter"]) if wp_row else None,
        "coarse_filter_reasons": _parse_json_field(wp_row["coarse_filter_reasons"], []) if wp_row else [],
        "archetypes": _parse_json_field(wp_row["archetypes_json"], []) if wp_row else [],
        "risk_flags": _parse_json_field(wp_row["risk_flags_json"], []) if wp_row else [],
        "sample_size_warning": bool(wp_row["sample_size_warning"]) if wp_row else False,
        "features": {
            "core_stats": core_stats,
            "steady_growth": steady_growth,
            "category_specialization": category_spec,
            "time_slice_consistency": time_slice,
        },
        "curve": curve,
        "events": events,
        "recent_trades": [_trade_row_to_dict(r) for r in recent_trades],
        "tier_history": [dict(r) for r in tier_history],
    }
    _cache_set(cache_key, result, ttl=15.0)  # 短 TTL，錢包詳情頁期待即時
    return result


def _merge_stats(ws_row, wp_row) -> dict:
    """以 wp_row 為主（1.5+），fallback 到 ws_row（Phase 1）."""
    def _pick(field_name, default=None):
        if wp_row is not None and wp_row[field_name] is not None:
            return wp_row[field_name]
        if ws_row is not None:
            try:
                return ws_row[field_name]
            except (KeyError, IndexError):
                return default
        return default

    return {
        "tier": _pick("tier", "excluded"),
        "trade_count_90d": int(_pick("trade_count_90d", 0) or 0),
        "resolved_count": int(_pick("resolved_count", 0) or 0),
        "win_rate": float(_pick("win_rate", 0.0) or 0.0),
        "cumulative_pnl": float(_pick("cumulative_pnl", 0.0) or 0.0),
        "avg_trade_size": float(_pick("avg_trade_size", 0.0) or 0.0),
        "last_trade_at": ws_row["last_trade_at"] if ws_row is not None else None,
        "last_computed_at": (
            wp_row["scanned_at"] if wp_row is not None else
            (ws_row["last_computed_at"] if ws_row is not None else None)
        ),
    }


def _extract_feature(features: dict, name: str) -> dict | None:
    """取出 feature dict 若存在（含 value/confidence/sample_size/notes）."""
    f = features.get(name)
    if not isinstance(f, dict):
        return None
    return {
        "feature_version": f.get("feature_version"),
        "value": f.get("value"),
        "confidence": f.get("confidence"),
        "sample_size": f.get("sample_size"),
        "notes": f.get("notes", ""),
    }


def _trade_row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "condition_id": r["condition_id"],
        "token_id": r["token_id"],
        "price": float(r["price"]) if r["price"] is not None else None,
        "size": float(r["size"]) if r["size"] is not None else None,
        "notional": float(r["notional"]) if r["notional"] is not None else None,
        "side": r["side"],
        "match_time": r["match_time"],
        "market_question": r["market_question"],
        "market_category": r["market_category"],
    }


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/profiles/{wallet}/history?limit=30
# 單一錢包的 profile 時序變化（畫像如何演進）
# ─────────────────────────────────────────────────────────────────────
@router.get("/profiles/{wallet}/history")
def wallet_profile_history(
    wallet: str,
    limit: int = Query(default=30, ge=1, le=200),
) -> dict:
    cache_key = f"wp_history:{wallet}:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    with _connect() as conn:
        rows = conn.execute(
            "SELECT wallet_address, scanner_version, scanned_at, tier, "
            "trade_count_90d, resolved_count, win_rate, cumulative_pnl, "
            "features_json, archetypes_json "
            "FROM wallet_profiles WHERE wallet_address=? "
            "ORDER BY scanned_at DESC LIMIT ?",
            (wallet, limit),
        ).fetchall()

    profiles = []
    for r in rows:
        features = _parse_json_field(r["features_json"], {})
        cat = (features.get("category_specialization", {}) or {}).get("value") or {}
        ts = (features.get("time_slice_consistency", {}) or {}).get("value") or {}
        profiles.append(
            {
                "scanner_version": r["scanner_version"],
                "scanned_at": r["scanned_at"],
                "tier": r["tier"],
                "trade_count_90d": r["trade_count_90d"],
                "resolved_count": r["resolved_count"],
                "win_rate": r["win_rate"],
                "cumulative_pnl": r["cumulative_pnl"],
                "primary_category": cat.get("primary_category"),
                "specialist_categories": cat.get("specialist_categories", []) or [],
                "is_consistent": ts.get("consistent"),
                "archetypes": _parse_json_field(r["archetypes_json"], []),
            }
        )

    result = {"wallet_address": wallet, "count": len(profiles), "profiles": profiles}
    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/tier-movers?hours=24&limit=10
# Phase A3: 最近 N 小時晉升鯨魚（dashboard overview 強化用）
# ─────────────────────────────────────────────────────────────────────
@router.get("/tier-movers")
def list_tier_movers(
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    cache_key = f"tier_movers:{hours}:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    _tier_rank = {"A": 3, "B": 2, "C": 1, "emerging": 0, "volatile": -1, "excluded": -2}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT th.wallet_address, th.from_tier, th.to_tier, th.changed_at, th.reason,
                   ws.cumulative_pnl, ws.win_rate, ws.trade_count_90d
            FROM whale_tier_history th
            LEFT JOIN whale_stats ws ON ws.wallet_address = th.wallet_address
            WHERE th.changed_at >= datetime('now', ?)
            ORDER BY th.id DESC LIMIT ?
            """,
            (f"-{hours} hours", limit * 3),  # over-fetch to filter promotions
        ).fetchall()

    movers: list[dict] = []
    for r in rows:
        prev_rank = _tier_rank.get(r["from_tier"] or "excluded", -2)
        new_rank = _tier_rank.get(r["to_tier"], -2)
        is_promotion = new_rank > prev_rank
        if not is_promotion:
            continue
        movers.append({
            "wallet_address": r["wallet_address"],
            "from_tier": r["from_tier"],
            "to_tier": r["to_tier"],
            "changed_at": r["changed_at"],
            "reason": r["reason"],
            "cumulative_pnl": float(r["cumulative_pnl"] or 0),
            "win_rate": float(r["win_rate"] or 0),
            "trade_count_90d": int(r["trade_count_90d"] or 0),
        })
        if len(movers) >= limit:
            break

    result = {"count": len(movers), "window_hours": hours, "movers": movers}
    _cache_set(cache_key, result, ttl=60.0)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/emerging-whales?limit=10
# Phase A3: emerging tier 錢包（剛崛起但樣本短的候選）
# ─────────────────────────────────────────────────────────────────────
@router.get("/emerging-whales")
def list_emerging_whales(
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    cache_key = f"emerging:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT wallet_address, tier, trade_count_90d, win_rate, cumulative_pnl,
                   avg_trade_size, resolved_count, last_trade_at, last_computed_at
            FROM whale_stats WHERE tier='emerging'
            ORDER BY cumulative_pnl DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    whales = [
        {
            "wallet_address": r["wallet_address"],
            "tier": r["tier"],
            "trade_count_90d": int(r["trade_count_90d"] or 0),
            "win_rate": float(r["win_rate"] or 0),
            "cumulative_pnl": float(r["cumulative_pnl"] or 0),
            "avg_trade_size": float(r["avg_trade_size"] or 0),
            "resolved_count": int(r["resolved_count"] or 0),
            "last_trade_at": r["last_trade_at"],
            "last_computed_at": r["last_computed_at"],
        }
        for r in rows
    ]
    result = {"count": len(whales), "whales": whales}
    _cache_set(cache_key, result, ttl=60.0)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/polymarket/steady-growers?limit=10
# Phase A3: 被 steady_growth feature 標記為 is_steady_grower=true 的錢包
# （資料來源為 wallet_profiles.features_json，需 Python 端 filter）
# ─────────────────────────────────────────────────────────────────────
@router.get("/steady-growers")
def list_steady_growers(
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    cache_key = f"steady_growers:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    with _connect() as conn:
        # 只取最新掃描；features_json LIKE 過濾 is_steady_grower=true 作粗篩，
        # Python 端再 parse 精確過濾
        rows = conn.execute(
            """
            SELECT wp.wallet_address, wp.tier, wp.scanned_at, wp.features_json,
                   wp.cumulative_pnl, wp.win_rate, wp.trade_count_90d, wp.resolved_count
            FROM wallet_profiles wp
            INNER JOIN (
                SELECT wallet_address, MAX(scanned_at) AS latest
                FROM wallet_profiles GROUP BY wallet_address
            ) latest
              ON wp.wallet_address = latest.wallet_address
             AND wp.scanned_at = latest.latest
            WHERE wp.features_json LIKE '%"is_steady_grower": true%'
            ORDER BY wp.cumulative_pnl DESC LIMIT ?
            """,
            (limit * 2,),  # over-fetch in case of false positives from LIKE
        ).fetchall()

    growers: list[dict] = []
    for r in rows:
        features = _parse_json_field(r["features_json"], {})
        sg = features.get("steady_growth") or {}
        sg_value = sg.get("value") or {}
        if not sg_value.get("is_steady_grower"):
            continue
        growers.append({
            "wallet_address": r["wallet_address"],
            "tier": r["tier"],
            "scanned_at": r["scanned_at"],
            "cumulative_pnl": float(r["cumulative_pnl"] or 0),
            "win_rate": float(r["win_rate"] or 0),
            "trade_count_90d": int(r["trade_count_90d"] or 0),
            "resolved_count": int(r["resolved_count"] or 0),
            "smoothness_score": float(sg_value.get("smoothness_score") or 0),
            "max_drawdown_ratio": float(sg_value.get("max_drawdown_ratio") or 0),
        })
        if len(growers) >= limit:
            break

    result = {"count": len(growers), "growers": growers}
    _cache_set(cache_key, result, ttl=60.0)
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
            tier_clause = f" AND a.tier IN ({placeholders})"
            params.extend(tiers)
    params.append(limit)

    # 1.5b: 一併 JOIN 出該錢包最新的 specialist 資訊與該交易市場的 category
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
              a.wallet_address, a.tx_hash, a.event_index, a.tier, a.condition_id,
              a.market_question, a.side, a.outcome, a.size, a.price, a.notional,
              a.match_time, a.alerted_at,
              m.category AS market_category,
              wp.features_json AS wp_features_json
            FROM whale_trade_alerts a
            LEFT JOIN markets m ON m.condition_id = a.condition_id
            LEFT JOIN (
                SELECT wp_inner.* FROM wallet_profiles wp_inner
                INNER JOIN (
                    SELECT wallet_address, MAX(scanned_at) AS latest
                    FROM wallet_profiles GROUP BY wallet_address
                ) latest
                  ON wp_inner.wallet_address = latest.wallet_address
                 AND wp_inner.scanned_at = latest.latest
            ) wp ON wp.wallet_address = a.wallet_address
            WHERE a.match_time >= datetime('now', ?)
            {tier_clause}
            ORDER BY a.match_time DESC LIMIT ?
            """,
            params,
        ).fetchall()

    alerts = []
    for r in rows:
        feats = _parse_json_field(r["wp_features_json"], {})
        cat = (feats.get("category_specialization", {}) or {}).get("value") or {}
        ts = (feats.get("time_slice_consistency", {}) or {}).get("value") or {}
        ts_conf = (feats.get("time_slice_consistency", {}) or {}).get("confidence")

        specialists = cat.get("specialist_categories", []) or []
        market_cat = r["market_category"] or ""
        match_specialist: bool | None = None
        if specialists:
            match_specialist = market_cat in specialists if market_cat else False

        alerts.append(
            {
                "wallet_address": r["wallet_address"],
                "tx_hash": r["tx_hash"],
                "event_index": r["event_index"],
                "tier": r["tier"],
                "condition_id": r["condition_id"],
                "market_question": r["market_question"],
                "market_category": market_cat,
                "side": r["side"],
                "outcome": r["outcome"],
                "size": r["size"],
                "price": r["price"],
                "notional": r["notional"],
                "match_time": r["match_time"],
                "alerted_at": r["alerted_at"],
                # 1.5b additions
                "specialist_categories": specialists,
                "primary_category": cat.get("primary_category"),
                "match_specialist": match_specialist,
                "is_consistent": ts.get("consistent") if ts_conf == "ok" else None,
            }
        )

    result = {"count": len(alerts), "alerts": alerts, "window_hours": hours}
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
