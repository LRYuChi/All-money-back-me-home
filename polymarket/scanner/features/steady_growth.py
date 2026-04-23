"""SteadyGrowthFeature — 錢包資產曲線平滑度（1.5b 新增）.

回答的問題：這個錢包的資產曲線是「階梯式穩定向上」還是「大漲大跌靠運氣」？

設計定位：
  - 目標 archetype：`steady_grower`（策略執行者）— 由 classify_archetypes (1.5c) 消費本特徵的 `is_steady_grower` 欄位
  - 計算基礎：Position.cash_pnl + end_date 重建的已實現 PnL 曲線（option A，不含 MTM）
  - 不做 tier 判定，只回傳結構化指標；tier 判定仍由 core_stats + stability_filter 負責

綜合平滑度分數：
    smoothness = 0.40 × R²(線性回歸)
               + 0.30 × min(gain/pain / cap, 1.0)
               + 0.30 × new_high_frequency_30d

觸發 is_steady_grower 的條件（全部必須滿足）：
    - smoothness ≥ min_smoothness_score
    - max_drawdown_ratio ≤ max_drawdown_ratio
    - longest_losing_streak ≤ max_longest_losing_streak
    - (可選) 每 30 天區段 PnL 皆 > 0

門檻讀自 pre_registered.yaml §0.3.1.steady_growth。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

import numpy as np

from polymarket.models import Position
from polymarket.scanner.features.base import BaseFeature, ScanContext
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


class SteadyGrowthFeature(BaseFeature):
    """資產曲線平滑度 — 辨識「策略執行者」型錢包."""

    name = "steady_growth"
    version = "1.0"
    min_samples = 20  # resolved positions

    def _compute(self, ctx: ScanContext) -> FeatureResult:
        cfg = ctx.pre_reg["scanner"]["features"]["thresholds"]["steady_growth"]
        min_resolved = int(cfg["min_resolved_positions"]["value"])
        min_smoothness = float(cfg["min_smoothness_score"]["value"])
        max_dd_ratio = float(cfg["max_drawdown_ratio"]["value"])
        max_streak = int(cfg["max_longest_losing_streak"]["value"])
        require_all_pos = bool(_v(cfg, "require_all_segments_positive", True))
        gtp_cap = float(cfg["gain_to_pain_cap"]["value"])
        weights = cfg.get("smoothness_weights") or {
            "r_squared": 0.40,
            "gain_to_pain": 0.30,
            "new_high_frequency": 0.30,
        }

        resolved = [p for p in ctx.positions if p.is_resolved and p.end_date is not None]
        if len(resolved) < min_resolved:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={
                    "is_steady_grower": False,
                    "reason": "insufficient_resolved",
                    "resolved_count": len(resolved),
                },
                confidence="low_samples",
                sample_size=len(resolved),
                notes=f"need >= {min_resolved} resolved positions, got {len(resolved)}",
            )

        dates, curve = _build_realized_pnl_curve(resolved, now=ctx.now, window_days=90)
        if not curve or len(curve) < 2:
            return FeatureResult(
                feature_name=self.name,
                feature_version=self.version,
                value={"is_steady_grower": False, "reason": "curve_too_short"},
                confidence="low_samples",
                sample_size=len(resolved),
                notes="equity curve reconstruction produced fewer than 2 daily points",
            )

        # Metrics
        r2 = _compute_r_squared(curve)
        max_dd_amount, max_dd_ratio_observed = _compute_max_drawdown(curve)
        gtp_raw = _compute_gain_to_pain_ratio(curve)
        gtp_norm = min(gtp_raw / gtp_cap, 1.0) if gtp_cap > 0 else 0.0
        nhf = _compute_new_high_frequency(curve, days=30)
        longest_streak = _compute_longest_losing_streak(resolved)
        segment_pnls = _compute_segment_pnls(resolved, now=ctx.now)
        all_segments_positive = all(s > 0 for s in segment_pnls)

        # Composite smoothness
        w_r2 = float(weights.get("r_squared", 0.40))
        w_gtp = float(weights.get("gain_to_pain", 0.30))
        w_nhf = float(weights.get("new_high_frequency", 0.30))
        total_w = w_r2 + w_gtp + w_nhf
        smoothness = (
            (w_r2 * r2 + w_gtp * gtp_norm + w_nhf * nhf) / total_w if total_w > 0 else 0.0
        )

        # Trigger logic
        passes_smoothness = smoothness >= min_smoothness
        passes_drawdown = max_dd_ratio_observed <= max_dd_ratio
        passes_streak = longest_streak <= max_streak
        passes_segments = all_segments_positive if require_all_pos else True
        is_steady = passes_smoothness and passes_drawdown and passes_streak and passes_segments

        return FeatureResult(
            feature_name=self.name,
            feature_version=self.version,
            value={
                "is_steady_grower": is_steady,
                "smoothness_score": round(smoothness, 4),
                "components": {
                    "r_squared": round(r2, 4),
                    "gain_to_pain_ratio": round(gtp_raw, 4),
                    "gain_to_pain_normalized": round(gtp_norm, 4),
                    "new_high_frequency_30d": round(nhf, 4),
                },
                "max_drawdown_ratio": round(max_dd_ratio_observed, 4),
                "max_drawdown_amount_usdc": round(max_dd_amount, 2),
                "longest_losing_streak": longest_streak,
                "segment_pnls_usdc": [round(s, 2) for s in segment_pnls],
                "all_segments_positive": all_segments_positive,
                "cumulative_pnl_usdc": round(curve[-1], 2),
                "curve_days": len(curve),
                "checks": {
                    "smoothness": passes_smoothness,
                    "drawdown": passes_drawdown,
                    "losing_streak": passes_streak,
                    "segments_positive": passes_segments,
                },
            },
            confidence="ok",
            sample_size=len(resolved),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pure math helpers (module-private; move to utility module if another feature
# needs them)
# ─────────────────────────────────────────────────────────────────────────────

def _v(node: dict, key: str, default):
    """Read `key.value` from a yaml node with `{value, set_at, rationale, ...}` leaves."""
    leaf = node.get(key)
    if isinstance(leaf, dict) and "value" in leaf:
        return leaf["value"]
    return leaf if leaf is not None else default


def _build_realized_pnl_curve(
    resolved: Sequence[Position],
    *,
    now: datetime,
    window_days: int = 90,
) -> tuple[list[date], list[float]]:
    """Rebuild daily cumulative realized PnL curve for positions within window.

    Only positions with `end_date` in [now - window_days, now] are counted.
    Curve starts at the earliest resolution date and carries forward daily until `now`.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window_start = (now - timedelta(days=window_days)).date()
    today = now.date()

    in_window = [
        p for p in resolved
        if p.end_date is not None and p.end_date.date() >= window_start
    ]
    if not in_window:
        return [], []

    daily: dict[date, float] = {}
    for p in in_window:
        d = p.end_date.date()  # type: ignore[union-attr]
        daily[d] = daily.get(d, 0.0) + float(p.cash_pnl)

    start_d = min(daily.keys())
    dates: list[date] = []
    values: list[float] = []
    cum = 0.0
    d = start_d
    while d <= today:
        if d in daily:
            cum += daily[d]
        dates.append(d)
        values.append(cum)
        d = d + timedelta(days=1)
    return dates, values


def _compute_r_squared(curve: Sequence[float]) -> float:
    """R² of linear fit on curve (curve vs index)."""
    if len(curve) < 3:
        return 0.0
    y = np.asarray(curve, dtype=np.float64)
    x = np.arange(len(y), dtype=np.float64)
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot == 0.0:
        return 1.0 if ss_res == 0.0 else 0.0
    return max(0.0, 1.0 - ss_res / ss_tot)


def _compute_max_drawdown(curve: Sequence[float]) -> tuple[float, float]:
    """Return (max_dd_amount, max_dd_ratio) where ratio is dd/peak_ever ∈ [0, 1]."""
    if not curve:
        return 0.0, 0.0
    peak = curve[0]
    max_dd = 0.0
    max_peak = peak
    for v in curve:
        peak = max(peak, v)
        max_peak = max(max_peak, peak)
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    if max_peak <= 0:
        return max_dd, 1.0 if max_dd > 0 else 0.0
    return max_dd, min(max_dd / max_peak, 1.0)


def _compute_gain_to_pain_ratio(curve: Sequence[float]) -> float:
    """Total gain divided by max drawdown amount. Returns cap 3.0 when no drawdown."""
    if not curve or len(curve) < 2:
        return 0.0
    total_gain = max(curve[-1] - curve[0], 0.0)
    dd_amount, _ = _compute_max_drawdown(curve)
    if dd_amount <= 0.0:
        return 3.0 if total_gain > 0 else 0.0
    return total_gain / dd_amount


def _compute_new_high_frequency(curve: Sequence[float], *, days: int = 30) -> float:
    """Fraction of last `days` days where curve hit a new all-time high."""
    if len(curve) < 2:
        return 0.0
    window = list(curve[-days:]) if len(curve) >= days else list(curve)
    if len(window) < 2:
        return 0.0
    peak = window[0]
    new_highs = 0
    for v in window[1:]:
        if v > peak:
            new_highs += 1
            peak = v
    return new_highs / (len(window) - 1)


def _compute_longest_losing_streak(resolved: Sequence[Position]) -> int:
    """Longest consecutive run of is_winning=False positions when sorted by end_date."""
    ordered = sorted(
        (p for p in resolved if p.end_date is not None),
        key=lambda p: p.end_date,  # type: ignore[arg-type]
    )
    max_streak = 0
    cur = 0
    for p in ordered:
        if p.is_winning is False:
            cur += 1
            max_streak = max(max_streak, cur)
        elif p.is_winning is True:
            cur = 0
    return max_streak


def _compute_segment_pnls(
    resolved: Sequence[Position],
    *,
    now: datetime,
    segment_days: int = 30,
    num_segments: int = 3,
) -> list[float]:
    """Per-segment sum of cash_pnl. Segment 0 = most recent 30 days."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    segments = [0.0] * num_segments
    for p in resolved:
        if p.end_date is None:
            continue
        age_days = (now - p.end_date).days
        seg_idx = age_days // segment_days
        if 0 <= seg_idx < num_segments:
            segments[seg_idx] += float(p.cash_pnl)
    return segments
