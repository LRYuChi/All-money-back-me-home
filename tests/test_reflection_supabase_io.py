"""Tests for reflection.supabase_io — backend implementations + factory."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from reflection.supabase_io import (
    SupabaseSignalHistoryReader,
    SupabaseSignalHistoryUpdater,
    _parse_iso,
    _supabase_row_to_unvalidated,
    build_reader_updater,
)
from reflection.validator import UnvalidatedRow


# ================================================================== #
# Helpers
# ================================================================== #
def test_parse_iso_handles_z_suffix():
    dt = _parse_iso("2026-01-01T12:00:00Z")
    assert dt == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_handles_explicit_offset():
    dt = _parse_iso("2026-01-01T12:00:00+00:00")
    assert dt == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_supabase_row_conversion():
    row = {
        "id": "42",
        "symbol": "crypto:hyperliquid:BTC",
        "horizon": "1h",
        "direction": "long",
        "ts": "2026-01-01T12:00:00Z",
        "expires_at": "2026-01-01T13:00:00Z",
    }
    u = _supabase_row_to_unvalidated(row)
    assert u.id == 42
    assert u.symbol == "crypto:hyperliquid:BTC"
    assert u.horizon == "1h"
    assert u.direction == "long"
    assert u.ts == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# ================================================================== #
# Mock Supabase client
# ================================================================== #
class FakeQuery:
    """Records all chained method calls for assertion + returns canned data."""

    def __init__(self, table_name: str, data: list[dict] | None = None):
        self.table_name = table_name
        self.data = data or []
        self.calls: list[tuple[str, tuple, dict]] = []
        self.last_filter_eq: dict[str, Any] = {}
        self.last_update_payload: dict[str, Any] | None = None
        self.last_insert_payload: dict[str, Any] | None = None

    # Chainable methods all return self
    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def is_(self, *args, **kwargs):
        self.calls.append(("is_", args, kwargs))
        return self

    def gte(self, *args, **kwargs):
        self.calls.append(("gte", args, kwargs))
        return self

    def lte(self, *args, **kwargs):
        self.calls.append(("lte", args, kwargs))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", (key, value), {}))
        self.last_filter_eq[key] = value
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def limit(self, *args, **kwargs):
        self.calls.append(("limit", args, kwargs))
        return self

    def update(self, payload):
        self.calls.append(("update", (payload,), {}))
        self.last_update_payload = payload
        return self

    def insert(self, payload):
        self.calls.append(("insert", (payload,), {}))
        self.last_insert_payload = payload
        return self

    def execute(self):
        return type("Resp", (), {"data": self.data})()


class FakeClient:
    def __init__(self, data_by_table: dict[str, list[dict]] | None = None):
        self._data = data_by_table or {}
        self.queries: list[FakeQuery] = []

    def table(self, name):
        q = FakeQuery(name, self._data.get(name, []))
        self.queries.append(q)
        return q


# ================================================================== #
# SupabaseSignalHistoryReader
# ================================================================== #
def test_reader_filters_unvalidated_within_window():
    rows = [
        {"id": 1, "symbol": "BTC", "horizon": "1h", "direction": "long",
         "ts": "2026-01-01T12:00:00Z", "expires_at": "2026-01-01T13:00:00Z"},
        {"id": 2, "symbol": "ETH", "horizon": "15m", "direction": "short",
         "ts": "2026-01-01T12:30:00Z", "expires_at": "2026-01-01T12:45:00Z"},
    ]
    client = FakeClient({"signal_history": rows})
    reader = SupabaseSignalHistoryReader(client)
    out = list(reader.read_unvalidated(max_age_hours=24, limit=100))

    assert len(out) == 2
    assert out[0].id == 1 and out[1].id == 2
    # Verify the chain included is_("validated_at","null"), gte/lte on ts, order, limit
    chain = [c[0] for c in client.queries[0].calls]
    assert "select" in chain and "is_" in chain
    assert "gte" in chain and "lte" in chain
    assert "order" in chain and "limit" in chain


def test_reader_skips_malformed_rows(caplog):
    import logging
    rows = [
        {"id": 1, "symbol": "BTC", "horizon": "1h", "direction": "long",
         "ts": "2026-01-01T12:00:00Z", "expires_at": "2026-01-01T13:00:00Z"},
        {"id": 2},  # missing fields
    ]
    client = FakeClient({"signal_history": rows})
    reader = SupabaseSignalHistoryReader(client)

    with caplog.at_level(logging.WARNING):
        out = list(reader.read_unvalidated(max_age_hours=24, limit=100))

    assert len(out) == 1
    assert out[0].id == 1


def test_reader_empty_data_returns_empty():
    client = FakeClient({"signal_history": []})
    reader = SupabaseSignalHistoryReader(client)
    out = list(reader.read_unvalidated(max_age_hours=24, limit=100))
    assert out == []


# ================================================================== #
# SupabaseSignalHistoryUpdater
# ================================================================== #
def test_updater_writes_correct_payload():
    client = FakeClient()
    updater = SupabaseSignalHistoryUpdater(client)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)

    updater.update_verdict(
        signal_id=42,
        was_correct=True,
        actual_return_pct=0.015,
        validated_at=now,
    )

    q = client.queries[0]
    assert q.last_update_payload == {
        "was_correct": True,
        "actual_return_pct": 0.015,
        "validated_at": "2026-01-02T10:00:00+00:00",
    }
    assert q.last_filter_eq == {"id": 42}


def test_updater_writes_none_for_inconclusive():
    """INCONCLUSIVE → was_correct=None must be sent as JSON null, not omitted."""
    client = FakeClient()
    updater = SupabaseSignalHistoryUpdater(client)
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    updater.update_verdict(
        signal_id=1, was_correct=None, actual_return_pct=0.001, validated_at=now,
    )
    payload = client.queries[0].last_update_payload
    assert payload["was_correct"] is None
    assert payload["actual_return_pct"] == 0.001


# ================================================================== #
# build_reader_updater factory
# ================================================================== #
def test_factory_postgres_when_dsn_set():
    from reflection.supabase_io import (
        PostgresSignalHistoryReader,
        PostgresSignalHistoryUpdater,
    )

    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""

    r, u = build_reader_updater(S())
    assert isinstance(r, PostgresSignalHistoryReader)
    assert isinstance(u, PostgresSignalHistoryUpdater)


def test_factory_raises_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""

    with pytest.raises(RuntimeError, match="reflection IO"):
        build_reader_updater(S())


def test_factory_postgres_wins_over_supabase():
    """Postgres priority over REST per docs."""
    from reflection.supabase_io import PostgresSignalHistoryReader

    class S:
        database_url = "postgresql://x"
        supabase_url = "https://x.supabase.co"
        supabase_service_key = "key"

    r, _ = build_reader_updater(S())
    assert isinstance(r, PostgresSignalHistoryReader)
