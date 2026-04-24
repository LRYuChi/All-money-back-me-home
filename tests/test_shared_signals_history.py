"""Tests for shared.signals.history — SignalHistoryWriter implementations + helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.signals.history import (
    InMemorySignalHistoryWriter,
    NoOpSignalHistoryWriter,
    record_safe,
)
from shared.signals.types import Direction, SignalSource, UniversalSignal


def make_signal(
    *,
    source: SignalSource = SignalSource.SMART_MONEY,
    direction: Direction = Direction.LONG,
    strength: float = 0.7,
    symbol: str = "crypto:hyperliquid:BTC",
) -> UniversalSignal:
    return UniversalSignal(
        source=source,
        symbol=symbol,
        horizon="15m",
        direction=direction,
        strength=strength,
        reason="test",
        details={"tid": 42, "px": 50_000.0},
    )


# ------------------------------------------------------------------ #
# to_row serialisation
# ------------------------------------------------------------------ #
def test_to_row_contains_all_columns():
    sig = make_signal()
    row = sig.to_row()
    assert row["source"] == "smart_money"
    assert row["symbol"] == "crypto:hyperliquid:BTC"
    assert row["horizon"] == "15m"
    assert row["direction"] == "long"
    assert row["strength"] == 0.7
    assert row["reason"] == "test"
    assert row["details"] == {"tid": 42, "px": 50_000.0}
    assert "ts" in row
    assert "expires_at" in row


def test_to_row_ts_is_iso_utc():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    sig = UniversalSignal(
        source=SignalSource.KRONOS, symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.5, reason="", ts=ts,
    )
    row = sig.to_row()
    assert row["ts"].startswith("2026-01-01T12:00:00")
    # Should be UTC-aware
    assert row["ts"].endswith("+00:00") or row["ts"].endswith("Z")


def test_to_row_details_is_mutable_copy():
    """Caller mutating the returned dict must NOT mutate the signal's details."""
    sig = make_signal()
    row = sig.to_row()
    row["details"]["new_key"] = 999
    assert "new_key" not in sig.details


# ------------------------------------------------------------------ #
# NoOpSignalHistoryWriter
# ------------------------------------------------------------------ #
def test_noop_writer_accepts_without_raising():
    w = NoOpSignalHistoryWriter()
    w.record(make_signal())   # no exception = pass


# ------------------------------------------------------------------ #
# InMemorySignalHistoryWriter
# ------------------------------------------------------------------ #
def test_inmemory_stores_records():
    w = InMemorySignalHistoryWriter()
    w.record(make_signal())
    w.record(make_signal(source=SignalSource.KRONOS))
    assert len(w.records) == 2


def test_inmemory_by_source_filters():
    w = InMemorySignalHistoryWriter()
    w.record(make_signal(source=SignalSource.SMART_MONEY))
    w.record(make_signal(source=SignalSource.KRONOS))
    w.record(make_signal(source=SignalSource.SMART_MONEY))

    sm = w.by_source("smart_money")
    assert len(sm) == 2
    assert all(s.source == SignalSource.SMART_MONEY for s in sm)


# ------------------------------------------------------------------ #
# record_safe — never raises
# ------------------------------------------------------------------ #
class BrokenWriter:
    def record(self, signal):
        raise RuntimeError("boom")


def test_record_safe_swallows_writer_exception():
    broken = BrokenWriter()
    sig = make_signal()
    # Must not raise
    ok = record_safe(broken, sig)
    assert ok is False


def test_record_safe_returns_true_on_success():
    w = InMemorySignalHistoryWriter()
    ok = record_safe(w, make_signal())
    assert ok is True
    assert len(w.records) == 1


def test_record_safe_continues_after_previous_failure():
    """Critical invariant: a failure on signal N must not prevent signal N+1."""
    class FlakyWriter:
        def __init__(self):
            self.calls = 0
            self.records = []
        def record(self, s):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("network hiccup")
            self.records.append(s)

    w = FlakyWriter()
    record_safe(w, make_signal())   # fails
    record_safe(w, make_signal())   # succeeds
    record_safe(w, make_signal())   # succeeds
    assert w.calls == 3
    assert len(w.records) == 2


# ------------------------------------------------------------------ #
# build_writer factory — falls through to NoOp when nothing configured
# ------------------------------------------------------------------ #
def test_build_writer_noop_fallback(monkeypatch):
    from shared.signals import history as h

    class EmptySettings:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""

    w = h.build_writer(EmptySettings())
    assert isinstance(w, NoOpSignalHistoryWriter)


def test_build_writer_prefers_postgres_over_supabase():
    from shared.signals import history as h

    class BothSet:
        database_url = "postgresql://x"
        supabase_url = "https://x.supabase.co"
        supabase_service_key = "key"

    w = h.build_writer(BothSet())
    # Postgres wins the priority order
    assert isinstance(w, h.PostgresSignalHistoryWriter)
