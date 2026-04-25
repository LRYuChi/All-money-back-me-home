"""ExposureProvider — sums open notional from paper + live trade tables.

GuardPipeline needs `open_notional_by_strategy / by_market / global` to
enforce G4/G5/G6 caps. This module computes those from sm_paper_trades +
live_trades where closed_at IS NULL.

Returns a `GuardContext` per call (caller usually uses this directly as
PendingOrderWorker's context_provider).

Backends mirror PnL aggregator: NoOp / InMemory / Supabase / Postgres
+ factory.

`open_notional` is computed as `size × entry_price` (signed by side,
absolute value taken). For positions with stale entry_price (NULL or 0)
the row is skipped with a warning — better to under-report exposure
than crash the whole guard pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from execution.pending_orders.types import PendingOrder
from risk.guards import GuardContext

logger = logging.getLogger(__name__)


class ExposureProvider(Protocol):
    def open_by_strategy(self) -> dict[str, float]: ...
    def open_by_market(self) -> dict[str, float]: ...
    def open_by_symbol(self) -> dict[str, float]: ...
    def global_open(self) -> float: ...


# ================================================================== #
# Helpers
# ================================================================== #
def _market_from_symbol(symbol: str) -> str:
    """Mirror risk.builtin_guards._market_from_symbol; canonical form
    'crypto:OKX:BTC/USDT:USDT' → 'crypto'."""
    if not symbol or ":" not in symbol:
        return "unknown"
    return symbol.split(":", 1)[0].lower()


def _row_notional(row: dict[str, Any]) -> float | None:
    """Compute |size × entry_price|. Returns None if either is missing/zero."""
    size = row.get("size")
    px = row.get("entry_price")
    if size is None or px is None:
        return None
    try:
        size_f = float(size)
        px_f = float(px)
    except (TypeError, ValueError):
        return None
    if size_f == 0 or px_f == 0:
        return None
    return abs(size_f * px_f)


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpExposureProvider:
    """Always reports zero exposure. Used when no DB is configured —
    G4/G5/G6/G7 will pass everything through unchanged. NOT for production
    risk-on deployments."""

    def open_by_strategy(self) -> dict[str, float]:
        return {}

    def open_by_market(self) -> dict[str, float]:
        return {}

    def open_by_symbol(self) -> dict[str, float]:
        return {}

    def global_open(self) -> float:
        return 0.0


# ================================================================== #
# InMemory
# ================================================================== #
@dataclass(slots=True)
class _OpenPosition:
    """Internal: one row's contribution to exposure."""
    strategy_id: str
    symbol: str
    notional_usd: float


class InMemoryExposureProvider:
    """Caller injects open positions; aggregator returns the buckets."""

    def __init__(self, positions: list[dict[str, Any]] | None = None):
        self._positions: list[_OpenPosition] = []
        for p in positions or []:
            self.add(**p)

    def add(self, *, strategy_id: str, symbol: str, notional_usd: float) -> None:
        self._positions.append(_OpenPosition(strategy_id, symbol, abs(notional_usd)))

    def open_by_strategy(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._positions:
            out[p.strategy_id] = out.get(p.strategy_id, 0.0) + p.notional_usd
        return out

    def open_by_market(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._positions:
            mkt = _market_from_symbol(p.symbol)
            out[mkt] = out.get(mkt, 0.0) + p.notional_usd
        return out

    def open_by_symbol(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._positions:
            out[p.symbol] = out.get(p.symbol, 0.0) + p.notional_usd
        return out

    def global_open(self) -> float:
        return sum(p.notional_usd for p in self._positions)


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabaseExposureProvider:
    """Reads open positions from sm_paper_trades + live_trades.

    A row is "open" iff closed_at IS NULL. Notional = |size × entry_price|.
    Per-table query failures degrade gracefully (logged warning, returns
    partial data) — better to undercount than crash the whole pipeline.

    `strategy_id` lookup: Phase F.1 will introduce a strategy_id column
    on the trade tables. Until then, we fall back to source_wallet_id
    (sm_paper_trades) → strategy_id mapping isn't 1:1, so per-strategy
    aggregation is approximate. G5/G6 (per-market / global) work fine.
    """

    PAPER_TABLE = "sm_paper_trades"
    LIVE_TABLE = "live_trades"

    def __init__(self, client: Any, *, include_live: bool = True):
        self._client = client
        self._include_live = include_live
        # Cache one snapshot per call series; caller can reset.
        self._cache: tuple[dict, dict, dict, float] | None = None

    def refresh(self) -> None:
        """Force re-fetch on next access."""
        self._cache = None

    def open_by_strategy(self) -> dict[str, float]:
        return self._snapshot()[0]

    def open_by_market(self) -> dict[str, float]:
        return self._snapshot()[1]

    def open_by_symbol(self) -> dict[str, float]:
        return self._snapshot()[2]

    def global_open(self) -> float:
        return self._snapshot()[3]

    def _snapshot(self) -> tuple[dict[str, float], dict[str, float], dict[str, float], float]:
        if self._cache is not None:
            return self._cache

        by_strategy: dict[str, float] = {}
        by_market: dict[str, float] = {}
        by_symbol: dict[str, float] = {}
        total = 0.0

        for tbl in self._tables():
            try:
                res = (
                    self._client.table(tbl)
                    .select("strategy_id,source_wallet_id,symbol,size,entry_price")
                    .is_("closed_at", "null")
                    .execute()
                )
                for r in (res.data or []):
                    notional = _row_notional(r)
                    if notional is None:
                        continue
                    sid = r.get("strategy_id") or f"wallet:{r.get('source_wallet_id', 'unknown')}"
                    sym = r.get("symbol", "")
                    by_strategy[sid] = by_strategy.get(sid, 0.0) + notional
                    mkt = _market_from_symbol(sym)
                    by_market[mkt] = by_market.get(mkt, 0.0) + notional
                    if sym:
                        by_symbol[sym] = by_symbol.get(sym, 0.0) + notional
                    total += notional
            except Exception as e:
                logger.warning("exposure: %s query failed (%s) — partial data", tbl, e)

        self._cache = (by_strategy, by_market, by_symbol, total)
        return self._cache

    def _tables(self) -> list[str]:
        return [self.PAPER_TABLE, self.LIVE_TABLE] if self._include_live else [self.PAPER_TABLE]


# ================================================================== #
# Postgres direct
# ================================================================== #
class PostgresExposureProvider:
    def __init__(self, dsn: str, *, include_live: bool = True):
        self._dsn = dsn
        self._include_live = include_live
        self._cache: tuple[dict, dict, dict, float] | None = None

    def refresh(self) -> None:
        self._cache = None

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def open_by_strategy(self) -> dict[str, float]:
        return self._snapshot()[0]

    def open_by_market(self) -> dict[str, float]:
        return self._snapshot()[1]

    def open_by_symbol(self) -> dict[str, float]:
        return self._snapshot()[2]

    def global_open(self) -> float:
        return self._snapshot()[3]

    def _snapshot(self) -> tuple[dict[str, float], dict[str, float], dict[str, float], float]:
        if self._cache is not None:
            return self._cache

        sql_paper = (
            "select coalesce(strategy_id, 'wallet:' || coalesce(source_wallet_id::text, 'unknown')), "
            "       symbol, size, entry_price "
            "from sm_paper_trades where closed_at is null"
        )
        sql_live = (
            "select coalesce(strategy_id, 'wallet:' || coalesce(source_wallet_id::text, 'unknown')), "
            "       symbol, size, entry_price "
            "from live_trades where closed_at is null"
        )

        by_strategy: dict[str, float] = {}
        by_market: dict[str, float] = {}
        by_symbol: dict[str, float] = {}
        total = 0.0

        with self._conn() as conn, conn.cursor() as cur:
            for sql, label in [(sql_paper, "sm_paper_trades"),
                               (sql_live, "live_trades") if self._include_live
                               else (None, None)]:
                if sql is None:
                    continue
                try:
                    cur.execute(sql)
                    for sid, sym, size, px in cur.fetchall():
                        if size is None or px is None:
                            continue
                        try:
                            notional = abs(float(size) * float(px))
                        except (TypeError, ValueError):
                            continue
                        if notional == 0:
                            continue
                        by_strategy[sid] = by_strategy.get(sid, 0.0) + notional
                        mkt = _market_from_symbol(sym or "")
                        by_market[mkt] = by_market.get(mkt, 0.0) + notional
                        if sym:
                            by_symbol[sym] = by_symbol.get(sym, 0.0) + notional
                        total += notional
                except Exception as e:
                    logger.warning("exposure: %s query failed (%s)", label, e)

        self._cache = (by_strategy, by_market, by_symbol, total)
        return self._cache


# ================================================================== #
# Factory
# ================================================================== #
def build_exposure_provider(settings) -> ExposureProvider:  # noqa: ANN001
    """Postgres > Supabase > NoOp."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("exposure_provider: PostgresExposureProvider")
        return PostgresExposureProvider(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("exposure_provider: SupabaseExposureProvider")
            return SupabaseExposureProvider(client)
        except ImportError:
            logger.warning("exposure_provider: supabase-py missing")

    logger.warning(
        "exposure_provider: NoOp (no DB) — G4/G5/G6 will see zero exposure"
    )
    return NoOpExposureProvider()


# ================================================================== #
# Convenience: build a context_provider closure
# ================================================================== #
def make_context_provider(
    capital_usd: float,
    exposure: ExposureProvider,
    *,
    signal_age_provider=None,
):
    """Build the ContextProvider closure that PendingOrderWorker expects.

    `signal_age_provider(order)` → float | None. None means "no age info";
    G1 then fails open. Plug in something that looks up the original
    fused_signal_id → signal_history.ts when you have that wiring (round 22+).

    Refresh policy: each call recomputes exposures via the underlying
    ExposureProvider's cache. SupabaseExposureProvider caches per
    snapshot — call `exposure.refresh()` between batches if you need
    fresh reads each tick.
    """
    def _provide(order: PendingOrder) -> GuardContext:
        # Refresh on each order so consecutive orders in the same tick see
        # cumulative effect of prior orders (a bit conservative — caller
        # may want to disable refresh for performance once exposure is
        # high-cardinality).
        if hasattr(exposure, "refresh"):
            try:
                exposure.refresh()
            except Exception:
                pass

        age = None
        if signal_age_provider is not None:
            try:
                age = signal_age_provider(order)
            except Exception:
                age = None

        return GuardContext(
            capital_usd=capital_usd,
            open_notional_by_strategy=exposure.open_by_strategy(),
            open_notional_by_market=exposure.open_by_market(),
            open_notional_by_symbol=_safe_open_by_symbol(exposure),
            global_open_notional=exposure.global_open(),
            signal_age_seconds=age,
        )

    return _provide


def _safe_open_by_symbol(exposure: ExposureProvider) -> dict[str, float]:
    """Backward-compat shim: third-party ExposureProvider impls written
    before round 29 may not define open_by_symbol; treat as empty dict."""
    fn = getattr(exposure, "open_by_symbol", None)
    if fn is None:
        return {}
    try:
        return fn()
    except Exception:
        return {}


__all__ = [
    "ExposureProvider",
    "NoOpExposureProvider",
    "InMemoryExposureProvider",
    "SupabaseExposureProvider",
    "PostgresExposureProvider",
    "build_exposure_provider",
    "make_context_provider",
]
