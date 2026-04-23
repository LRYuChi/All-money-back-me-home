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
    """Sortino [-2, 2] → [0, 1]; beyond ±2 = clamp.
    Iteration 2: 收緊至 ±2,避免 grid bot 的極端 Sortino (e.g. 27+) 直接拿滿分.
    真實 discretionary trader 的 Sortino 多落在 0.5 ~ 2.5.
    """
    return (_clip(x, -2.0, 2.0) + 2.0) / 4.0


def norm_profit_factor(x: float) -> float:
    """PF [0, 5) → [0, 1].
    Iteration 2: 上限從 10 降至 5.PF=5 已屬頂級 discretionary trader;
    > 5 多為 grid bot 或小樣本過擬合(e.g. 收到 PF=186 的就是 bot).
    PF = 1 → 0.39, PF = 2 → 0.61, PF = 3 → 0.77, PF = 5+ → 1.0
    """
    return _clip(math.log1p(max(0.0, x)) / math.log1p(5), 0.0, 1.0)


def norm_holding_cv(x: float) -> float:
    """Bell-shaped reward centred at CV ≈ 1.5 (真實 discretionary trader 典型值).
    CV → 0 或 CV → 5 時回 0.
    """
    target = 1.5
    spread = 1.2
    return max(0.0, 1.0 - ((x - target) / spread) ** 2)


def bot_penalty(holding_cv: float, *, threshold: float = 0.5) -> float:
    """顯性 bot penalty (Iteration 2).

    cv < threshold 時線性扣分:
      cv = 0     → penalty = 1.0 (最重)
      cv = 0.25  → penalty = 0.5
      cv = 0.5+  → penalty = 0.0

    與 norm_holding_cv 的 bell curve 不同:
      - bell 是 "你拿多少正分",cv=0 時拿 0 分
      - bot_penalty 是 "你被扣多少",cv=0 時扣滿
    合起來:討喜的 cv 有小獎勵,極端 bot-like 有大懲罰.
    """
    if holding_cv >= threshold:
        return 0.0
    return 1.0 - (holding_cv / threshold)


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
        "bot_penalty":       bot_penalty(metrics.holding_time_cv),  # cv < 0.5 扣分
    }

    # Total negative-weight term (backward-compat with old RankingSettings missing w_bot_penalty)
    w_bot = getattr(cfg, "w_bot_penalty", 0.0)

    # Weighted contributions
    contributions = {
        "sortino":          cfg.w_sortino * components["sortino"],
        "profit_factor":    cfg.w_profit_factor * components["profit_factor"],
        "drawdown_recovery": cfg.w_dd_recovery * components["drawdown_recovery"],
        "holding_time_cv":  cfg.w_holding_cv * components["holding_time_cv"],
        "regime_stability": cfg.w_regime_stability * components["regime_stability"],
        "martingale_penalty": -cfg.w_martingale_penalty * components["martingale_penalty"],
        "bot_penalty":       -w_bot * components["bot_penalty"],
    }

    raw_score = sum(contributions.values())

    # 正向權重加總,讓 final score 落在 [0, 1] 區間
    positive_weight_sum = (
        cfg.w_sortino + cfg.w_profit_factor + cfg.w_dd_recovery
        + cfg.w_holding_cv + cfg.w_regime_stability
    )
    # max score 是所有正項 = positive_weight_sum,min 是 -(w_mart + w_bot)
    total_penalty_weight = cfg.w_martingale_penalty + w_bot
    denom = positive_weight_sum + total_penalty_weight
    final = (raw_score + total_penalty_weight) / denom if denom > 0 else 0.0
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
    "bot_penalty",
    "norm_holding_cv",
    "norm_profit_factor",
    "norm_sortino",
    "score_and_rank",
    "score_wallet",
]


# Keep Trade unused import from triggering linter while preserving public namespace hint
_ = Trade
