"""Hyperliquid SDK wrapper.

目的:
1. 把 `hyperliquid.info.Info` 的 raw payload 轉成我們的 domain model (Trade)
2. 提供 rate limit / retry / 穩定的型別
3. 讓測試可以 mock(注入 Info-like protocol)

Phase 1 只讀,不下單.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from smart_money.store.schema import Action, Side, Trade

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Info-like Protocol (for testability)
# ------------------------------------------------------------------ #
class InfoLike(Protocol):
    """與 hyperliquid.info.Info 相符的最小介面."""

    def user_fills_by_time(
        self,
        address: str,
        start_time: int,
        end_time: int | None = None,
        aggregate_by_time: bool | None = False,
    ) -> list[dict[str, Any]]: ...

    def user_state(self, address: str, dex: str = "") -> dict[str, Any]: ...

    def all_mids(self, dex: str = "") -> dict[str, str]: ...


# ------------------------------------------------------------------ #
# Exceptions
# ------------------------------------------------------------------ #
class HLClientError(Exception):
    """Base for hl_client errors."""


class HLRateLimitError(HLClientError):
    """Server 回 429 或類似訊號."""


# ------------------------------------------------------------------ #
# Mapping helpers
# ------------------------------------------------------------------ #
def _parse_dir(direction: str) -> tuple[Side, Action]:
    """HL `dir` 欄位 → (side, action).

    HL dir 常見值:
      "Open Long" / "Close Long" / "Open Short" / "Close Short"
      "Buy" / "Sell"(spot)
      "Long > Short" / "Short > Long"(反手,視為先 close 再 open,本 helper 回 open)

    回傳以「持倉方向」為 side,而非 BUY/SELL 方向.
    """
    d = direction.strip()
    if "Long" in d and "Short" in d:
        # 反手:以目的方向為 side, action=open(調用端另行拆成兩筆)
        side: Side = "short" if d.startswith("Long") else "long"
        return side, "open"
    if "Long" in d:
        return "long", ("close" if d.startswith("Close") else "open")
    if "Short" in d:
        return "short", ("close" if d.startswith("Close") else "open")
    # fallback (spot Buy/Sell) — P1 不處理 spot
    raise HLClientError(f"Unrecognised dir: {direction!r}")


def _fill_to_trade(fill: dict[str, Any], wallet_id: UUID) -> Trade | None:
    """單筆 HL fill → Trade.

    回傳 None 代表此 fill 不該入庫(e.g. spot, unknown dir).
    """
    try:
        direction = fill.get("dir", "")
        side, action = _parse_dir(direction)
    except HLClientError:
        logger.debug("skip fill (unparseable dir): %s", direction)
        return None

    try:
        hl_trade_id = str(fill["tid"])
    except KeyError:
        # 有些版本用 hash/oid
        hl_trade_id = fill.get("hash") or str(fill.get("oid", ""))
        if not hl_trade_id:
            logger.warning("fill without tid/hash/oid: %s", fill)
            return None

    ts_ms = int(fill.get("time", 0))
    if ts_ms == 0:
        return None

    closed_pnl = fill.get("closedPnl")
    return Trade(
        wallet_id=wallet_id,
        hl_trade_id=hl_trade_id,
        symbol=str(fill.get("coin", "")),
        side=side,
        action=action,
        size=float(fill.get("sz", 0)),
        price=float(fill.get("px", 0)),
        pnl=(float(closed_pnl) if closed_pnl is not None else None),
        fee=float(fill.get("fee", 0) or 0),
        ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        raw=fill,
    )


# ------------------------------------------------------------------ #
# HLClient
# ------------------------------------------------------------------ #
class HLClient:
    """HL REST wrapper with rate limit + backoff."""

    # HL 官方限制:info endpoints 約 1200 req/min;保守抓 1000 req/min
    DEFAULT_MIN_INTERVAL_SEC = 0.06          # 約 1000 req/min
    FILLS_PAGE_MAX = 2000                     # user_fills_by_time 單次最多

    def __init__(
        self,
        info: InfoLike,
        min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
        max_retries: int = 5,
        sleep_fn: "callable[[float], None]" = time.sleep,
        time_fn: "callable[[], float]" = time.monotonic,
    ):
        self._info = info
        self._min_interval = min_interval_sec
        self._max_retries = max_retries
        self._sleep = sleep_fn
        self._time = time_fn
        self._last_call_ts: float = 0.0

    # --- rate-limited call ------------------------------------------------
    def _throttle(self) -> None:
        now = self._time()
        elapsed = now - self._last_call_ts
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_call_ts = self._time()

    def _retrying(self, fn, *args, **kwargs):
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001  (HL SDK raises generic)
                msg = str(exc).lower()
                if "rate" in msg or "429" in msg:
                    backoff = min(30.0, 0.5 * (2 ** attempt))
                    logger.warning("HL rate limit, backoff %.2fs (attempt %d)", backoff, attempt + 1)
                    self._sleep(backoff)
                    continue
                if attempt == self._max_retries - 1:
                    raise
                backoff = min(10.0, 0.5 * (2 ** attempt))
                logger.warning("HL call failed: %s, retry %.2fs", exc, backoff)
                self._sleep(backoff)
        raise HLClientError(f"exhausted {self._max_retries} retries")

    # --- public API -------------------------------------------------------
    def get_wallet_trades(
        self,
        address: str,
        wallet_id: UUID,
        start_ms: int,
        end_ms: int | None = None,
    ) -> Iterable[Trade]:
        """分頁拉取錢包歷史交易,yield Trade 物件.

        HL 單次最多 2000 筆,分頁策略:
          - 拉一批 (start, end)
          - 若回傳 = 2000 筆,用「最後一筆時間 + 1ms」當新 start,繼續
          - < 2000 筆表示已抓完這個時段
        """
        cursor = start_ms
        end = end_ms or int(self._time() * 1000) + 86_400_000  # 預設給未來一天當上界

        while cursor < end:
            fills = self._retrying(
                self._info.user_fills_by_time,
                address,
                cursor,
                end,
                False,
            )
            if not fills:
                break

            batch_last_ts = 0
            produced = 0
            for fill in fills:
                trade = _fill_to_trade(fill, wallet_id)
                if trade is not None:
                    yield trade
                    produced += 1
                batch_last_ts = max(batch_last_ts, int(fill.get("time", 0)))

            logger.debug(
                "address=%s cursor=%d fetched=%d kept=%d batch_last=%d",
                address, cursor, len(fills), produced, batch_last_ts,
            )

            # 終止條件:回傳少於 page size,或最後一筆時間沒前進
            if len(fills) < self.FILLS_PAGE_MAX or batch_last_ts <= cursor:
                break
            cursor = batch_last_ts + 1

    def get_current_state(self, address: str) -> dict[str, Any]:
        """查當前倉位 (clearinghouseState)."""
        return self._retrying(self._info.user_state, address)

    def get_all_mids(self) -> dict[str, float]:
        """所有 perp 當前 mid 價."""
        raw = self._retrying(self._info.all_mids)
        return {k: float(v) for k, v in raw.items()}


__all__ = [
    "HLClient",
    "HLClientError",
    "HLRateLimitError",
    "InfoLike",
    "_fill_to_trade",    # 測試需要
    "_parse_dir",        # 測試需要
]
