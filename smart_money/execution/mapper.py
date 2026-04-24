"""HL → OKX symbol mapping + size/notional helpers.

Shared between P4c shadow simulator (records paper trades in OKX symbol
space for continuity with P5 live trades) and P5 live execution (same
table, same symbol semantics).

This module does NOT touch OKX API. P5 adds a `validate_against_okx_markets()`
that calls `fetch_markets()` at daemon startup to verify every mapped
symbol exists on the live exchange.

Loading:
    default path: config/smart_money/symbol_map.yaml
    override:     SM_SYMBOL_MAP_PATH env var (future)

Behaviour:
    - Unknown HL symbol → returns None (caller records skipped_signal
      with reason='symbol_unsupported').
    - Size below `min_notional_usd` → SizeCheck.below_min (caller records
      skipped_signal with reason='below_min_size').
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


DEFAULT_MAP_PATH = Path("config/smart_money/symbol_map.yaml")


@dataclass(slots=True, frozen=True)
class SymbolMapEntry:
    hl: str                      # Hyperliquid native, e.g. "BTC"
    okx: str                     # ccxt format, e.g. "BTC/USDT:USDT"
    min_notional_usd: float      # order notional must >= this


@dataclass(slots=True, frozen=True)
class SizeCheck:
    """Result of translating a HL size into OKX-side info + validity flag."""

    ok: bool
    okx_symbol: str | None
    size_coin: float
    notional_usd: float
    reason: Literal["ok", "unknown_symbol", "below_min_size"]
    entry: SymbolMapEntry | None


class SymbolMapper:
    """Loaded symbol map + size-check helpers."""

    def __init__(self, entries: dict[str, SymbolMapEntry]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, path: Path | None = None) -> "SymbolMapper":
        """Load from yaml. Missing file → empty mapper (everything unknown)."""
        path = path or DEFAULT_MAP_PATH
        if not path.exists():
            logger.warning("symbol_map not found at %s — all symbols will be unknown", path)
            return cls({})

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.error("pyyaml not installed — symbol map cannot load")
            return cls({})

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        entries: dict[str, SymbolMapEntry] = {}
        for hl_sym, cfg in raw.items():
            if not isinstance(cfg, dict):
                logger.warning("symbol_map: skip %s (not a dict)", hl_sym)
                continue
            okx = cfg.get("okx")
            if not okx:
                logger.warning("symbol_map: skip %s (missing okx)", hl_sym)
                continue
            entries[hl_sym] = SymbolMapEntry(
                hl=hl_sym,
                okx=okx,
                min_notional_usd=float(cfg.get("min_notional_usd", 10.0)),
            )
        logger.info("symbol_map: loaded %d entries from %s", len(entries), path)
        return cls(entries)

    def lookup(self, hl_symbol: str) -> SymbolMapEntry | None:
        return self._entries.get(hl_symbol)

    def check(self, hl_symbol: str, size_coin: float, px: float) -> SizeCheck:
        """Translate (HL symbol, coin size, px) into OKX-side params + validity."""
        entry = self.lookup(hl_symbol)
        if entry is None:
            return SizeCheck(
                ok=False, okx_symbol=None, size_coin=size_coin,
                notional_usd=size_coin * px,
                reason="unknown_symbol", entry=None,
            )
        notional = size_coin * px
        if notional < entry.min_notional_usd:
            return SizeCheck(
                ok=False, okx_symbol=entry.okx, size_coin=size_coin,
                notional_usd=notional,
                reason="below_min_size", entry=entry,
            )
        return SizeCheck(
            ok=True, okx_symbol=entry.okx, size_coin=size_coin,
            notional_usd=notional, reason="ok", entry=entry,
        )

    def known_symbols(self) -> list[str]:
        return list(self._entries.keys())


__all__ = ["SymbolMapper", "SymbolMapEntry", "SizeCheck", "DEFAULT_MAP_PATH"]
