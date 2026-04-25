"""SymbolCatalog — what does a given exchange actually trade?

G2 SymbolSupportedGuard uses this to deny orders for symbols the
exchange can't fill (typo, delisting, wrong exchange tag in the
canonical symbol). Without it G2 would have to be hardcoded per
exchange — bad for multi-exchange support.

Backends:
  - NoOpSymbolCatalog       — every symbol "supported" (G2 fail-open)
  - InMemorySymbolCatalog   — caller seeds; tests + smoke
  - YamlSymbolCatalog       — loads a curated whitelist from YAML
                              (round 35; pre-F.1.x ccxt wiring path)
  - CachedSymbolCatalog     — wraps loader() with TTL refresh
  - <Exchange>SymbolCatalog — per-adapter: queries the exchange's
                              instruments endpoint, caches with TTL

Catalogs work in canonical-symbol space ("crypto:OKX:BTC/USDT:USDT").
Adapter-specific instrument formats stay inside the adapter — the
catalog is the boundary.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
# YAML loader
# ================================================================== #
class YamlSymbolCatalog(InMemorySymbolCatalog):
    """Reads a curated whitelist from YAML and seeds an InMemory catalog.

    Schema:
        symbols:
          - crypto:OKX:BTC/USDT:USDT
          - crypto:OKX:ETH/USDT:USDT
          - crypto:OKX:SOL/USDT:USDT

    Used as the prod path for G2 until ccxt-okx wiring (F.1.x) replaces
    it with a live catalog refresh from the exchange's instruments endpoint.
    Curated YAML is intentional — it's an opt-in tradeable list, narrower
    than "everything OKX lists" (avoids accidentally trading a low-volume
    pair the strategy was never tested on).
    """

    @classmethod
    def from_path(cls, path: Path | str) -> "YamlSymbolCatalog":
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError(
                "YamlSymbolCatalog requires PyYAML — pip install pyyaml"
            ) from e

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"symbol catalog not found: {p}")

        data = yaml.safe_load(p.read_text())
        if not isinstance(data, dict):
            raise ValueError(
                f"symbol catalog YAML must be a mapping; got {type(data).__name__}"
            )
        raw = data.get("symbols") or []
        if not isinstance(raw, list):
            raise ValueError(
                f"symbol catalog 'symbols' must be a list; got {type(raw).__name__}"
            )

        m = cls(symbols=[str(s) for s in raw])
        logger.info(
            "loaded symbol catalog from %s: %d symbols", p, len(m.all_supported()),
        )
        return m


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


# ================================================================== #
# Factory
# ================================================================== #
def build_symbol_catalog(settings) -> "SymbolCatalog":  # noqa: ANN001
    """Yaml > NoOp.

    settings.symbol_catalog_path (env: SM_SYMBOL_CATALOG_PATH) — when set,
    loads YamlSymbolCatalog. Otherwise NoOp (G2 fail-opens, useful in dev/
    shadow without a curated list).

    Phase F.1.x will add an OKXSymbolCatalog branch once ccxt is wired —
    for now the curated YAML is the prod path.
    """
    raw_path = (
        getattr(settings, "symbol_catalog_path", "")
        or ""
    ).strip()
    if not raw_path:
        logger.info(
            "symbol_catalog: NoOp (no SM_SYMBOL_CATALOG_PATH) — "
            "G2 will allow every symbol",
        )
        return NoOpSymbolCatalog()
    try:
        return YamlSymbolCatalog.from_path(raw_path)
    except FileNotFoundError as e:
        logger.warning(
            "symbol_catalog: %s; falling back to NoOp", e,
        )
        return NoOpSymbolCatalog()
    except Exception as e:
        logger.error(
            "symbol_catalog: failed to load %s (%s); falling back to NoOp",
            raw_path, e,
        )
        return NoOpSymbolCatalog()


__all__ = [
    "CachedSymbolCatalog",
    "InMemorySymbolCatalog",
    "NoOpSymbolCatalog",
    "SymbolCatalog",
    "YamlSymbolCatalog",
    "build_symbol_catalog",
]
