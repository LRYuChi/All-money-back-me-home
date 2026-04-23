"""Smart Money (Hyperliquid) 儀表板 API — 純讀取 Supabase sm_* 表.

資料來源：supabase/migrations/013_smart_money.sql 定義的 sm_wallets / sm_rankings /
sm_wallet_trades 三張表。Scanner CLI（smart_money/cli/*）定期寫入這些表；
本 router 只負責 UI 讀取，不執行掃描邏輯。

Endpoints:
    GET /api/smart-money/status       — 最新 snapshot 狀態 + 覆蓋統計
    GET /api/smart-money/leaderboard  — 最新 snapshot 排行榜 (top N)

Supabase 未配置或 SDK 未裝時，回傳 {configured: false}，不拋 500。
"""

from __future__ import annotations

import logging
import time
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
