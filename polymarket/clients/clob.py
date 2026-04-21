"""Polymarket CLOB REST client — 公共端點（不需 auth）.

Phase 0 僅覆蓋讀取用途：markets、book、trades。
Phase 3 才會引入 py-clob-client 做授權下單。

參考：
  - https://docs.polymarket.com/#introduction
  - https://github.com/Polymarket/py-clob-client
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from polymarket.config import (
    CLOB_REST_URL,
    DEFAULT_BACKOFF_BASE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
)
from polymarket.models import Market, OrderBook

logger = logging.getLogger(__name__)


class ClobClient:
    """CLOB REST client (read-only, Phase 0)."""

    def __init__(
        self,
        base_url: str = CLOB_REST_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._external_client = client is not None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> "ClobClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # === Internal ===

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(DEFAULT_RETRIES):
            try:
                r = self._client.get(url, params=params)
                if r.status_code == 429:
                    sleep_for = DEFAULT_BACKOFF_BASE * (2**attempt)
                    logger.warning("CLOB rate limited, sleep %.2fs", sleep_for)
                    time.sleep(sleep_for)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.RequestError) as exc:
                last_exc = exc
                sleep_for = DEFAULT_BACKOFF_BASE * (2**attempt)
                logger.warning("CLOB GET %s failed (attempt %d): %s", path, attempt + 1, exc)
                time.sleep(sleep_for)
        raise RuntimeError(f"CLOB GET {path} failed after {DEFAULT_RETRIES} attempts: {last_exc}")

    # === Markets ===

    def get_markets(self, next_cursor: str = "") -> tuple[list[Market], str]:
        """單頁取得 markets，回傳 (markets, next_cursor)。cursor 為空字串代表起點或結尾。"""
        params: dict[str, Any] = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        data = self._get("/markets", params=params)
        raw = data.get("data", []) if isinstance(data, dict) else data
        next_c = data.get("next_cursor", "") if isinstance(data, dict) else ""
        markets = [Market.model_validate(m) for m in raw]
        return markets, next_c

    def iter_markets(self, max_pages: int = 5) -> list[Market]:
        """迭代多頁 markets，上限 max_pages 防呆（官方一頁 ~500 筆）."""
        out: list[Market] = []
        cursor = ""
        for _ in range(max_pages):
            markets, cursor = self.get_markets(next_cursor=cursor)
            out.extend(markets)
            # Polymarket 使用 "LTE=" 作為結束 sentinel
            if not cursor or cursor == "LTE=":
                break
        return out

    def get_market(self, condition_id: str) -> Market:
        data = self._get(f"/markets/{condition_id}")
        return Market.model_validate(data)

    # === Book ===

    def get_book(self, token_id: str) -> OrderBook:
        data = self._get("/book", params={"token_id": token_id})
        # CLOB /book 回傳的 market 欄位是 condition_id，asset_id 是 token_id
        return OrderBook.model_validate(data)

