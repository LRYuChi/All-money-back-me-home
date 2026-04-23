"""BaseFollower — 所有 follower 的合約.

合約語意：
    follower.on_alert(ctx) 接受一筆鯨魚交易 alert, 回傳 FollowerDecision.
    decision='follow' → 紀錄到 paper_trades, 推播加標記
    decision='skip'   → 條件不符, 不動作
    decision='veto'   → 強烈反對 (reason 帶細節), 不動作但記入 follower_decisions
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


Decision = Literal["follow", "skip", "veto"]


@dataclass
class AlertContext:
    """丟給 follower 決策用的完整訊息。都是已存在 DB 的資料."""

    wallet_address: str
    tx_hash: str
    event_index: int
    tier: str
    condition_id: str | None
    market_question: str | None
    market_category: str | None
    outcome: str
    side: str  # 'BUY' | 'SELL'
    price: float
    size: float
    notional: float
    match_time: datetime
    # 錢包畫像（從 WalletProfileService 取得，可能為 None）
    wallet_profile: dict | None = None


@dataclass
class FollowerDecision:
    """follower 的決策輸出。無論 follow/skip/veto 都會記錄到 follower_decisions."""

    follower_name: str
    follower_version: str
    decision: Decision
    reason: str  # 人類可讀，簡短
    decided_at: datetime

    # 若 decision='follow'，填下列
    proposed_stake_pct: float | None = None       # 佔紙上資金百分比 (0.01 = 1%)
    proposed_size_usdc: float | None = None       # 絕對金額 (USDC)

    def is_follow(self) -> bool:
        return self.decision == "follow"

    def to_db_dict(self, source: AlertContext, paper_trade_id: int | None = None) -> dict[str, Any]:
        return {
            "follower_name": self.follower_name,
            "follower_version": self.follower_version,
            "decided_at": self.decided_at.isoformat(),
            "source_wallet": source.wallet_address,
            "source_tx_hash": source.tx_hash,
            "source_event_index": source.event_index,
            "source_tier": source.tier,
            "decision": self.decision,
            "reason": self.reason,
            "proposed_stake_pct": self.proposed_stake_pct,
            "proposed_size_usdc": self.proposed_size_usdc,
            "paper_trade_id": paper_trade_id,
        }


class BaseFollower(ABC):
    """所有 follower 的基底. 外層保證不會向上丟例外."""

    name: str = ""
    version: str = "0.0"

    def on_alert(self, ctx: AlertContext) -> FollowerDecision:
        """頂層入口, 包住例外."""
        if not self.name:
            raise NotImplementedError(f"{type(self).__name__} 必須設定 name")
        try:
            return self._on_alert(ctx)
        except Exception as exc:
            logger.exception("follower %s failed for %s", self.name, ctx.wallet_address)
            return FollowerDecision(
                follower_name=self.name,
                follower_version=self.version,
                decision="skip",
                reason=f"internal_error: {exc}",
                decided_at=datetime.now(tz=ctx.match_time.tzinfo) if ctx.match_time else datetime.now(),
            )

    @abstractmethod
    def _on_alert(self, ctx: AlertContext) -> FollowerDecision:
        """子類別實作."""
        raise NotImplementedError
