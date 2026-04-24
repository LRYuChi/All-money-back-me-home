"""Python dataclasses 對應 supabase/migrations/013_smart_money.sql schema.

這些 model 不綁特定 ORM,方便 InMemoryStore 與 SupabaseStore 共用.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4


Side = Literal["long", "short"]
PositionSide = Literal["long", "short", "flat"]   # with 'flat' for closed positions
Action = Literal["open", "close", "increase", "decrease"]


@dataclass(slots=True)
class Wallet:
    address: str
    first_seen_at: datetime
    last_active_at: datetime
    id: UUID = field(default_factory=uuid4)
    tags: list[str] = field(default_factory=list)
    notes: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "address": self.address,
            "first_seen_at": self.first_seen_at.astimezone(timezone.utc).isoformat(),
            "last_active_at": self.last_active_at.astimezone(timezone.utc).isoformat(),
            "tags": self.tags,
            "notes": self.notes,
        }


@dataclass(slots=True)
class Trade:
    wallet_id: UUID
    hl_trade_id: str
    symbol: str              # HL native, e.g. "BTC"
    side: Side
    action: Action
    size: float
    price: float
    pnl: float | None
    fee: float
    ts: datetime
    raw: dict[str, Any] | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "wallet_id": str(self.wallet_id),
            "hl_trade_id": self.hl_trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "action": self.action,
            "size": self.size,
            "price": self.price,
            "pnl": self.pnl,
            "fee": self.fee,
            "ts": self.ts.astimezone(timezone.utc).isoformat(),
            "raw": self.raw,
        }


@dataclass(slots=True)
class Ranking:
    snapshot_date: datetime     # 實際是 date,用 datetime 方便序列化
    wallet_id: UUID
    rank: int
    score: float
    metrics: dict[str, Any]     # 指標細項(Sortino/PF/DD/...)
    ai_analysis: dict[str, Any] | None = None


@dataclass(slots=True)
class PaperTrade:
    source_wallet_id: UUID | None
    symbol: str                 # OKX symbol, e.g. "BTC/USDT:USDT"
    side: Side
    size: float
    entry_price: float
    opened_at: datetime
    exit_price: float | None = None
    pnl: float | None = None
    signal_latency_ms: int | None = None
    closed_at: datetime | None = None


@dataclass(slots=True)
class LiveTrade:
    source_wallet_id: UUID | None
    symbol: str
    side: Side
    size: float
    opened_at: datetime
    okx_order_id: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    pnl: float | None = None
    signal_latency_ms: int | None = None
    guard_decisions: dict[str, Any] | None = None
    closed_at: datetime | None = None


@dataclass(slots=True)
class WalletPosition:
    """Per (wallet, symbol) current position — classifier state-machine persistence.

    `side='flat'` means the wallet has closed out of this symbol. We keep the
    row (rather than deleting) so `last_updated_ts` reflects when we observed
    the close, useful for drift detection and freshness.
    """

    wallet_id: UUID
    symbol: str                              # HL native, e.g. "BTC"
    side: PositionSide                        # 'long' | 'short' | 'flat'
    size: float                              # absolute magnitude; 0 when flat
    avg_entry_px: float | None               # None when flat
    last_updated_ts: datetime

    def to_row(self) -> dict[str, Any]:
        return {
            "wallet_id": str(self.wallet_id),
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "avg_entry_px": self.avg_entry_px,
            "last_updated_ts": self.last_updated_ts.astimezone(timezone.utc).isoformat(),
        }


@dataclass(slots=True)
class SkippedSignal:
    """A fill/signal we chose not to act on — audit + observability record."""

    wallet_id: UUID | None
    wallet_address: str
    symbol_hl: str
    reason: str                              # see docs §6.1
    signal_latency_ms: int | None = None
    direction_raw: str | None = None
    hl_trade_id: int | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_row(self) -> dict[str, Any]:
        return {
            "wallet_id": str(self.wallet_id) if self.wallet_id else None,
            "wallet_address": self.wallet_address,
            "symbol_hl": self.symbol_hl,
            "reason": self.reason,
            "signal_latency_ms": self.signal_latency_ms,
            "direction_raw": self.direction_raw,
            "hl_trade_id": self.hl_trade_id,
            "detail": self.detail,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
        }


__all__ = [
    "Action",
    "LiveTrade",
    "PaperTrade",
    "PositionSide",
    "Ranking",
    "Side",
    "SkippedSignal",
    "Trade",
    "Wallet",
    "WalletPosition",
]
