"""Polymarket 核心資料模型 — Pydantic v2.

設計原則：
  - 只建模 Phase 0-2 需要的欄位，避免過早建模
  - 原始 API 回應中會有很多我們不用的欄位，用 model_config extra="ignore" 忽略
  - 所有時間欄位統一成 datetime（UTC）
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Side = Literal["BUY", "SELL"]


def _parse_dt(v: str | int | float | datetime | None) -> datetime | None:
    """強制 tz-aware 的 datetime 解析。所有 naive datetime 都視為 UTC."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Token(BaseModel):
    """Market 中的一個結果（二元市場為 Yes/No，多選項市場可為候選人/州名等）.

    無論 outcome 標籤為何，該 token 個別仍為二元結算（解算時 0 或 1）。
    """

    model_config = ConfigDict(extra="ignore")

    token_id: str
    outcome: str
    price: float | None = None
    winner: bool | None = None

    @property
    def is_binary(self) -> bool:
        return self.outcome in ("Yes", "No")


class Market(BaseModel):
    """Polymarket CLOB market.

    condition_id 是市場的主鍵（同一事件的 YES/NO 共用同一個 condition_id）。
    每個 outcome (YES/NO) 有獨立的 token_id 用於訂單簿查詢。
    """

    model_config = ConfigDict(extra="ignore")

    condition_id: str
    question: str
    market_slug: str = ""
    category: str = ""
    end_date_iso: datetime | None = None
    tokens: list[Token] = Field(default_factory=list)
    active: bool = True
    closed: bool = False
    minimum_order_size: float = 0.0
    minimum_tick_size: float = 0.01
    maker_base_fee: float = 0.0
    taker_base_fee: float = 0.0

    @field_validator("end_date_iso", mode="before")
    @classmethod
    def _parse_end_date(cls, v: str | datetime | None) -> datetime | None:
        return _parse_dt(v)

    def yes_token(self) -> Token | None:
        return next((t for t in self.tokens if t.outcome == "Yes"), None)

    def no_token(self) -> Token | None:
        return next((t for t in self.tokens if t.outcome == "No"), None)

    def is_binary(self) -> bool:
        """True 表示標準 YES/NO 市場；False 表示多選項市場."""
        outcomes = {t.outcome for t in self.tokens}
        return outcomes == {"Yes", "No"}


class Level(BaseModel):
    """Order book 一檔."""

    model_config = ConfigDict(extra="ignore")

    price: Decimal
    size: Decimal

    @field_validator("price", "size", mode="before")
    @classmethod
    def _to_decimal(cls, v: str | float | Decimal) -> Decimal:
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class OrderBook(BaseModel):
    """Polymarket CLOB 訂單簿."""

    model_config = ConfigDict(extra="ignore")

    market: str  # condition_id
    asset_id: str  # token_id
    bids: list[Level] = Field(default_factory=list)
    asks: list[Level] = Field(default_factory=list)
    hash: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def best_bid(self) -> Level | None:
        return max(self.bids, key=lambda lv: lv.price) if self.bids else None

    def best_ask(self) -> Level | None:
        return min(self.asks, key=lambda lv: lv.price) if self.asks else None

    def mid_price(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb.price + ba.price) / Decimal(2)

    def spread(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba.price - bb.price


class Trade(BaseModel):
    """Polymarket 成交紀錄."""

    model_config = ConfigDict(extra="ignore")

    id: str
    market: str  # condition_id
    asset_id: str  # token_id (選填)
    price: Decimal
    size: Decimal
    side: Side
    status: str = ""
    maker_address: str = ""
    taker_address: str = ""
    match_time: datetime

    @field_validator("price", "size", mode="before")
    @classmethod
    def _to_decimal(cls, v: str | float | Decimal) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))

    @field_validator("match_time", mode="before")
    @classmethod
    def _parse_match_time(cls, v: str | int | float | datetime) -> datetime:
        parsed = _parse_dt(v)
        if parsed is None:
            raise ValueError("match_time is required and cannot be parsed")
        return parsed

    def notional_usdc(self) -> Decimal:
        return self.price * self.size


class Position(BaseModel):
    """Polymarket Data API 使用者持倉.

    用於鯨魚分析：已結算倉位的 cashPnl 就是完整 realized PnL。

    API 欄位對照（2026-04 實測）：
      - redeemable=true：市場已解算
      - curPrice=0 or 1：已結算（贏或輸）
      - cashPnl：倉位總 P&L（含結算後 mark-to-market）— 用此作為 PnL 來源
      - realizedPnl：僅計入結算前的賣出部分 — 不用
    """

    model_config = ConfigDict(extra="ignore")

    proxy_wallet: str = Field(default="", alias="proxyWallet")
    asset: str = ""  # token_id
    condition_id: str = Field(default="", alias="conditionId")
    outcome: str = ""
    size: Decimal = Decimal(0)
    avg_price: Decimal = Field(default=Decimal(0), alias="avgPrice")
    initial_value: Decimal = Field(default=Decimal(0), alias="initialValue")
    current_value: Decimal = Field(default=Decimal(0), alias="currentValue")
    cash_pnl: Decimal = Field(default=Decimal(0), alias="cashPnl")
    realized_pnl: Decimal = Field(default=Decimal(0), alias="realizedPnl")
    cur_price: Decimal = Field(default=Decimal(0), alias="curPrice")
    redeemable: bool = False
    end_date: datetime | None = Field(default=None, alias="endDate")

    @field_validator(
        "size",
        "avg_price",
        "initial_value",
        "current_value",
        "cash_pnl",
        "realized_pnl",
        "cur_price",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, v: str | float | Decimal | None) -> Decimal:
        if v is None or v == "":
            return Decimal(0)
        return v if isinstance(v, Decimal) else Decimal(str(v))

    @field_validator("end_date", mode="before")
    @classmethod
    def _parse_end_date(cls, v: str | int | float | datetime | None) -> datetime | None:
        return _parse_dt(v)

    @property
    def is_resolved(self) -> bool:
        """是否已結算（可判斷勝敗）.

        判斷邏輯：
          - redeemable=true 即代表市場已解算可贖回
          - 價格已跑到極端（0 或 1）也代表已結算（即使 redeemable 欄位缺漏）
        """
        if self.redeemable:
            return True
        if self.cur_price == Decimal(0) or self.cur_price == Decimal(1):
            # 只當 initial_value > 0 才視為已結算（避免空倉誤判）
            return self.initial_value > Decimal(0)
        return False

    @property
    def is_winning(self) -> bool | None:
        """已結算且 cashPnl > 0 視為贏。None 表示未結算."""
        if not self.is_resolved:
            return None
        return self.cash_pnl > Decimal(0)
