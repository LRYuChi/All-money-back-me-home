"""Polymarket Followers — 鯨魚發現後的決策層.

職責分工：
    Scanner     → 辨識「誰是鯨魚」（身份層）
    Followers   → 決定「要不要跟」+ 紙上跟單（決策/紀錄層）
    Executor    → 真實下單（Phase 3 才會有）

當前 Phase 1.5b 僅實作 Follower + Paper Book，**不執行任何真實下單**.
所有跟單訊號都只寫入 paper_trades 表，供手動驗證 + 未來 Phase 3 數據來源.
"""

from polymarket.followers.base import BaseFollower, FollowerDecision
from polymarket.followers.copy_whale import CopyWhaleFollower

REGISTRY: dict[str, BaseFollower] = {
    CopyWhaleFollower.name: CopyWhaleFollower(),
}


def get(name: str) -> BaseFollower | None:
    return REGISTRY.get(name)


def all_followers() -> list[BaseFollower]:
    return list(REGISTRY.values())


__all__ = ["BaseFollower", "FollowerDecision", "REGISTRY", "get", "all_followers"]
