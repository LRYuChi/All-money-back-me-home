"""WinRateProvider — supplies historical win/loss stats for G10 Kelly.

G10 KellyPositionGuard needs:
  - n_trades       — sample size (used to gate insufficient-data → ALLOW)
  - win_rate       — wins / total ∈ [0, 1]
  - avg_win_pct    — average winning trade's return (positive)
  - avg_loss_pct   — average losing trade's |return| (positive)

Kelly fraction:
    f* = (p * b - q) / b
    where p = win_rate, q = 1 - p, b = avg_win_pct / avg_loss_pct

In practice we always use fractional Kelly (e.g. 25%) — full Kelly is too
aggressive and very sensitive to estimation noise.

Backends:
  - NoOpWinRateProvider          — always returns None (G10 fail-opens)
  - InMemoryWinRateProvider      — caller seeds (key, stats) — tests + smoke
  - PostgresWinRateProvider      — queries sm_paper_trades + live_trades
                                   GROUP BY strategy_id (or symbol)
                                   over a lookback window

Stats are keyed by `(strategy_id, symbol, lookback_days)`. The provider
may aggregate at strategy or symbol level depending on caller intent —
G10 typically queries by strategy_id (different strategies have very
different edges; mixing them dilutes the signal).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WinRateStats:
    """Outcome stats over a sample. All fields use positive numbers; the
    direction (win vs loss) is encoded in the field name, not the sign."""

    n_trades: int
    win_rate: float       # in [0,1]
    avg_win_pct: float    # mean winning return, e.g. 0.018 = +1.8%
    avg_loss_pct: float   # mean losing |return|, e.g. 0.012 = -1.2% loss

    def __post_init__(self):
        if self.n_trades < 0:
            raise ValueError(f"n_trades must be ≥ 0, got {self.n_trades}")
        if not (0.0 <= self.win_rate <= 1.0):
            raise ValueError(f"win_rate must be in [0,1], got {self.win_rate}")
        if self.avg_win_pct < 0 or self.avg_loss_pct < 0:
            raise ValueError(
                f"avg_win_pct/avg_loss_pct must be ≥ 0; got "
                f"win={self.avg_win_pct}, loss={self.avg_loss_pct}"
            )

    @property
    def kelly_fraction(self) -> float:
        """Full Kelly fraction. Negative when expected value < 0."""
        if self.avg_loss_pct == 0:
            # All wins, no losses — Kelly says go max. Cap at 1.0 to be sane.
            return 1.0
        b = self.avg_win_pct / self.avg_loss_pct
        if b == 0:
            return -1.0   # avg win is zero → always lose
        p = self.win_rate
        q = 1.0 - p
        return (p * b - q) / b


class WinRateProvider(Protocol):
    def stats(
        self,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        lookback_days: int = 30,
    ) -> WinRateStats | None: ...


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpWinRateProvider:
    """Always returns None — G10 will always fail-open. Use when no
    trade history is available yet."""

    def stats(self, **_) -> WinRateStats | None:
        return None


# ================================================================== #
# InMemory — for tests
# ================================================================== #
class InMemoryWinRateProvider:
    """Caller pre-seeds a flat key → WinRateStats map. Key is a tuple
    (strategy_id, symbol). Both fields can be None for "all"."""

    def __init__(
        self,
        seeded: dict[tuple[str | None, str | None], WinRateStats] | None = None,
    ):
        self._by_key: dict[tuple[str | None, str | None], WinRateStats] = (
            seeded or {}
        )

    def add(
        self,
        stats: WinRateStats,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        self._by_key[(strategy_id, symbol)] = stats

    def stats(
        self,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        lookback_days: int = 30,
    ) -> WinRateStats | None:
        # Most-specific lookup wins; fall back through narrower contexts.
        for key in [
            (strategy_id, symbol),
            (strategy_id, None),
            (None, symbol),
            (None, None),
        ]:
            if key in self._by_key:
                return self._by_key[key]
        return None


# ================================================================== #
# Postgres direct
# ================================================================== #
class PostgresWinRateProvider:
    """Queries sm_paper_trades + live_trades for closed positions in the
    lookback window, computes per-strategy win/loss stats.

    Win/loss is determined by `pnl > 0` vs `pnl < 0` (NULL = open, skipped).
    Returns:
      avg_win_pct  = mean(pnl/notional) over winners
      avg_loss_pct = mean(|pnl|/notional) over losers
    """

    def __init__(self, dsn: str, *, include_live: bool = True):
        self._dsn = dsn
        self._include_live = include_live

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def stats(
        self,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        lookback_days: int = 30,
    ) -> WinRateStats | None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        rows = self._fetch_pnl_rows(strategy_id, symbol, cutoff)
        if not rows:
            return None
        return _compute_stats(rows)

    def _fetch_pnl_rows(
        self,
        strategy_id: str | None,
        symbol: str | None,
        cutoff: datetime,
    ) -> list[tuple[float, float]]:
        """Returns (pnl_usd, notional_usd) tuples across both trade tables."""
        rows: list[tuple[float, float]] = []

        clauses = ["closed_at >= %s", "pnl is not null", "size is not null", "entry_price is not null"]
        params: list[Any] = [cutoff]
        if strategy_id is not None:
            clauses.append("strategy_id = %s")
            params.append(strategy_id)
        if symbol is not None:
            clauses.append("symbol = %s")
            params.append(symbol)
        where = " and ".join(clauses)

        for tbl in self._tables():
            try:
                with self._conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        f"select pnl, abs(size * entry_price) "
                        f"from {tbl} where {where}",
                        tuple(params),
                    )
                    for pnl, notional in cur.fetchall():
                        try:
                            rows.append((float(pnl), float(notional)))
                        except (TypeError, ValueError):
                            continue
            except Exception as e:
                logger.warning(
                    "win_rate: %s query failed (%s) — partial sample",
                    tbl, e,
                )
        return rows

    def _tables(self) -> list[str]:
        return (
            ["sm_paper_trades", "live_trades"]
            if self._include_live
            else ["sm_paper_trades"]
        )


# ================================================================== #
# Helpers
# ================================================================== #
def _compute_stats(
    rows: Iterable[tuple[float, float]],
) -> WinRateStats:
    """Compute WinRateStats from (pnl, notional) tuples. Skips zero-notional
    rows (would divide by zero) and zero-pnl trades (neither win nor loss)."""
    win_returns: list[float] = []
    loss_returns: list[float] = []
    n = 0
    for pnl, notional in rows:
        if notional <= 0:
            continue
        ret = pnl / notional
        if pnl > 0:
            win_returns.append(ret)
            n += 1
        elif pnl < 0:
            loss_returns.append(abs(ret))
            n += 1
    if n == 0:
        return WinRateStats(n_trades=0, win_rate=0.0,
                            avg_win_pct=0.0, avg_loss_pct=0.0)
    win_rate = len(win_returns) / n
    avg_win = sum(win_returns) / len(win_returns) if win_returns else 0.0
    avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0.0
    return WinRateStats(
        n_trades=n,
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
    )


# ================================================================== #
# Factory
# ================================================================== #
def build_win_rate_provider(settings) -> WinRateProvider:  # noqa: ANN001
    """Postgres > NoOp. Supabase REST not yet wired (G10 stats are
    aggregations; REST per-row + manual aggregation would be slow).
    For Supabase deployments use direct DSN if available."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("win_rate_provider: PostgresWinRateProvider")
        return PostgresWinRateProvider(dsn)

    logger.warning(
        "win_rate_provider: NoOp (no DATABASE_URL) — G10 KellyGuard will "
        "always fail-open"
    )
    return NoOpWinRateProvider()


__all__ = [
    "WinRateProvider",
    "WinRateStats",
    "NoOpWinRateProvider",
    "InMemoryWinRateProvider",
    "PostgresWinRateProvider",
    "build_win_rate_provider",
]
