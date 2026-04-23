"""Classify — 第四階段：從特徵向量產出 tier + archetype + risk_flags.

版本進展：
  1.5a: tier 重用 features.whales.classify_tier；archetype/risk 為 stub
  1.5b: 加 emerging tier（whales.py 側）；archetype/risk 仍 stub
  1.5c: archetype 啟用 — 從現有 features 產出 multi-label 畫像
  1.5c.2: risk_flags 啟用（concentration_high / loss_loading / wash_trade_suspicion）

Archetype 定義 — 彼此不互斥、可共存：
  1.5c.0:
    steady_grower       steady_growth.is_steady_grower=True
    domain_specialist   category_specialization.specialist_categories 非空
    consistent_trader   time_slice_consistency.consistent=True
  1.5c.1:
    alpha_hunter        brier_calibration.market_edge ≥ 閾值 + 樣本充分

Risk flags（告警 — 跟單前的警示訊號）：
  1.5c.2:
    concentration_high  單一類別佔總 notional > 80%（過度集中）
    loss_loading        最近 30d 段 PnL 顯著比前兩段差（策略正在失效）
    wash_trade_suspicion category_specialization 揭示同一類別下多倉對敲跡象

所有 archetype 判定都要求 feature confidence='ok'。
Risk flags 比較寬鬆：只要 feature value 有足夠資訊就可觸發（為了不遺漏警示）。
門檻值讀自 pre_registered.yaml §0.3.1.<feature>.archetype_<name> / risk_<name>.
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

# Risk flag 常數
RISK_CONCENTRATION_HIGH = "concentration_high"
RISK_LOSS_LOADING = "loss_loading"
RISK_WASH_TRADE_SUSPICION = "wash_trade_suspicion"

_RISK_ORDER = (
    RISK_CONCENTRATION_HIGH,
    RISK_LOSS_LOADING,
    RISK_WASH_TRADE_SUSPICION,
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
    """依 features 輸出 risk flags. 多標籤、彼此獨立."""
    tags: list[str] = []

    if _is_concentration_high(features.get("category_specialization"), pre_reg):
        tags.append(RISK_CONCENTRATION_HIGH)

    if _is_loss_loading(features.get("steady_growth"), pre_reg):
        tags.append(RISK_LOSS_LOADING)

    if _is_wash_trade_suspicion(features.get("category_specialization"), pre_reg):
        tags.append(RISK_WASH_TRADE_SUSPICION)

    return [t for t in _RISK_ORDER if t in tags]


def _is_concentration_high(
    feature: FeatureResult | None, pre_reg: dict[str, Any]
) -> bool:
    """單一類別 notional 佔比 > 閾值（預設 80%）→ 集中度過高.

    使用 category_specialization.value.categories[*].notional.
    confidence=low_samples 時跳過（類別資料不足以判斷）.
    """
    if feature is None or feature.confidence != "ok":
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    cats = value.get("categories") or {}
    if not isinstance(cats, dict) or not cats:
        return False

    threshold = _get_risk_threshold(
        pre_reg, "category_specialization", "risk_concentration_high", "max_share", 0.80
    )

    # 用 notional 算佔比；排除 unknown category 的貢獻（分母與分子都排除）
    known_notional = 0.0
    max_notional = 0.0
    for name, stat in cats.items():
        if not isinstance(stat, dict):
            continue
        if name == "(unknown)":
            continue
        n = float(stat.get("notional") or 0)
        known_notional += n
        if n > max_notional:
            max_notional = n
    if known_notional <= 0:
        return False
    return (max_notional / known_notional) > threshold


def _is_loss_loading(
    feature: FeatureResult | None, pre_reg: dict[str, Any]
) -> bool:
    """最近 30d segment PnL 顯著比前兩段差 → 策略失效中.

    觸發條件：segment_pnls[0] 負值 且 |segment_pnls[0]| ≥ max(segment_pnls[1], segment_pnls[2]) × 閾值.
    或更簡：segment_pnls[0] < 0 AND 其他兩段皆正.
    """
    if feature is None or feature.confidence not in ("ok", "low_samples"):
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    segs = value.get("segment_pnls_usdc")
    if not isinstance(segs, list) or len(segs) < 3:
        return False

    try:
        s0, s1, s2 = float(segs[0]), float(segs[1]), float(segs[2])
    except (TypeError, ValueError):
        return False

    _ = _get_risk_threshold(
        pre_reg, "steady_growth", "risk_loss_loading", "min_prior_positive_count", 2
    )  # threshold currently unused in simple rule but registered

    # 規則：最近一段虧損，且前兩段中至少兩段為正值
    if s0 >= 0:
        return False
    prior_positive = sum(1 for s in (s1, s2) if s > 0)
    return prior_positive >= 2


def _is_wash_trade_suspicion(
    feature: FeatureResult | None, pre_reg: dict[str, Any]
) -> bool:
    """category_specialization 顯示某類別的 resolved 多但 win_rate ≈ 0.5 且 notional 集中.

    粗略啟發：單一類別 resolved ≥ N 且 win_rate 落在 [0.45, 0.55] 且佔比高.
    """
    if feature is None or feature.confidence != "ok":
        return False
    value = feature.value or {}
    if not isinstance(value, dict):
        return False
    cats = value.get("categories") or {}
    if not isinstance(cats, dict):
        return False

    min_resolved = int(_get_risk_threshold(
        pre_reg, "category_specialization", "risk_wash_trade_suspicion",
        "min_category_resolved", 20
    ))
    lo = float(_get_risk_threshold(
        pre_reg, "category_specialization", "risk_wash_trade_suspicion",
        "win_rate_lower", 0.45
    ))
    hi = float(_get_risk_threshold(
        pre_reg, "category_specialization", "risk_wash_trade_suspicion",
        "win_rate_upper", 0.55
    ))
    min_share = float(_get_risk_threshold(
        pre_reg, "category_specialization", "risk_wash_trade_suspicion",
        "min_notional_share", 0.60
    ))

    total_notional = sum(
        float(s.get("notional") or 0) for s in cats.values()
        if isinstance(s, dict)
    )
    if total_notional <= 0:
        return False

    for name, stat in cats.items():
        if not isinstance(stat, dict) or name == "(unknown)":
            continue
        if int(stat.get("resolved") or 0) < min_resolved:
            continue
        wr = float(stat.get("win_rate") or 0)
        if not (lo <= wr <= hi):
            continue
        notional = float(stat.get("notional") or 0)
        if notional / total_notional >= min_share:
            return True
    return False


def _get_risk_threshold(
    pre_reg: dict[str, Any],
    feature_name: str,
    risk_name: str,
    field_name: str,
    default: Any,
) -> Any:
    """讀 pre_reg.scanner.features.thresholds[feature].risk_[name].[field].value.

    yaml 缺失時回 default（保守預設）。
    """
    try:
        node = (
            pre_reg["scanner"]["features"]["thresholds"][feature_name][risk_name]
            [field_name]
        )
        if isinstance(node, dict) and "value" in node:
            return node["value"]
        return node
    except (KeyError, TypeError):
        return default
