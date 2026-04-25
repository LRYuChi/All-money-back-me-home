"""SymbolCatalog — what does a given exchange actually trade?

G2 SymbolSupportedGuard uses this to deny orders for symbols the
exchange can't fill (typo, delisting, wrong exchange tag in the
canonical symbol). Without it G2 would have to be hardcoded per
exchange — bad for multi-exchange support.

Backends:
  - NoOpSymbolCatalog       — every symbol "supported" (G2 fail-open)
  - InMemorySymbolCatalog   — caller seeds; tests + smoke
  - <Exchange>SymbolCatalog — per-adapter: queries the exchange's
                              instruments endpoint, caches with TTL

Catalogs work in canonical-symbol space ("crypto:OKX:BTC/USDT:USDT").
Adapter-specific instrument formats stay inside the adapter — the
catalog is the boundary.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)


class SymbolCatalog(Protocol):
    def supports(self, symbol: str) -> bool: ...
    def all_supported(self) -> set[str]: ...


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpSymbolCatalog:
    """Every symbol supported. G2 fail-opens — useful when no catalog
    is wired yet."""

    def supports(self, symbol: str) -> bool:
        return True

    def all_supported(self) -> set[str]:
        return set()


# ================================================================== #
# InMemory
# ================================================================== #
class InMemorySymbolCatalog:
    """Caller seeds the supported set. Used by tests + as a frozen
    catalog for environments that ship a static instrument list."""

    def __init__(self, symbols: Iterable[str] | None = None):
        self._supported: set[str] = set(symbols or ())

    def add(self, symbol: str) -> None:
        self._supported.add(symbol)

    def add_many(self, symbols: Iterable[str]) -> None:
        self._supported.update(symbols)

    def supports(self, symbol: str) -> bool:
        return symbol in self._supported

    def all_supported(self) -> set[str]:
        return set(self._supported)


# ================================================================== #
# TTL-cached wrapper
# ================================================================== #
class CachedSymbolCatalog:
    """Wraps a `loader()` callable that returns the current supported set.
    Re-fetches when TTL expires; busts on `refresh()`.

    Concrete adapters (OKXSymbolCatalog) instantiate this with their own
    fetch closure — keeps caching policy out of the adapter HTTP code.
    """

    def __init__(
        self,
        loader,                    # Callable[[], set[str]]
        *,
        ttl_seconds: float = 3600.0,
    ):
        self._loader = loader
        self._ttl = ttl_seconds
        self._cached: set[str] | None = None
        self._loaded_at: datetime | None = None

    def refresh(self) -> None:
        self._cached = None
        self._loaded_at = None

    def supports(self, symbol: str) -> bool:
        return symbol in self._snapshot()

    def all_supported(self) -> set[str]:
        return set(self._snapshot())

    def _snapshot(self) -> set[str]:
        now = datetime.now(timezone.utc)
        if (
            self._cached is None
            or self._loaded_at is None
            or (now - self._loaded_at).total_seconds() >= self._ttl
        ):
            try:
                self._cached = set(self._loader())
                self._loaded_at = now
                logger.info(
                    "symbol catalog refreshed: %d symbols", len(self._cached),
                )
            except Exception as e:
                logger.warning(
                    "symbol catalog refresh failed (%s); "
                    "%s symbols remain cached", e,
                    len(self._cached) if self._cached is not None else 0,
                )
                if self._cached is None:
                    # First load failed and no fallback — return empty so
                    # G2 denies everything (fail-CLOSED here, since an
                    # uninitialised catalog with no fallback means we
                    # don't know what's tradeable).
                    return set()
        return self._cached


__all__ = [
    "CachedSymbolCatalog",
    "InMemorySymbolCatalog",
    "NoOpSymbolCatalog",
    "SymbolCatalog",
]
