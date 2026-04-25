"""Exchange adapter layer (Phase F.1).

Concrete dispatchers for live trading sit here. Each exchange (OKX,
IBKR, TW broker) provides:

  - A `Client` Protocol for the HTTP/REST interface (so tests can mock
    without hitting the network)
  - A `Dispatcher` implementation matching execution.pending_orders.Dispatcher
  - A `SymbolCatalog` exposing what the exchange actually trades
    (used by G2 SymbolSupportedGuard)

Round 32 ships only the OKX scaffolding. IBKR and TW broker land in
later F.1.x rounds.
"""

from execution.exchanges.idempotency import make_client_order_id
from execution.exchanges.symbol_catalog import (
    InMemorySymbolCatalog,
    NoOpSymbolCatalog,
    SymbolCatalog,
    YamlSymbolCatalog,
    build_symbol_catalog,
)
from execution.exchanges.types import (
    ExchangeError,
    ExchangeRequest,
    ExchangeResponse,
)

__all__ = [
    "ExchangeError",
    "ExchangeRequest",
    "ExchangeResponse",
    "InMemorySymbolCatalog",
    "NoOpSymbolCatalog",
    "SymbolCatalog",
    "YamlSymbolCatalog",
    "build_symbol_catalog",
    "make_client_order_id",
]
