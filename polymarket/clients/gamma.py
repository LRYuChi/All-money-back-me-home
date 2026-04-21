"""Polymarket Gamma API client — 市場元資料、事件樹、搜尋.

Gamma 提供比 CLOB /markets 更豐富的元資料（分類、事件關聯、標籤）。
Phase 0 僅需 list_markets 與 get_event 兩個方法。

參考：https://docs.polymarket.com/
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from polymarket.config import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
    GAMMA_REST_URL,
)

logger = logging.getLogger(__name__)


class GammaClient:
    """Gamma REST client."""

    def __init__(
        self,
        base_url: str = GAMMA_REST_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._external_client = client is not None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> "GammaClient":
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
                logger.warning("Gamma GET %s failed (attempt %d): %s", path, attempt + 1, exc)
        raise RuntimeError(f"Gamma GET {path} failed after {DEFAULT_RETRIES} attempts: {last_exc}")

    def list_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume",
        ascending: bool = False,
    ) -> list[dict]:
        """列市場（Gamma 原始 dict，欄位比 CLOB 多）."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        data = self._get("/markets", params=params)
        return data if isinstance(data, list) else data.get("data", [])

    def get_event(self, event_id: str | int) -> dict:
        return self._get(f"/events/{event_id}")

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        return self.list_markets(limit=limit)  # Gamma 的 q= 參數支援隨版本改變；保守起見先不帶
