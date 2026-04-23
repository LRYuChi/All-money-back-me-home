"""Classify — 第四階段：從特徵向量產出 tier + archetype + risk_flags.

版本進展：
  1.5a: tier 重用 features.whales.classify_tier；archetype/risk 為 stub
  1.5b: 加 emerging tier（whales.py 側）；archetype/risk 仍 stub
  1.5c: archetype 啟用 — 從現有 features 產出 multi-label 畫像
  1.5d+: risk_flags（concentration_high / loss_loading 等）

Archetype 定義 — 彼此不互斥、可共存：
  1.5c.0:
    steady_grower       steady_growth.is_steady_grower=True
    domain_specialist   category_specialization.specialist_categories 非空
    consistent_trader   time_slice_consistency.consistent=True
  1.5c.1:
    alpha_hunter        brier_calibration.market_edge ≥ 閾值 + 樣本充分

所有判定都要求 feature confidence='ok'；low_samples / unknown 的 feature 不貢獻 tag。
門檻值讀自 pre_registered.yaml §0.3.1.<feature>.archetype_<name>.
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
ARCHETYPE_ALPHA_HUNTER = "alpha_hunter"

_ARCHETYPE_ORDER = (
    ARCHETYPE_STEADY_GROWER,
    ARCHETYPE_DOMAIN_SPECIALIST,
    ARCHETYPE_CONSISTENT_TRADER,
    ARCHETYPE_ALPHA_HUNTER,
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

    if _is_alpha_hunter(features.get("brier_calibration"), pre_reg):
        tags.append(ARCHETYPE_ALPHA_HUNTER)

    # 保證固定順序（便於 diff 與展示）
    return [t for t in _ARCHETYPE_ORDER if t in tags]


def _is_alpha_hunter(
    feature: FeatureResult | None, pre_reg: dict[str, Any]
) -> bool:
    """brier_calibration feature 指示錢包有顯著 market edge.

    門檻：
      - feature.confidence == 'ok'
      - value.market_edge ≥ min_market_edge (default 0.08)
      - value.n_analyzed ≥ min_n_analyzed (default 30)
    """
    if feature is None or feature.confidence != "ok":
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    edge = value.get("market_edge")
    n_analyzed = value.get("n_analyzed")
    if edge is None or n_analyzed is None:
        return False

    try:
        cfg = (
            pre_reg["scanner"]["features"]["thresholds"]["brier_calibration"]
            ["archetype_alpha_hunter"]
        )
        min_edge = float(cfg["min_market_edge"]["value"])
        min_n = int(cfg["min_n_analyzed"]["value"])
    except (KeyError, TypeError):
        # 安全預設：門檻 yaml 遺失時使用保守值
        min_edge = 0.08
        min_n = 30

    return float(edge) >= min_edge and int(n_analyzed) >= min_n


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
