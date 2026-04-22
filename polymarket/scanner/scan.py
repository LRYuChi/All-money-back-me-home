"""Scan — Scanner 主流程編排.

呼叫順序：
    Discovery → 對每個候選錢包：[Coarse Filter → Features → Classify → 寫 WalletProfile]

設計原則：
    - 單一錢包的失敗不應拖垮整批掃描（個別 try/except）
    - 每個階段的中間結果都記在 raw_features 供未來歸因
    - Scanner 只「產出 + 寫入」，不負責決定何時呼叫（pipeline.py 的責任）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from polymarket.config import load_pre_registered
from polymarket.models import Position, Trade
from polymarket.scanner import SCANNER_VERSION
from polymarket.scanner.classify import (
    classify_archetypes,
    classify_tier,
    detect_risk_flags,
)
from polymarket.scanner.coarse_filter import apply_coarse_filter
from polymarket.scanner.features import REGISTRY, get as get_feature
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.profile import FeatureResult, WalletProfile

logger = logging.getLogger(__name__)


def scan_wallet(
    wallet_address: str,
    trades: list[Trade],
    positions: list[Position],
    *,
    pre_reg: dict[str, Any] | None = None,
    market_categories: dict[str, str] | None = None,
    now: datetime | None = None,
) -> WalletProfile:
    """掃描單一錢包，回傳 WalletProfile.

    純函式：相同輸入永遠產生相同輸出（除了 scanned_at 來自 now 參數）。
    上層呼叫者負責處理 IO（拉 trades/positions、寫 DB）。
    """
    pre_reg = pre_reg or load_pre_registered()
    now = now or datetime.now(timezone.utc)
    market_categories = market_categories or {}

    # 驗證 SCANNER_VERSION 與 yaml 對齊
    yaml_version = pre_reg.get("scanner", {}).get("version", {}).get("value")
    if yaml_version != SCANNER_VERSION:
        logger.warning(
            "SCANNER_VERSION mismatch: code=%s yaml=%s. yaml takes precedence.",
            SCANNER_VERSION,
            yaml_version,
        )

    # === Stage 2: Coarse Filter ===
    cf = apply_coarse_filter(wallet_address, trades, positions, pre_reg, now=now)

    # === Stage 3: Features ===
    ctx = ScanContext(
        wallet_address=wallet_address,
        trades=trades,
        positions=positions,
        now=now,
        pre_reg=pre_reg,
        market_categories=market_categories,
    )
    features: dict[str, FeatureResult] = {}
    enabled_names = _enabled_features(pre_reg)
    for fname in enabled_names:
        feature = get_feature(fname)
        if feature is None:
            logger.warning("feature '%s' enabled in yaml but not in REGISTRY", fname)
            continue
        features[fname] = feature.compute(ctx)

    # === Stage 4: Classify ===
    core = features.get("core_stats")
    tier, stability_pass = classify_tier(
        passed_coarse=cf.passed, core_stats=core, pre_reg=pre_reg
    )
    archetypes = classify_archetypes(features, tier, pre_reg)
    risk_flags = detect_risk_flags(features, pre_reg)

    # === Build WalletProfile ===
    if core and core.value:
        cv = core.value
        trade_count = int(cv.get("trade_count_90d", 0))
        resolved_count = int(cv.get("resolved_count", 0))
        cumulative_pnl = float(cv.get("cumulative_pnl", 0.0))
        avg_trade_size = float(cv.get("avg_trade_size", 0.0))
        win_rate = float(cv.get("win_rate", 0.0))
        raw_features = {
            "segment_win_rates": cv.get("segment_win_rates", []),
            "stability_pass": stability_pass,
        }
    else:
        trade_count = resolved_count = 0
        cumulative_pnl = avg_trade_size = win_rate = 0.0
        raw_features = {}

    sample_warning = (
        trade_count < int(pre_reg["scanner"]["coarse_filter"]["min_trades_total"]["value"])
        or resolved_count < 3
    )

    return WalletProfile(
        wallet_address=wallet_address,
        scanner_version=SCANNER_VERSION,
        scanned_at=now,
        passed_coarse_filter=cf.passed,
        coarse_filter_reasons=cf.reasons,
        trade_count_90d=trade_count,
        resolved_count=resolved_count,
        cumulative_pnl=cumulative_pnl,
        avg_trade_size=avg_trade_size,
        win_rate=win_rate,
        features=features,
        tier=tier,
        archetypes=archetypes,
        risk_flags=risk_flags,
        sample_size_warning=sample_warning,
        raw_features=raw_features,
    )


def _enabled_features(pre_reg: dict[str, Any]) -> list[str]:
    """從 yaml 讀取當前 SCANNER_VERSION 啟用哪些 feature."""
    enabled_map = pre_reg.get("scanner", {}).get("features", {}).get("enabled_in_version", {})
    enabled = enabled_map.get(SCANNER_VERSION)
    if enabled is None:
        # Fallback: 用所有註冊的 features
        logger.warning(
            "no enabled_in_version[%s] in yaml; falling back to all registered features",
            SCANNER_VERSION,
        )
        return list(REGISTRY.keys())
    return list(enabled)
