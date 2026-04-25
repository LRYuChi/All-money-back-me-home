"""OKX-backed symbol catalog. Wraps fetch_instruments() with TTL cache."""
from __future__ import annotations

from execution.exchanges.okx.client import OKXClient
from execution.exchanges.symbol_catalog import CachedSymbolCatalog


class OKXSymbolCatalog(CachedSymbolCatalog):
    """OKX instruments → set of canonical symbols.

    Default 1h TTL — instruments don't change minute-by-minute, but a
    delisting must propagate within an hour so we don't keep trying to
    open positions that'll instantly fail.
    """

    def __init__(
        self,
        client: OKXClient,
        *,
        ttl_seconds: float = 3600.0,
    ):
        super().__init__(loader=client.fetch_instruments, ttl_seconds=ttl_seconds)
        self._client = client


__all__ = ["OKXSymbolCatalog"]
