"""WalletProfileService — 統一的錢包畫像讀取介面.

設計目的：
    所有下游使用者（Telegram、API router、未來的決策層）都透過此服務取得
    錢包畫像，而不直接 query whale_stats 或 wallet_profiles 表。

    這層抽象的價值：
    - 隱藏「Phase 1（whale_stats）vs Phase 1.5+（wallet_profiles）」的雙表共存
    - 未來淘汰 whale_stats 時只需改此服務，下游無感
    - 統一 'low_samples / unknown' 的處理慣例
    - 單點加入 caching / batching 的擴充點

對外 API：
    get_profile(wallet_address) -> ProfileView | None
    list_profiles_by_tier(tiers) -> list[ProfileView]
    list_profile_history(wallet_address) -> list[ProfileView]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polymarket.scanner.profile import WalletProfile

logger = logging.getLogger(__name__)


@dataclass
class ProfileView:
    """對外暴露的合成型畫像。融合 wallet_profiles + whale_stats 的最佳資料.

    `data_source` 標示這份畫像主要來自哪張表，方便下游記錄歸因。
    1.5b 欄位（specialist / consistency）僅在 wallet_profiles 來源時填值，
    whale_stats fallback 時為 None / 空 list。
    """

    # 基礎欄位（兩表共有）
    wallet_address: str
    tier: str
    trade_count_90d: int
    resolved_count: int
    win_rate: float
    cumulative_pnl: float
    avg_trade_size: float
    archetypes: list[str]
    risk_flags: list[str]
    sample_size_warning: bool
    last_trade_at: str | None
    last_computed_at: str
    scanner_version: str | None  # None 表示來自 whale_stats
    data_source: str  # 'wallet_profiles' | 'whale_stats'

    # 1.5b 新增：領域專精
    primary_category: str | None = None
    specialist_categories: list[str] = field(default_factory=list)
    category_count: int = 0

    # 1.5b 新增：時間切片一致性
    is_consistent: bool | None = None  # None = low_samples 或無資料
    win_rate_std: float | None = None
    valid_segments: int = 0

    # 1.5b 新增：feature 信心度（讓 UI 知道哪個欄位可信）
    features_confidence: dict[str, str] = field(default_factory=dict)


class WalletProfileService:
    """單一進入點。建構時注入 repo，方便測試 mock."""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def get_profile(self, wallet_address: str) -> ProfileView | None:
        """取得單一錢包的當前最佳畫像.

        優先順序：
        1. wallet_profiles 最新一筆（任何 scanner_version）
        2. fallback 到 whale_stats（Phase 1 contract）
        3. 都沒有 → None
        """
        wp = self._repo.get_latest_wallet_profile(wallet_address)
        if wp:
            return self._from_wallet_profile_row(wp)

        ws = self._repo.get_whale_stats(wallet_address)
        if ws:
            return self._from_whale_stats_row(ws)
        return None

    def list_profiles_by_tier(
        self,
        tiers: list[str] | None = None,
        *,
        limit: int = 100,
    ) -> list[ProfileView]:
        """以 tier 過濾的清單。回傳合併兩表後的最佳畫像清單.

        當前實作：以 wallet_profiles 為主，缺少的錢包補 whale_stats。
        未來 wallet_profiles 完全覆蓋後可移除 fallback 分支。
        """
        tiers = tiers or ["A", "B", "C"]

        # Phase 1.5+ 的最新 profile
        wp_rows = self._repo.list_latest_wallet_profiles(tier=tiers, limit=limit)
        wp_addrs = {r["wallet_address"] for r in wp_rows}
        result: list[ProfileView] = [self._from_wallet_profile_row(r) for r in wp_rows]

        # 補齊只在 whale_stats 出現的錢包（Phase 1 only）
        ws_rows = self._repo.list_whales_by_tier(*tiers)
        for r in ws_rows:
            if r["wallet_address"] in wp_addrs:
                continue  # 已經有 v1.5+ 資料
            if len(result) >= limit:
                break
            result.append(self._from_whale_stats_row(r))

        return result[:limit]

    def list_profile_history(
        self, wallet_address: str, *, limit: int = 30
    ) -> list[ProfileView]:
        """單一錢包跨時間的 profile 變化（僅 wallet_profiles，whale_stats 沒時序）."""
        rows = self._repo.list_wallet_profile_history(wallet_address, limit=limit)
        return [self._from_wallet_profile_row(r) for r in rows]

    # === Private converters ===

    @staticmethod
    def _from_wallet_profile_row(row: dict) -> ProfileView:
        wp = WalletProfile.from_db_row(row)

        # 1.5b feature 抽取
        cat = wp.features.get("category_specialization")
        cat_value = (cat.value or {}) if cat else {}
        primary_category = cat_value.get("primary_category")
        specialist_categories = cat_value.get("specialist_categories", []) or []
        category_count = int(cat_value.get("category_count", 0))

        ts = wp.features.get("time_slice_consistency")
        ts_value = (ts.value or {}) if ts else {}
        # consistent 為 None 代表 low_samples，回傳 None 而非 False（保留語意）
        is_consistent = ts_value.get("consistent") if ts and ts.confidence == "ok" else None
        win_rate_std = ts_value.get("win_rate_std") if ts and ts.confidence == "ok" else None
        valid_segments = int(ts_value.get("valid_segments", 0))

        features_confidence = {name: fr.confidence for name, fr in wp.features.items()}

        return ProfileView(
            wallet_address=wp.wallet_address,
            tier=wp.tier,
            trade_count_90d=wp.trade_count_90d,
            resolved_count=wp.resolved_count,
            win_rate=wp.win_rate,
            cumulative_pnl=wp.cumulative_pnl,
            avg_trade_size=wp.avg_trade_size,
            archetypes=wp.archetypes,
            risk_flags=wp.risk_flags,
            sample_size_warning=wp.sample_size_warning,
            last_trade_at=_extract_last_trade_at(wp),
            last_computed_at=wp.scanned_at.isoformat(),
            scanner_version=wp.scanner_version,
            data_source="wallet_profiles",
            primary_category=primary_category,
            specialist_categories=specialist_categories,
            category_count=category_count,
            is_consistent=is_consistent,
            win_rate_std=win_rate_std,
            valid_segments=valid_segments,
            features_confidence=features_confidence,
        )

    @staticmethod
    def _from_whale_stats_row(row: dict) -> ProfileView:
        return ProfileView(
            wallet_address=row["wallet_address"],
            tier=row["tier"],
            trade_count_90d=int(row["trade_count_90d"] or 0),
            resolved_count=int(row.get("resolved_count") or 0),
            win_rate=float(row["win_rate"] or 0.0),
            cumulative_pnl=float(row["cumulative_pnl"] or 0.0),
            avg_trade_size=float(row["avg_trade_size"] or 0.0),
            archetypes=[],         # Phase 1 沒這個概念
            risk_flags=[],         # Phase 1 沒這個概念
            sample_size_warning=False,
            last_trade_at=row.get("last_trade_at"),
            last_computed_at=row["last_computed_at"],
            scanner_version=None,  # 來自 Phase 1，無 scanner_version
            data_source="whale_stats",
        )


def _extract_last_trade_at(wp: WalletProfile) -> str | None:
    """從 features.core_stats 中取 last_trade_at（如果有）."""
    core = wp.features.get("core_stats")
    if core and core.value and isinstance(core.value, dict):
        return core.value.get("last_trade_at")
    return None
