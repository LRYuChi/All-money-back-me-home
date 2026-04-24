"""Smart Money (Hyperliquid) 儀表板 API — 純讀取 Supabase sm_* 表.

資料來源：supabase/migrations/013_smart_money.sql + 015_smart_money_positions.sql
定義的 sm_wallets / sm_rankings / sm_wallet_trades / sm_paper_trades /
sm_skipped_signals / sm_wallet_positions 六張表。

Endpoints:
    GET /api/smart-money/status           — 最新 snapshot 狀態 + 覆蓋統計
    GET /api/smart-money/leaderboard      — 最新 snapshot 排行榜 (top N)
    GET /api/smart-money/signal-health    — Shadow 訊號管線健康 (P4c)

Supabase 未配置或 SDK 未裝時，回傳 {configured: false}，不拋 500。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/smart-money", tags=["smart-money"])

_CACHE_TTL = 60.0  # leaderboard 不需要 sub-minute 新鮮度
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


def _unavailable_payload() -> dict:
    return {
        "configured": False,
        "reason": "SUPABASE_URL / SUPABASE_KEY 未設定，或 supabase SDK 未安裝",
    }


# ─────────────────────────────────────────────────────────────────────
# GET /api/smart-money/status
# ─────────────────────────────────────────────────────────────────────
@router.get("/status")
def get_status() -> dict:
    """回傳最新 snapshot 日期 + 追蹤中的錢包總數 + 排名筆數."""
    if cached := _cache_get("status"):
        return cached

    sb = get_supabase()
    if sb is None:
        return _unavailable_payload()

    try:
        # 最新 snapshot_date
        latest = (
            sb.table("sm_rankings")
            .select("snapshot_date")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        latest_date = latest.data[0]["snapshot_date"] if latest.data else None

        # 當日排名筆數
        ranking_count = 0
        if latest_date:
            rc = (
                sb.table("sm_rankings")
                .select("id", count="exact")
                .eq("snapshot_date", latest_date)
                .execute()
            )
            ranking_count = rc.count or 0

        # 追蹤錢包總數
        wc = sb.table("sm_wallets").select("id", count="exact").execute()
        wallet_count = wc.count or 0

        result = {
            "configured": True,
            "latest_snapshot_date": latest_date,
            "ranking_count": ranking_count,
            "wallet_count": wallet_count,
        }
    except Exception as exc:
        logger.exception("smart-money status query failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"supabase query failed: {exc}") from exc

    _cache_set("status", result, ttl=30.0)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/smart-money/leaderboard?limit=50&snapshot_date=YYYY-MM-DD
# ─────────────────────────────────────────────────────────────────────
@router.get("/leaderboard")
def get_leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
    snapshot_date: str | None = Query(
        default=None,
        description="指定快照日期（YYYY-MM-DD）；不填則取最新",
    ),
) -> dict:
    """最新一次 ranking 快照的前 N 名（預設 50）."""
    cache_key = f"leaderboard:{snapshot_date or 'latest'}:{limit}"
    if cached := _cache_get(cache_key):
        return cached

    sb = get_supabase()
    if sb is None:
        return _unavailable_payload()

    try:
        target_date = snapshot_date
        if target_date is None:
            # 查最新 snapshot
            latest = (
                sb.table("sm_rankings")
                .select("snapshot_date")
                .order("snapshot_date", desc=True)
                .limit(1)
                .execute()
            )
            if not latest.data:
                return {
                    "configured": True,
                    "snapshot_date": None,
                    "count": 0,
                    "rankings": [],
                }
            target_date = latest.data[0]["snapshot_date"]

        # JOIN wallets + rankings 取地址 + 排名
        # Supabase-py 不直接支援 JOIN，用 relationship select
        resp = (
            sb.table("sm_rankings")
            .select(
                "rank, score, metrics, ai_analysis, "
                "sm_wallets(address, tags, last_active_at, notes)"
            )
            .eq("snapshot_date", target_date)
            .order("rank", desc=False)
            .limit(limit)
            .execute()
        )

        rankings = []
        for row in resp.data or []:
            wallet = row.get("sm_wallets") or {}
            rankings.append({
                "rank": row["rank"],
                "score": float(row["score"]),
                "address": wallet.get("address"),
                "tags": wallet.get("tags") or [],
                "last_active_at": wallet.get("last_active_at"),
                "notes": wallet.get("notes"),
                "metrics": row.get("metrics") or {},
                "ai_analysis": row.get("ai_analysis"),
            })

        result = {
            "configured": True,
            "snapshot_date": target_date,
            "count": len(rankings),
            "rankings": rankings,
        }
    except Exception as exc:
        logger.exception("smart-money leaderboard query failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"supabase query failed: {exc}") from exc

    _cache_set(cache_key, result)
    return result


# ─────────────────────────────────────────────────────────────────────
# GET /api/smart-money/signal-health
# ─────────────────────────────────────────────────────────────────────
_DENSITY_WINDOWS = [("1h", 1), ("6h", 6), ("24h", 24)]


def _percentile(sorted_values: list[int], p: float) -> int | None:
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


@router.get("/signal-health")
def get_signal_health() -> dict:
    """Shadow 訊號管線即時健康 (P4c).

    匯聚：
      - 1h / 6h / 24h paper trades + skipped signals 密度
      - 24h latency 百分位 (p50/p95/p99) — P4 Gate 指標
      - skipped_by_reason 分佈
      - 當前持倉 side 分佈

    Health 判定：
      - green  : 1h 內有 pipeline 活動（paper_trade 或 skipped_signal 任一）
      - yellow : 1h 靜默但 24h 有活動 (鯨魚 idle 正常)，或 p95 latency > 15s
      - red    : 24h 完全無活動（WS 可能死了 / 白名單全離線）
    """
    if cached := _cache_get("signal-health"):
        return cached

    sb = get_supabase()
    if sb is None:
        return _unavailable_payload()

    try:
        now = datetime.now(timezone.utc)

        # ---- Density: paper trades by window ----
        density: dict[str, dict[str, int | None]] = {}
        last_any: datetime | None = None
        for label, hours in _DENSITY_WINDOWS:
            since_iso = (now - timedelta(hours=hours)).isoformat()
            # paper trades
            pt = (
                sb.table("sm_paper_trades")
                .select("id,closed_at,opened_at", count="exact")
                .gte("opened_at", since_iso)
                .execute()
            )
            rows = pt.data or []
            paper_open = sum(1 for r in rows if r.get("closed_at") is None)
            paper_closed = sum(1 for r in rows if r.get("closed_at") is not None)
            if rows and hours == 24:
                last_paper = max(
                    datetime.fromisoformat(r["opened_at"].replace("Z", "+00:00"))
                    for r in rows
                )
                last_any = last_paper if last_any is None else max(last_any, last_paper)

            # skipped signals
            sk = (
                sb.table("sm_skipped_signals")
                .select("id,created_at", count="exact")
                .gte("created_at", since_iso)
                .execute()
            )
            sk_rows = sk.data or []
            skipped = len(sk_rows)
            if sk_rows and hours == 24:
                last_sk = max(
                    datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                    for r in sk_rows
                )
                last_any = last_sk if last_any is None else max(last_any, last_sk)

            density[label] = {
                "paper_open": paper_open,
                "paper_closed": paper_closed,
                "skipped": skipped,
            }

        # ---- Latency percentiles (24h) ----
        since_24h_iso = (now - timedelta(hours=24)).isoformat()
        lat_resp = (
            sb.table("sm_paper_trades")
            .select("signal_latency_ms")
            .gte("opened_at", since_24h_iso)
            .not_.is_("signal_latency_ms", "null")
            .execute()
        )
        lat_values = sorted(
            r["signal_latency_ms"] for r in (lat_resp.data or [])
            if r.get("signal_latency_ms") is not None
        )
        latency = {
            "n": len(lat_values),
            "p50_ms": _percentile(lat_values, 0.5),
            "p95_ms": _percentile(lat_values, 0.95),
            "p99_ms": _percentile(lat_values, 0.99),
        }

        # ---- Skip reason breakdown (24h) ----
        reasons_resp = (
            sb.table("sm_skipped_signals")
            .select("reason")
            .gte("created_at", since_24h_iso)
            .execute()
        )
        reasons: dict[str, int] = {}
        for r in reasons_resp.data or []:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1

        # ---- Position side distribution ----
        pos_resp = sb.table("sm_wallet_positions").select("wallet_id,side").execute()
        pos_rows = pos_resp.data or []
        positions = {"long": 0, "short": 0, "flat": 0}
        wallets_with_state: set[str] = set()
        for r in pos_rows:
            s = r.get("side") or "flat"
            positions[s] = positions.get(s, 0) + 1
            wallets_with_state.add(r["wallet_id"])
        positions["distinct_wallets"] = len(wallets_with_state)

        # ---- Health decision ----
        d1h = density["1h"]
        d24h = density["24h"]
        any_1h = d1h["paper_open"] + d1h["paper_closed"] + d1h["skipped"]
        any_24h = d24h["paper_open"] + d24h["paper_closed"] + d24h["skipped"]

        p95 = latency["p95_ms"]
        if any_24h == 0:
            health = "red"
            health_reason = "no pipeline activity in 24h"
        elif p95 is not None and p95 > 15_000:
            health = "yellow"
            health_reason = f"24h p95 latency {p95}ms exceeds 15s budget"
        elif any_1h == 0:
            health = "yellow"
            health_reason = "silent 1h (whales idle)"
        else:
            health = "green"
            health_reason = None

        result = {
            "configured": True,
            "checked_at": now.isoformat(),
            "health": health,
            "health_reason": health_reason,
            "last_activity_at": last_any.isoformat() if last_any else None,
            "density": density,
            "latency_24h": latency,
            "skipped_by_reason_24h": reasons,
            "positions": positions,
        }
    except Exception as exc:
        logger.exception("signal-health query failed: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"supabase query failed: {exc}"
        ) from exc

    _cache_set("signal-health", result, ttl=30.0)
    return result
