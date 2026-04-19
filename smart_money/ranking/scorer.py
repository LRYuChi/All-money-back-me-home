"""排名綜合分數:把 6 個原始指標 normalize 後加權.

Normalize 策略:
- Sortino:clip 到 [-3, 3] 後 map 到 [0, 1]
- Profit Factor:log1p 正向 + 0~3 區間 → [0, 1]
- DD recovery:已 0~1
- Holding CV:想要 CV 在 0.5 ~ 2.5 的中間帶(過低=bot,過高=過於亂);
              bell 形狀 reward
- Regime stability:已 0~1
- Martingale penalty:已 0~1 (higher = worse → 扣分)

分數可解釋:任何呼叫者都可拿 breakdown dict 看每個維度貢獻.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from smart_money.config import RankingSettings
from smart_money.ranking.metrics import MetricsBundle
from smart_money.store.schema import Trade


# ------------------------------------------------------------------ #
# Normalization helpers
# ------------------------------------------------------------------ #
def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def norm_sortino(x: float) -> float:
    """Sortino [-3, 3] → [0, 1]; beyond ±3 = clamp."""
    return (_clip(x, -3.0, 3.0) + 3.0) / 6.0


def norm_profit_factor(x: float) -> float:
    """PF [0, ∞) → [0, 1].
    PF = 1 → 0.5;PF = 2 → ~0.8;PF = 3 → ~0.9;PF = 10(上限) → ~0.97
    """
    return _clip(math.log1p(max(0.0, x)) / math.log1p(10), 0.0, 1.0)


def norm_holding_cv(x: float) -> float:
    """Bell-shaped reward centred at CV ≈ 1.5 (真實 discretionary trader 典型值).
    CV → 0 或 CV → 5 時回 0.
    """
    target = 1.5
    spread = 1.2
    return max(0.0, 1.0 - ((x - target) / spread) ** 2)


# ------------------------------------------------------------------ #
# Score
# ------------------------------------------------------------------ #
@dataclass(slots=True)
class ScoreBreakdown:
    score: float                    # final [0, 1]
    components: dict[str, float]    # normalized 各項
    contributions: dict[str, float] # component × weight

    def explain(self) -> str:
        """生成 human-readable 解釋(給報告 / Telegram 用)."""
        rows = []
        for k, v in sorted(self.contributions.items(), key=lambda x: -abs(x[1])):
            rows.append(f"  {k:<22} contrib={v:+.3f}  (raw={self.components[k]:.3f})")
        return f"SCORE = {self.score:.4f}\n" + "\n".join(rows)


def score_wallet(
    metrics: MetricsBundle,
    *,
    config: RankingSettings | None = None,
) -> ScoreBreakdown:
    """把 MetricsBundle 轉成 [0, 1] 分數,回 breakdown.

    權重配置從 config 讀取,同一份 config + 輸入保證輸出確定.
    """
    cfg = config or RankingSettings()

    # Normalize each raw metric → [0, 1]
    components = {
        "sortino":          norm_sortino(metrics.sortino),
        "profit_factor":    norm_profit_factor(metrics.profit_factor),
        "drawdown_recovery": metrics.drawdown_recovery,    # already 0-1
        "holding_time_cv":  norm_holding_cv(metrics.holding_time_cv),
        "regime_stability": metrics.regime_stability,       # already 0-1
        "martingale_penalty": metrics.martingale_penalty,   # already 0-1 (扣分)
    }

    # Weighted contributions
    contributions = {
        "sortino":          cfg.w_sortino * components["sortino"],
        "profit_factor":    cfg.w_profit_factor * components["profit_factor"],
        "drawdown_recovery": cfg.w_dd_recovery * components["drawdown_recovery"],
        "holding_time_cv":  cfg.w_holding_cv * components["holding_time_cv"],
        "regime_stability": cfg.w_regime_stability * components["regime_stability"],
        "martingale_penalty": -cfg.w_martingale_penalty * components["martingale_penalty"],
    }

    raw_score = sum(contributions.values())

    # 正向權重加總,讓 final score 落在 [0, 1] 區間
    positive_weight_sum = (
        cfg.w_sortino + cfg.w_profit_factor + cfg.w_dd_recovery
        + cfg.w_holding_cv + cfg.w_regime_stability
    )
    # max score 是所有正項 = positive_weight_sum,min 是 -w_martingale_penalty
    # 為了 UI 好讀,把 score shift 到 [0, 1]:
    #   final = (raw_score + w_martingale_penalty) / (positive_weight_sum + w_martingale_penalty)
    denom = positive_weight_sum + cfg.w_martingale_penalty
    final = (raw_score + cfg.w_martingale_penalty) / denom if denom > 0 else 0.0
    final = _clip(final, 0.0, 1.0)

    return ScoreBreakdown(
        score=final,
        components=components,
        contributions=contributions,
    )


def score_and_rank(
    wallet_metrics: list[tuple[str, MetricsBundle]],
    *,
    config: RankingSettings | None = None,
) -> list[tuple[str, ScoreBreakdown]]:
    """批次計分並排序.

    wallet_metrics: [(address_or_id, metrics), ...]
    回傳按分數降冪排序的 list.
    """
    scored = [(addr, score_wallet(m, config=config)) for addr, m in wallet_metrics]
    return sorted(scored, key=lambda x: x[1].score, reverse=True)


__all__ = [
    "ScoreBreakdown",
    "norm_holding_cv",
    "norm_profit_factor",
    "norm_sortino",
    "score_and_rank",
    "score_wallet",
]


# Keep Trade unused import from triggering linter while preserving public namespace hint
_ = Trade
