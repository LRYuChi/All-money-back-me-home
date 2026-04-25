"""OKX exchange adapter (Phase F.1 round 32).

Public API:

    from execution.exchanges.okx import (
        OKXClient,                  # Protocol — implement to swap real/mock
        OKXLiveDispatcher,          # Dispatcher implementation
        OKXSymbolCatalog,           # for G2 SymbolSupportedGuard
        build_okx_dispatcher,       # factory: settings → dispatcher (or None)
    )

The real ccxt-okx HTTP client is NOT imported here. Round 32 ships the
scaffolding only — real credentials + ccxt wiring come in F.1.x once
sandbox testing is signed off.
"""

from execution.exchanges.okx.client import (
    FakeOKXClient,
    OKXClient,
)
from execution.exchanges.okx.dispatcher import (
    OKXLiveDispatcher,
    build_okx_dispatcher,
)
from execution.exchanges.okx.symbol_catalog import OKXSymbolCatalog

__all__ = [
    "FakeOKXClient",
    "OKXClient",
    "OKXLiveDispatcher",
    "OKXSymbolCatalog",
    "build_okx_dispatcher",
]
