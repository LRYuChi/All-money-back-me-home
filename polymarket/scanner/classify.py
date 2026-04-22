"""Classify — 第四階段：從特徵向量產出 tier + archetype + risk_flags.

1.5a 範圍：
    - tier classification: 重用既有 features.whales.classify_tier 邏輯（不改變判斷）
    - archetype: 1.5a 不啟用，回傳 []
    - risk_flags: 1.5a 不啟用，回傳 []

1.5b 起 archetype 與 risk_flags 會逐步啟用。
"""

from __future__ import annotations

import logging
from typing import Any

from polymarket.features.whales import (
    TIER_EXCLUDED,
    WhaleStats,
    classify_tier as _legacy_classify_tier,
)
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


def classify_tier(
    *,
    passed_coarse: bool,
    core_stats: FeatureResult | None,
    pre_reg: dict[str, Any],
) -> tuple[str, bool]:
    """回傳 (tier, stability_pass)。沒通過粗篩或無核心統計 → 'excluded'."""
    if not passed_coarse:
        return TIER_EXCLUDED, False
    if core_stats is None or core_stats.value is None:
        return TIER_EXCLUDED, False

    v = core_stats.value
    stats = WhaleStats(
        wallet_address="",  # tier 判斷不需要
        trade_count_90d=int(v.get("trade_count_90d", 0)),
        win_rate=float(v.get("win_rate", 0.0)),
        cumulative_pnl=float(v.get("cumulative_pnl", 0.0)),
        avg_trade_size=float(v.get("avg_trade_size", 0.0)),
        resolved_count=int(v.get("resolved_count", 0)),
        segment_win_rates=list(v.get("segment_win_rates", [])),
    )
    tier = _legacy_classify_tier(stats, pre_reg=pre_reg)
    return tier, stats.stability_pass


def classify_archetypes(
    features: dict[str, FeatureResult],
    tier: str,
    pre_reg: dict[str, Any],
) -> list[str]:
    """1.5a 階段不啟用，回傳空。1.5c 起會根據各 feature 的判斷產出 multi-label."""
    return []


def detect_risk_flags(
    features: dict[str, FeatureResult],
    pre_reg: dict[str, Any],
) -> list[str]:
    """1.5a 階段不啟用，回傳空。1.5c 起會偵測 concentration_high、loss_loading 等."""
    return []
