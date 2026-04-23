"""PositionConfidenceFeature — 倉位大小與勝率的耦合分析 (1.5c.3).

回答的問題：這個錢包是「逢賭都 bet 一樣」還是「會在獲勝倉位下大注」？

核心指標：
  size_ratio_winners_over_losers = avg_notional(winners) / avg_notional(losers)
  > 1.0 代表錢包事後看贏的倉位下得比輸的大（可能反映進場時的 conviction）

設計邊界：
  - 這不是「真的」conviction 測量（需要 counterfactual data we don't have）
  - 只是一個事後統計：錢包下大注的地方剛好是贏的地方嗎？
  - 對「策略執行者」有意義；對「賭徒」或「martingale」可偵測異常：
    size_ratio < 0.9（反向：下大注給了輸的倉位）可能是 martingale 加碼

另輸出：
  notional_cv (coefficient of variation) — 倉位大小的變異係數
  可觀察「單一尺寸下注」(cv 低) vs「下注大小差異大」(cv 高)；供上游決定
  wallet 的 size discipline.

confidence：
  - 需要 min_winners + min_losers 都達標才回 ok，否則 low_samples.

未來擴展：
  - 結合 brier_calibration.calibration.buckets 計算「conviction-weighted alpha」
    — 但需 avg_price 分桶的 notional 加權 brier；延後到 1.5c.4 再處理.
"""

from __future__ import annotations

import logging
from statistics import pstdev, mean

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


class PositionConfidenceFeature(BaseFeature):
    """倉位大小 vs 勝率的耦合指標 — 是否「conviction sized」."""

    name = "position_confidence"
    version = "1.0"
    min_samples = 30

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        cfg = ctx.pre_reg["scanner"]["features"]["thresholds"]["position_confidence"]
        min_resolved = int(cfg["min_resolved_positions"]["value"])
        min_winners = int(cfg["min_winners"]["value"])
        min_losers = int(cfg["min_losers"]["value"])
        size_ratio_threshold = float(cfg["size_ratio_threshold"]["value"])

        resolved = [p for p in ctx.positions if p.is_resolved]
        n = len(resolved)

        if n < min_resolved:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={"is_confidence_sized": False, "reason": "insufficient_resolved", "n_settled": n},
                confidence="low_samples",
                sample_size=n,
                notes=f"need >= {min_resolved} resolved, got {n}",
            )

        winners: list[float] = []
        losers: list[float] = []
        all_notionals: list[float] = []
        for p in resolved:
            notional = abs(float(p.initial_value))
            if notional <= 0:
                continue
            all_notionals.append(notional)
            if bool(p.is_winning):
                winners.append(notional)
            else:
                losers.append(notional)

        if len(winners) < min_winners or len(losers) < min_losers:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "is_confidence_sized": False,
                    "reason": "insufficient_winners_or_losers",
                    "n_winners": len(winners),
                    "n_losers": len(losers),
                    "n_settled": n,
                },
                confidence="low_samples",
                sample_size=n,
                notes=f"need winners >= {min_winners} and losers >= {min_losers}; "
                f"got {len(winners)} / {len(losers)}",
            )

        winner_avg = mean(winners)
        loser_avg = mean(losers)
        size_ratio = winner_avg / loser_avg if loser_avg > 0 else float("inf")

        overall_avg = mean(all_notionals)
        overall_std = pstdev(all_notionals) if len(all_notionals) > 1 else 0.0
        notional_cv = (overall_std / overall_avg) if overall_avg > 0 else 0.0

        is_confidence_sized = size_ratio >= size_ratio_threshold
        # 反向警訊：下大注給輸的倉位（martingale 跡象）
        is_reverse_sized = size_ratio < (1.0 / size_ratio_threshold)

        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value={
                "is_confidence_sized": is_confidence_sized,
                "is_reverse_sized": is_reverse_sized,
                "size_ratio_winners_over_losers": round(size_ratio, 4),
                "winner_avg_notional": round(winner_avg, 2),
                "loser_avg_notional": round(loser_avg, 2),
                "n_winners": len(winners),
                "n_losers": len(losers),
                "n_settled": n,
                "avg_notional_overall": round(overall_avg, 2),
                "notional_std": round(overall_std, 2),
                "notional_cv": round(notional_cv, 4),
            },
            confidence="ok",
            sample_size=n,
        )
