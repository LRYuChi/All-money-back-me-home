"""Classify — 第四階段：從特徵向量產出 tier + archetype + risk_flags.

版本進展：
  1.5a: tier 重用 features.whales.classify_tier；archetype/risk 為 stub
  1.5b: 加 emerging tier（whales.py 側）；archetype/risk 仍 stub
  1.5c: archetype 啟用 — 從現有 features 產出 multi-label 畫像
  1.5d+: risk_flags（concentration_high / loss_loading 等）

Archetype 定義（1.5c.0）— 彼此不互斥、可共存：
  steady_grower       來自 steady_growth.value.is_steady_grower=True
  domain_specialist   來自 category_specialization.value.specialist_categories 非空
  consistent_trader   來自 time_slice_consistency.value.consistent=True

所有判定都要求 feature confidence='ok'；low_samples / unknown 的 feature 不貢獻 tag。
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

# Archetype 常數（供下游代碼 / 測試穩定引用）
ARCHETYPE_STEADY_GROWER = "steady_grower"
ARCHETYPE_DOMAIN_SPECIALIST = "domain_specialist"
ARCHETYPE_CONSISTENT_TRADER = "consistent_trader"

_ARCHETYPE_ORDER = (
    ARCHETYPE_STEADY_GROWER,
    ARCHETYPE_DOMAIN_SPECIALIST,
    ARCHETYPE_CONSISTENT_TRADER,
)


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
    """依 features 輸出 archetype multi-label. 排除 excluded tier 錢包.

    每個 archetype 對應單一 feature 的高置信正向判斷；未來可加入複合式 archetype
    （例如結合 brier_calibration + position_confidence 的 calibrated_sizer）.
    """
    # excluded 錢包基本上沒有 scanner features 足夠豐富；直接跳過
    if tier == TIER_EXCLUDED:
        return []

    tags: list[str] = []

    if _is_ok_feature_positive(features.get("steady_growth"), key="is_steady_grower"):
        tags.append(ARCHETYPE_STEADY_GROWER)

    if _has_specialists(features.get("category_specialization")):
        tags.append(ARCHETYPE_DOMAIN_SPECIALIST)

    if _is_ok_feature_positive(features.get("time_slice_consistency"), key="consistent"):
        tags.append(ARCHETYPE_CONSISTENT_TRADER)

    # 保證固定順序（便於 diff 與展示）
    return [t for t in _ARCHETYPE_ORDER if t in tags]


def _is_ok_feature_positive(feature: FeatureResult | None, *, key: str) -> bool:
    """feature.confidence=='ok' 且 value[key] is True."""
    if feature is None or feature.confidence != "ok":
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    return value.get(key) is True


def _has_specialists(feature: FeatureResult | None) -> bool:
    """category_specialization 有至少一個 specialist_categories entry."""
    if feature is None or feature.confidence != "ok":
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    specialists = value.get("specialist_categories")
    return isinstance(specialists, list) and len(specialists) > 0


def detect_risk_flags(
    features: dict[str, FeatureResult],
    pre_reg: dict[str, Any],
) -> list[str]:
    """1.5a 階段不啟用，回傳空。1.5c 起會偵測 concentration_high、loss_loading 等."""
    return []
