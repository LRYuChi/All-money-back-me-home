"""BaseFeature ABC — 所有 scanner features 的合約.

設計強制：
    - 每個 feature 自帶 min_samples 門檻；不達就回傳 confidence='low_samples'
    - compute() 永不引發例外給上層；內部錯誤一律包裝成 FeatureResult.unknown()
    - 同名 feature 升版時 version 必須變動，方便歷史比較
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from polymarket.models import Position, Trade
from polymarket.scanner.profile import FeatureResult

logger = logging.getLogger(__name__)


@dataclass
class ScanContext:
    """單次 scan 的共用上下文，傳給每個 feature.compute()."""

    wallet_address: str
    trades: list[Trade]
    positions: list[Position]
    now: datetime
    pre_reg: dict[str, Any]
    # condition_id → category。可能不完整；feature 應自己處理 None
    market_categories: dict[str, str]


class BaseFeature(ABC):
    """所有 scanner feature 的基底."""

    # 子類別必須覆寫
    name: str = ""
    version: str = "0.0"
    min_samples: int = 0  # 達不到則回傳 confidence='low_samples'

    def compute(self, ctx: ScanContext) -> FeatureResult:
        """頂層入口：包住例外，保證永不向上拋."""
        if not self.name:
            raise NotImplementedError(f"{type(self).__name__} 必須設定 name")
        try:
            return self._compute(ctx)
        except Exception as exc:
            logger.exception("feature %s compute failed for %s", self.name, ctx.wallet_address)
            return FeatureResult.unknown(self.name, self.version, sample_size=0, reason=f"error: {exc}")

    @abstractmethod
    def _compute(self, ctx: ScanContext) -> FeatureResult:
        """子類別實作。可以引發例外，外層 compute() 會處理."""
        raise NotImplementedError
