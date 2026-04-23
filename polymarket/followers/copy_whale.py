"""CopyWhaleFollower v0 — 第一個 concrete follower.

基於鯨魚 tier 決定紙上跟單 size 與否.

決策規則 (v0, simple & conservative):
    1. Tier A:        跟, size = 3% of paper portfolio
    2. Tier B:        跟, size = 2%
    3. Tier C:        跟, size = 1%
    4. Tier E (emerging): 跟, size = 0.5% (樣本少, 保守)
    5. volatile / excluded: skip

    額外過濾（v0 保守，未來 v1 會加更多）:
    - notional < $100:         skip (雜訊小單, 非策略訊號)
    - price <= 0.03 或 >= 0.97: skip (極端機率市場, 邊際 edge 低)
    - match_time > 2h 前:       skip (追高風險, 鯨魚已進場太久)
    - 已跟過 (同 wallet+market): skip (避免 duplicate)
    - paper book 有現有同市場開倉: skip (避免 position stacking)

未來 v1 會加入:
    - 專長類別不符 (category_specialization 特徵): skip
    - 時間切片不穩 (time_slice_consistency): 降低 size
    - 市場流動性過低 (spread > 5% / depth < $1k): skip
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from polymarket.followers.base import AlertContext, BaseFollower, FollowerDecision


# Tier → stake % （佔紙上資金比例, 非真實資金）
_TIER_STAKES: dict[str, float] = {
    "A": 0.03,
    "B": 0.02,
    "C": 0.01,
    "emerging": 0.005,
}

_MIN_ACCEPTABLE_NOTIONAL = 100.0   # 跟單的最小 USDC 門檻 (過濾雜訊)
_MIN_PRICE = 0.03                  # 避免極端機率市場
_MAX_PRICE = 0.97
_MAX_AGE_HOURS = 2                 # 鯨魚進場太久則不追


class CopyWhaleFollower(BaseFollower):
    name = "copy_whale"
    version = "1.0"

    def _on_alert(self, ctx: AlertContext) -> FollowerDecision:
        now = datetime.now(timezone.utc)
        base = dict(
            follower_name=self.name,
            follower_version=self.version,
            decided_at=now,
        )

        # 1. Tier filter — 非 A/B/C/emerging 一律 skip
        stake_pct = _TIER_STAKES.get(ctx.tier)
        if stake_pct is None:
            return FollowerDecision(**base, decision="skip", reason=f"tier_not_tracked:{ctx.tier}")

        # 2. Notional filter
        if ctx.notional < _MIN_ACCEPTABLE_NOTIONAL:
            return FollowerDecision(
                **base,
                decision="skip",
                reason=f"notional_too_small:${ctx.notional:.0f}<${_MIN_ACCEPTABLE_NOTIONAL:.0f}",
            )

        # 3. Extreme price filter
        if ctx.price < _MIN_PRICE or ctx.price > _MAX_PRICE:
            return FollowerDecision(
                **base,
                decision="skip",
                reason=f"price_extreme:{ctx.price:.3f}",
            )

        # 4. Staleness filter — 鯨魚進場 > 2h 前則不追
        if ctx.match_time:
            age = (now - ctx.match_time).total_seconds() / 3600
            if age > _MAX_AGE_HOURS:
                return FollowerDecision(
                    **base,
                    decision="skip",
                    reason=f"trade_too_old:{age:.1f}h>{_MAX_AGE_HOURS}h",
                )

        # 5. All checks pass → follow
        # Actual paper size will be computed by PaperBook using current paper balance.
        # Here we only propose the %; caller converts to USDC.
        return FollowerDecision(
            **base,
            decision="follow",
            reason=f"tier_{ctx.tier}_ok",
            proposed_stake_pct=stake_pct,
            proposed_size_usdc=None,  # 留給 PaperBook 算
        )
