"""CoreStatsFeature — 包裝既有 features.whales.compute_whale_stats.

1.5a 階段唯一的 feature。內容與 Phase 1 的 WhaleStats 相同，只是搬進 scanner
的特徵框架。確保新舊邏輯完全等價，不改變任何現有判斷。

從 1.5b 起會新增其他 feature；core_stats 不會被取代，而是作為其他 feature
依賴的基礎統計（例如 calibration 需要 trade list、time_slice 需要 segment_win_rates）。
"""

from __future__ import annotations

from polymarket.features.whales import compute_whale_stats
from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult


class CoreStatsFeature(BaseFeature):
    name = "core_stats"
    version = "1.0"
    min_samples = 1  # 任何資料都比沒資料好；low_samples 由各使用者自行判斷

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        stats = compute_whale_stats(
            ctx.wallet_address, ctx.trades, ctx.positions, now=ctx.now
        )
        value = {
            "trade_count_90d": stats.trade_count_90d,
            "resolved_count": stats.resolved_count,
            "win_rate": stats.win_rate,
            "cumulative_pnl": stats.cumulative_pnl,
            "avg_trade_size": stats.avg_trade_size,
            "segment_win_rates": stats.segment_win_rates,
            "stability_pass": stats.stability_pass,
            "last_trade_at": stats.last_trade_at.isoformat() if stats.last_trade_at else None,
        }
        confidence = "ok" if stats.trade_count_90d >= self.min_samples else "low_samples"
        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value=value,
            confidence=confidence,
            sample_size=stats.trade_count_90d,
            notes="" if stats.resolved_count > 0 else "no_resolved_positions",
        )
