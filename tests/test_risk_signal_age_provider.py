"""Tests for SignalAgeProvider + G1 wiring (round 23)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    GuardContext,
    GuardPipeline,
    GuardResult,
    InMemoryExposureProvider,
    InMemorySignalAgeProvider,
    LatencyBudgetGuard,
    NoOpSignalAgeProvider,
    SupabaseSignalAgeProvider,
    build_signal_age_provider,
    make_context_provider,
)
from risk.signal_age_provider import (
    PostgresSignalAgeProvider,
    _parse_ts,
    _seconds_since,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    notional: float = 500.0,
    fused_signal_id: int | None = 42,
) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1",
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode="shadow",
        fused_signal_id=fused_signal_id,
    )


# ================================================================== #
# _parse_ts helper
# ================================================================== #
def test_parse_ts_handles_aware_datetime():
    n = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert _parse_ts(n) == n


def test_parse_ts_makes_naive_datetime_utc():
    n = datetime(2026, 4, 25, 12, 0)
    out = _parse_ts(n)
    assert out is not None and out.tzinfo == timezone.utc


def test_parse_ts_iso_with_offset():
    out = _parse_ts("2026-04-25T13:45:00+00:00")
    assert out == datetime(2026, 4, 25, 13, 45, tzinfo=timezone.utc)


def test_parse_ts_iso_with_z_suffix():
    """Supabase sometimes returns 'Z' instead of '+00:00'."""
    out = _parse_ts("2026-04-25T13:45:00Z")
    assert out == datetime(2026, 4, 25, 13, 45, tzinfo=timezone.utc)


def test_parse_ts_returns_none_for_invalid_string():
    assert _parse_ts("not-a-date") is None


def test_parse_ts_returns_none_for_none():
    assert _parse_ts(None) is None


# ================================================================== #
# _seconds_since helper
# ================================================================== #
def test_seconds_since_basic():
    ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    n = datetime(2026, 4, 25, 12, 0, 30, tzinfo=timezone.utc)
    assert _seconds_since(ts, n) == 30.0


def test_seconds_since_naive_ts_treated_as_utc():
    ts = datetime(2026, 4, 25, 12, 0)  # naive
    n = datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc)
    assert _seconds_since(ts, n) == 60.0


def test_seconds_since_negative_for_future():
    """Future ts → negative age. Caller's job to interpret."""
    ts = datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc)
    n = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert _seconds_since(ts, n) == -60.0


# ================================================================== #
# NoOp
# ================================================================== #
def test_noop_returns_none_for_any_order():
    p = NoOpSignalAgeProvider()
    assert p.age_seconds(make_order()) is None
    assert p.age_seconds(make_order(fused_signal_id=None)) is None


# ================================================================== #
# InMemory
# ================================================================== #
def test_inmemory_returns_age_for_known_id():
    n = datetime(2026, 4, 25, 12, 0, 30, tzinfo=timezone.utc)
    seeded = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    p = InMemorySignalAgeProvider({42: seeded})
    age = p.age_seconds(make_order(fused_signal_id=42), now=n)
    assert age == 30.0


def test_inmemory_returns_none_for_unknown_id():
    n = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    p = InMemorySignalAgeProvider({42: n})
    assert p.age_seconds(make_order(fused_signal_id=999), now=n) is None


def test_inmemory_returns_none_when_order_has_no_signal_id():
    p = InMemorySignalAgeProvider({42: datetime.now(timezone.utc)})
    assert p.age_seconds(make_order(fused_signal_id=None)) is None


def test_inmemory_add_normalises_naive_ts():
    p = InMemorySignalAgeProvider()
    p.add(7, datetime(2026, 4, 25, 12, 0))   # naive
    n = datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc)
    assert p.age_seconds(make_order(fused_signal_id=7), now=n) == 60.0


# ================================================================== #
# Supabase REST
# ================================================================== #
class _FakeRow:
    def __init__(self, data):
        self.data = data


class _FakeSupabaseClient:
    """Minimal stub matching the chain used in SupabaseSignalAgeProvider."""

    def __init__(self, ts_by_id: dict[int, str]):
        self._ts_by_id = ts_by_id
        self.calls = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _FakeQuery:
    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._where_id: int | None = None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        if col == "id":
            self._where_id = int(val)
        return self

    def limit(self, _n):
        return self

    def execute(self):
        self._client.calls += 1
        ts = self._client._ts_by_id.get(self._where_id)
        if ts is None:
            return _FakeRow([])
        return _FakeRow([{"ts": ts}])


def test_supabase_returns_age_from_fetched_ts():
    client = _FakeSupabaseClient({
        42: "2026-04-25T12:00:00+00:00",
    })
    p = SupabaseSignalAgeProvider(client)
    n = datetime(2026, 4, 25, 12, 0, 45, tzinfo=timezone.utc)
    age = p.age_seconds(make_order(fused_signal_id=42), now=n)
    assert age == 45.0


def test_supabase_caches_resolved_ts():
    """Same id queried twice → only one fetch."""
    client = _FakeSupabaseClient({
        42: "2026-04-25T12:00:00+00:00",
    })
    p = SupabaseSignalAgeProvider(client)
    n = datetime(2026, 4, 25, 12, 0, 30, tzinfo=timezone.utc)
    p.age_seconds(make_order(fused_signal_id=42), now=n)
    p.age_seconds(make_order(fused_signal_id=42), now=n)
    assert client.calls == 1


def test_supabase_caches_negative_lookups():
    """Missing id is also cached → don't re-fetch every order."""
    client = _FakeSupabaseClient({})  # nothing
    p = SupabaseSignalAgeProvider(client)
    p.age_seconds(make_order(fused_signal_id=999))
    p.age_seconds(make_order(fused_signal_id=999))
    assert client.calls == 1


def test_supabase_refresh_busts_cache():
    client = _FakeSupabaseClient({
        42: "2026-04-25T12:00:00+00:00",
    })
    p = SupabaseSignalAgeProvider(client)
    p.age_seconds(make_order(fused_signal_id=42))
    p.refresh()
    p.age_seconds(make_order(fused_signal_id=42))
    assert client.calls == 2


def test_supabase_returns_none_when_id_missing():
    client = _FakeSupabaseClient({})
    p = SupabaseSignalAgeProvider(client)
    assert p.age_seconds(make_order(fused_signal_id=12345)) is None


def test_supabase_skip_lookup_when_order_has_no_signal_id():
    client = _FakeSupabaseClient({})
    p = SupabaseSignalAgeProvider(client)
    assert p.age_seconds(make_order(fused_signal_id=None)) is None
    assert client.calls == 0


def test_supabase_returns_none_on_query_failure():
    class BadClient:
        def table(self, _):
            raise ConnectionError("DB down")
    p = SupabaseSignalAgeProvider(BadClient())
    assert p.age_seconds(make_order(fused_signal_id=42)) is None


# ================================================================== #
# Postgres direct (just construction + miss without DB)
# ================================================================== #
def test_postgres_constructor_does_not_connect():
    """Lazy construction — DSN parsed but no connection until first call."""
    p = PostgresSignalAgeProvider("postgresql://nowhere:5432/x")
    assert p.age_seconds(make_order(fused_signal_id=None)) is None


def test_postgres_returns_none_on_connection_failure():
    """Bad DSN → fail-open None."""
    p = PostgresSignalAgeProvider("postgresql://invalid-host-xyz:1/x")
    # First call attempts to connect; should swallow the exception
    assert p.age_seconds(make_order(fused_signal_id=42)) is None


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""
    p = build_signal_age_provider(S())
    assert isinstance(p, NoOpSignalAgeProvider)


def test_factory_postgres_when_dsn_set():
    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""
    p = build_signal_age_provider(S())
    assert isinstance(p, PostgresSignalAgeProvider)


# ================================================================== #
# G1 integration via make_context_provider
# ================================================================== #
def test_make_context_provider_passes_age_through():
    """G1 sees signal_age_seconds derived from the provider."""
    n_now = datetime.now(timezone.utc)
    age_provider = InMemorySignalAgeProvider({42: n_now - timedelta(seconds=8)})
    exposure = InMemoryExposureProvider()
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
        signal_age_provider=age_provider.age_seconds,
    )
    order = make_order(fused_signal_id=42)
    ctx = ctx_provider(order)
    # roughly 8s; tolerate scheduling jitter
    assert ctx.signal_age_seconds is not None
    assert 7.5 <= ctx.signal_age_seconds <= 9.0


def test_g1_denies_stale_signal_via_lookup():
    """End-to-end: provider serves age, G1 denies because age > budget."""
    n_now = datetime.now(timezone.utc)
    # signal happened 30s ago — past 15s default budget
    age_provider = InMemorySignalAgeProvider({42: n_now - timedelta(seconds=30)})
    exposure = InMemoryExposureProvider()
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
        signal_age_provider=age_provider.age_seconds,
    )
    pipeline = GuardPipeline([LatencyBudgetGuard(budget_seconds=15)])
    order = make_order(fused_signal_id=42)
    run = pipeline.evaluate(order, ctx_provider(order))
    assert not run.accepted
    assert run.decisions[0].guard_name == "latency"


def test_g1_allows_when_no_signal_id_means_no_age():
    """No fused_signal_id → provider returns None → G1 fail-opens."""
    age_provider = InMemorySignalAgeProvider({})
    exposure = InMemoryExposureProvider()
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
        signal_age_provider=age_provider.age_seconds,
    )
    pipeline = GuardPipeline([LatencyBudgetGuard(budget_seconds=15)])
    order = make_order(fused_signal_id=None)
    run = pipeline.evaluate(order, ctx_provider(order))
    assert run.accepted
    assert run.decisions[0].result == GuardResult.ALLOW
    assert "no signal_age_seconds" in run.decisions[0].reason


def test_g1_allows_fresh_signal():
    n_now = datetime.now(timezone.utc)
    age_provider = InMemorySignalAgeProvider({42: n_now - timedelta(seconds=2)})
    exposure = InMemoryExposureProvider()
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
        signal_age_provider=age_provider.age_seconds,
    )
    pipeline = GuardPipeline([LatencyBudgetGuard(budget_seconds=15)])
    order = make_order(fused_signal_id=42)
    run = pipeline.evaluate(order, ctx_provider(order))
    assert run.accepted


def test_make_context_provider_swallows_age_provider_exception():
    """If the age_provider crashes on a single order, ctx_provider still
    returns a valid context (age=None)."""
    def bad_age_provider(_order):
        raise RuntimeError("DB blew up")

    exposure = InMemoryExposureProvider()
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
        signal_age_provider=bad_age_provider,
    )
    ctx = ctx_provider(make_order())
    assert ctx.signal_age_seconds is None
