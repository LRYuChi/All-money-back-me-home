"""BrierCalibrationFeature — 機率校準與 alpha 量化 (1.5c.1).

回答的問題：這個錢包「賭錯方向的機率是多少？」以及「市場在他下注的倉位上有多大誤差？」

核心概念 — p 為進場時的隱含機率（= 倉位 avg_price），o ∈ {0, 1} 為實際結果：
  Brier = (1/N) Σ (p - o)²
  Market Edge = actual_win_rate - weighted_avg_entry_price
  Calibration Error = Σ w_k · |avg_implied_k - actual_wr_k|（依 bucket 加權）

語義注意：
  - 這裡 Brier 測的是「市場」在錢包下注位置上的 Brier（不是錢包自己預測的 Brier）
  - 對錢包而言 Brier 越大越好（市場越錯，錢包抓到 inefficient 機會）
  - 對策略而言 Brier 越小越好（策略的 forecast 對得越準）—— 語義相反！
    因此這個 feature 只提供數字，不套用 strategy_promotion 的門檻做硬判定。
  - archetype 層才決定如何解讀（alpha_hunter 要求 market_edge ≥ 閾值 + 充分樣本）

回傳結構：
  {
    brier_score: float,
    avg_entry_price: float,
    actual_win_rate: float,
    market_edge: float,
    calibration: {
      weighted_abs_error: float,
      buckets: [
        {range: [lo, hi], n: int, avg_implied: float, actual_wr: float, diff: float},
        ...
      ],
    },
    n_settled: int,
    reference_strategy_brier_threshold: float,  # 純參考用，不作硬判定
  }

confidence 規則：
  - n_settled < min_resolved_positions → low_samples
  - 否則 ok
"""

from __future__ import annotations

import logging
from typing import Sequence

from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


class BrierCalibrationFeature(BaseFeature):
    """機率校準 / Brier / market_edge."""

    name = "brier_calibration"
    version = "1.0"
    min_samples = 30  # resolved positions — Brier 要統計顯著需要 ≥ 30 筆

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        cfg = ctx.pre_reg["scanner"]["features"]["thresholds"]["brier_calibration"]
        min_resolved = int(cfg["min_resolved_positions"]["value"])
        buckets_cfg: Sequence[Sequence[float]] = cfg["buckets"]["value"]
        min_per_bucket = int(cfg["min_samples_per_bucket"]["value"])
        ref_strategy_brier = float(cfg["reference_strategy_brier_threshold"]["value"])

        resolved = [p for p in ctx.positions if p.is_resolved]
        n = len(resolved)
        if n < min_resolved:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "brier_score": None,
                    "avg_entry_price": None,
                    "actual_win_rate": None,
                    "market_edge": None,
                    "calibration": {"weighted_abs_error": None, "buckets": []},
                    "n_settled": n,
                    "reference_strategy_brier_threshold": ref_strategy_brier,
                },
                confidence="low_samples",
                sample_size=n,
                notes=f"need >= {min_resolved} resolved positions, got {n}",
            )

        # 準備 (p, o, weight) 三元組。weight = initial_value（notional-weighted）
        triples: list[tuple[float, int, float]] = []
        for p in resolved:
            price = float(p.avg_price)
            if price <= 0 or price >= 1:
                # 極端價格（0 或 1）表示進場時已接近結算，無 Brier 意義
                continue
            o = 1 if bool(p.is_winning) else 0
            w = abs(float(p.initial_value)) or 1.0
            triples.append((price, o, w))

        if not triples:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "brier_score": None,
                    "avg_entry_price": None,
                    "actual_win_rate": None,
                    "market_edge": None,
                    "calibration": {"weighted_abs_error": None, "buckets": []},
                    "n_settled": n,
                    "reference_strategy_brier_threshold": ref_strategy_brier,
                },
                confidence="low_samples",
                sample_size=n,
                notes="all resolved positions had extreme entry prices (0 or 1)",
            )

        # Brier (unweighted mean — 與 strategy_promotion formula 對齊)
        brier = sum((p - o) ** 2 for p, o, _ in triples) / len(triples)

        # Notional-weighted avg entry price (for market_edge 計算)
        total_w = sum(w for _, _, w in triples)
        weighted_entry = sum(p * w for p, _, w in triples) / total_w

        actual_wr = sum(o for _, o, _ in triples) / len(triples)
        market_edge = actual_wr - weighted_entry

        # Calibration buckets
        cal_buckets = _compute_calibration(
            triples, buckets=buckets_cfg, min_samples=min_per_bucket
        )
        weighted_abs_error = _weighted_calibration_error(cal_buckets)

        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value={
                "brier_score": round(brier, 4),
                "avg_entry_price": round(weighted_entry, 4),
                "actual_win_rate": round(actual_wr, 4),
                "market_edge": round(market_edge, 4),
                "calibration": {
                    "weighted_abs_error": round(weighted_abs_error, 4)
                    if weighted_abs_error is not None
                    else None,
                    "buckets": cal_buckets,
                },
                "n_settled": n,
                "n_analyzed": len(triples),  # 排除極端價格後的實際分析數
                "reference_strategy_brier_threshold": ref_strategy_brier,
            },
            confidence="ok",
            sample_size=len(triples),
        )


def _compute_calibration(
    triples: list[tuple[float, int, float]],
    *,
    buckets: Sequence[Sequence[float]],
    min_samples: int,
) -> list[dict]:
    """依 p 分桶計算每桶實際勝率 vs 平均隱含機率."""
    result: list[dict] = []
    for bucket_range in buckets:
        lo, hi = float(bucket_range[0]), float(bucket_range[1])
        in_bucket = [(p, o, w) for p, o, w in triples if lo <= p < hi]
        # 末桶包含右端點
        if hi == 1.0:
            in_bucket = [(p, o, w) for p, o, w in triples if lo <= p <= hi]
        n = len(in_bucket)
        if n < min_samples:
            result.append({
                "range": [lo, hi],
                "n": n,
                "avg_implied": None,
                "actual_wr": None,
                "diff": None,
                "sufficient": False,
            })
            continue
        avg_implied = sum(p for p, _, _ in in_bucket) / n
        actual_wr = sum(o for _, o, _ in in_bucket) / n
        diff = actual_wr - avg_implied
        result.append({
            "range": [lo, hi],
            "n": n,
            "avg_implied": round(avg_implied, 4),
            "actual_wr": round(actual_wr, 4),
            "diff": round(diff, 4),
            "sufficient": True,
        })
    return result


def _weighted_calibration_error(buckets: list[dict]) -> float | None:
    """|avg_implied - actual_wr| 加權平均（權重 = 桶內樣本數）."""
    total_n = sum(b["n"] for b in buckets if b["sufficient"])
    if total_n == 0:
        return None
    weighted = sum(
        abs(b["diff"]) * b["n"] for b in buckets if b["sufficient"]
    )
    return weighted / total_n
