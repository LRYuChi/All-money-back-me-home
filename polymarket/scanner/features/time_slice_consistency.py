"""TimeSliceConsistencyFeature — 時間切片一致性 (1.5b 第二優先).

回答的問題：這個錢包的勝率是「穩定技能」還是「少數爆發撐場」？

計算邏輯：
    1. 將已結算倉位按 end_date 分配到 N 個等長時段（預設 3 段，每段 30 天）
    2. 計算每段的勝率（樣本不足的段標為 -1）
    3. 統計：mean、std、range、coefficient of variation
    4. 一致性判斷：std ≤ max_std_for_consistent → consistent

回傳資料結構：
    {
      "segments": [
        {"index": 0, "days_back": [0, 30],  "resolved": 12, "win_rate": 0.58},
        {"index": 1, "days_back": [30, 60], "resolved": 10, "win_rate": 0.60},
        {"index": 2, "days_back": [60, 90], "resolved": 8,  "win_rate": 0.625},
      ],
      "win_rate_mean": 0.60,
      "win_rate_std": 0.018,
      "win_rate_range": 0.045,
      "coefficient_of_variation": 0.030,
      "consistent": true,
      "valid_segments": 3,
    }

confidence 規則：
    - 不足 num_segments 個有效段（樣本足夠的段）→ low_samples
    - 有效段 < num_segments 但 ≥ 2 → ok（仍可給出部分判斷，consistent 可能為 false）
"""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta
from typing import Any

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


class TimeSliceConsistencyFeature(BaseFeature):
    name = "time_slice_consistency"
    version = "1.0"
    min_samples = 9  # 3 per segment × 3 segments；下方還會更嚴格判定

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        cfg = ctx.pre_reg["scanner"]["features"]["thresholds"]["time_slice_consistency"]
        segment_days = int(cfg["segment_days"]["value"])
        num_segments = int(cfg["num_segments"]["value"])
        min_per_seg = int(cfg["min_samples_per_segment"]["value"])
        max_std = float(cfg["max_std_for_consistent"]["value"])

        resolved = [p for p in ctx.positions if p.is_resolved and p.end_date is not None]

        # 分段：segment 0 = 最近 N 天，segment 1 = N~2N 天前 ...
        segments_data: list[dict[str, Any]] = []
        valid_rates: list[float] = []

        for i in range(num_segments):
            cutoff_recent = ctx.now - timedelta(days=i * segment_days)
            cutoff_old = ctx.now - timedelta(days=(i + 1) * segment_days)
            in_seg = [p for p in resolved if cutoff_old <= p.end_date < cutoff_recent]
            seg_resolved = len(in_seg)
            seg_wins = sum(1 for p in in_seg if p.is_winning)

            seg = {
                "index": i,
                "days_back": [i * segment_days, (i + 1) * segment_days],
                "resolved": seg_resolved,
                "wins": seg_wins,
            }

            if seg_resolved >= min_per_seg:
                rate = seg_wins / seg_resolved
                seg["win_rate"] = round(rate, 4)
                seg["sufficient"] = True
                valid_rates.append(rate)
            else:
                seg["win_rate"] = None
                seg["sufficient"] = False

            segments_data.append(seg)

        valid_count = len(valid_rates)

        if valid_count < 2:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "segments": segments_data,
                    "win_rate_mean": None,
                    "win_rate_std": None,
                    "win_rate_range": None,
                    "coefficient_of_variation": None,
                    "consistent": False,
                    "valid_segments": valid_count,
                },
                confidence="low_samples",
                sample_size=sum(s["resolved"] for s in segments_data),
                notes=f"only {valid_count} valid segments (need >= 2 for std)",
            )

        mean_rate = statistics.fmean(valid_rates)
        # population std for tiny n=2/3 ratios
        std_rate = statistics.pstdev(valid_rates)
        range_rate = max(valid_rates) - min(valid_rates)
        cov = (std_rate / mean_rate) if mean_rate > 0 else None
        consistent = std_rate <= max_std and valid_count >= num_segments

        confidence = "ok" if valid_count >= num_segments else "low_samples"
        notes = ""
        if valid_count < num_segments:
            notes = f"only {valid_count}/{num_segments} segments have enough samples"

        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value={
                "segments": segments_data,
                "win_rate_mean": round(mean_rate, 4),
                "win_rate_std": round(std_rate, 4),
                "win_rate_range": round(range_rate, 4),
                "coefficient_of_variation": round(cov, 4) if cov is not None else None,
                "consistent": bool(consistent),
                "valid_segments": valid_count,
            },
            confidence=confidence,
            sample_size=sum(s["resolved"] for s in segments_data),
            notes=notes,
        )
