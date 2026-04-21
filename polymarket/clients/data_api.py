"""Polymarket Data API client — 公開歷史數據.

Data API 提供公開的歷史交易、持倉、PnL 等數據（不需認證）。
相對於 CLOB 的 /trades（需認證、只返回自己的交易），Data API 才是情報工作的主要來源。

Phase 0 僅實作 get_market_trades。
Phase 1 擴充：get_user_trades, get_user_positions, get_user_pnl（鯨魚追蹤）。

參考：https://docs.polymarket.com/#data-api
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from polymarket.config import (
    DATA_API_URL,
    DEFAULT_BACKOFF_BASE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
)
from polymarket.models import Position, Trade

logger = logging.getLogger(__name__)


class DataApiClient:
    """Polymarket Data API REST client."""

    def __init__(
        self,
        base_url: str = DATA_API_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._external_client = client is not None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> "DataApiClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(DEFAULT_RETRIES):
            try:
                r = self._client.get(url, params=params)
                if r.status_code == 429:
                    time.sleep(DEFAULT_BACKOFF_BASE * (2**attempt))
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.RequestError) as exc:
                last_exc = exc
                time.sleep(DEFAULT_BACKOFF_BASE * (2**attempt))
                logger.warning("DataAPI GET %s failed (attempt %d): %s", path, attempt + 1, exc)
        raise RuntimeError(f"DataAPI GET {path} failed after {DEFAULT_RETRIES} attempts: {last_exc}")

    def get_market_trades(
        self,
        *,
        market: str | None = None,
        taker_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Trade]:
        """取得市場的公開成交紀錄。market 可以是 condition_id 或 token_id (依 API 實作)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset, "takerOnly": str(taker_only).lower()}
        if market:
            params["market"] = market
        data = self._get("/trades", params=params)
        raw = data if isinstance(data, list) else data.get("data", [])
        trades: list[Trade] = []
        for t in raw:
            norm = _normalize_trade_fields(t)
            try:
                trades.append(Trade.model_validate(norm))
            except Exception as exc:
                logger.warning("Skipping malformed trade: %s (%s)", exc, t.get("transactionHash", "?"))
        return trades

    def get_user_trades(
        self,
        user: str,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Trade]:
        """取得特定錢包的公開成交歷史（用於鯨魚統計）."""
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        data = self._get("/trades", params=params)
        raw = data if isinstance(data, list) else data.get("data", [])
        trades: list[Trade] = []
        for t in raw:
            norm = _normalize_trade_fields(t)
            try:
                trades.append(Trade.model_validate(norm))
            except Exception as exc:
                logger.warning("Skipping malformed trade for %s: %s", user, exc)
        return trades

    def get_user_positions(
        self,
        user: str,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Position]:
        """取得特定錢包的所有持倉（包含已結算與進行中）."""
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        data = self._get("/positions", params=params)
        raw = data if isinstance(data, list) else data.get("data", [])
        positions: list[Position] = []
        for p in raw:
            try:
                positions.append(Position.model_validate(p))
            except Exception as exc:
                logger.warning("Skipping malformed position for %s: %s", user, exc)
        return positions


def _normalize_trade_fields(t: dict) -> dict:
    """Data API trade 欄位名稱與我們的 Trade 模型對齊."""
    out = dict(t)
    # id: prefer transactionHash + eventIndex for uniqueness; else fallback
    if "id" not in out:
        tx = out.get("transactionHash", "")
        idx = out.get("eventIndex", "")
        out["id"] = f"{tx}:{idx}" if tx else out.get("proxyWallet", "") + ":" + str(out.get("timestamp", ""))
    # market / asset_id
    if "market" not in out:
        out["market"] = out.get("conditionId", "")
    if "asset_id" not in out:
        out["asset_id"] = out.get("asset") or out.get("tokenId") or ""
    # side: Data API uses BUY/SELL already in uppercase typically
    if "side" in out:
        out["side"] = str(out["side"]).upper()
    # match_time: Data API uses timestamp (unix seconds)
    if "match_time" not in out:
        out["match_time"] = out.get("timestamp", 0)
    # addresses
    if "maker_address" not in out:
        out["maker_address"] = out.get("maker", "") or out.get("makerAddress", "")
    if "taker_address" not in out:
        out["taker_address"] = out.get("taker") or out.get("takerAddress") or out.get("proxyWallet", "")
    return out
