"""Price fetching for reflection — abstracted so we can mock in tests.

Real backends arrive in Phase C with the data layer:
- `HLPriceFetcher`        — Hyperliquid candles (crypto)
- `OKXPriceFetcher`       — OKX kline (crypto, fallback for HL)
- `YfinancePriceFetcher`  — US/TW stocks
- `BarsTablePriceFetcher` — once `market_bars` table exists

For now we ship Protocol + InMemory + a NotImplemented placeholder so
validator code is fully testable without any data layer present.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class PriceUnavailable(Exception):
    """Raised by fetchers when no price can be retrieved (delisted symbol,
    timestamp out of range, network failure that the fetcher chose to surface
    rather than swallow). Validator catches this and marks MISSING_PRICE."""


class PriceFetcher(Protocol):
    """Get the close price of `symbol` at-or-immediately-after `ts`.

    Contract:
      - `symbol` uses canonical form (e.g. "crypto:hyperliquid:BTC")
      - `ts` is UTC-aware datetime
      - Implementations may snap to the nearest available bar within
        `max_drift_seconds` (default 1h); beyond that they raise
        `PriceUnavailable`.
      - Concurrent calls must be safe (validator may parallelise later).
    """

    def get_close_at(
        self,
        symbol: str,
        ts: datetime,
        *,
        max_drift_seconds: int = 3600,
    ) -> float: ...


class InMemoryPriceFetcher:
    """Test/smoke backend. Caller pre-populates a price book.

    The price book is a dict keyed by (symbol, ts_iso) → price. `get_close_at`
    finds the closest entry within `max_drift_seconds`. Useful for
    deterministic validator tests without a real data feed.
    """

    def __init__(self, prices: dict[tuple[str, datetime], float] | None = None):
        # Internally stored sorted by (symbol, ts) for binary-search lookup
        self._book: dict[str, list[tuple[datetime, float]]] = {}
        for (sym, ts), px in (prices or {}).items():
            self.add(sym, ts, px)

    def add(self, symbol: str, ts: datetime, price: float) -> None:
        bucket = self._book.setdefault(symbol, [])
        bucket.append((ts, price))
        bucket.sort()

    def get_close_at(
        self,
        symbol: str,
        ts: datetime,
        *,
        max_drift_seconds: int = 3600,
    ) -> float:
        bucket = self._book.get(symbol)
        if not bucket:
            raise PriceUnavailable(f"no prices for {symbol}")

        # Find first entry at or after ts; check drift on it AND on the
        # immediately-prior entry (closer is winner).
        best_dt: timedelta | None = None
        best_price: float | None = None
        for entry_ts, px in bucket:
            drift = abs((entry_ts - ts).total_seconds())
            if drift > max_drift_seconds:
                continue
            if best_dt is None or timedelta(seconds=drift) < best_dt:
                best_dt = timedelta(seconds=drift)
                best_price = px

        if best_price is None:
            raise PriceUnavailable(
                f"no price for {symbol} within {max_drift_seconds}s of {ts.isoformat()}"
            )
        return best_price


__all__ = ["PriceFetcher", "InMemoryPriceFetcher", "PriceUnavailable"]
