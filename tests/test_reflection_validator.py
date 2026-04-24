"""Tests for reflection.validator + reflection.types + reflection.price."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reflection.price import InMemoryPriceFetcher, PriceUnavailable
from reflection.types import Correctness, ValidationResult, ValidationStats
from reflection.validator import (
    DEFAULT_CORRECTNESS_THRESHOLD,
    UnvalidatedRow,
    _verdict,
    validate_signals,
)


# ================================================================== #
# _verdict — pure function matrix
# ================================================================== #
@pytest.mark.parametrize("direction,actual_return,expected", [
    # long signals
    ("long", 0.005, Correctness.CORRECT),         # +0.5% > 0.2% → correct
    ("long", -0.005, Correctness.INCORRECT),      # -0.5% < -0.2% → incorrect
    ("long", 0.001, Correctness.INCONCLUSIVE),    # +0.1% in band
    ("long", 0.0, Correctness.INCONCLUSIVE),      # exactly 0
    ("long", 0.002, Correctness.INCONCLUSIVE),    # exactly threshold = inconclusive (strict >)

    # short signals (mirror)
    ("short", -0.005, Correctness.CORRECT),
    ("short", 0.005, Correctness.INCORRECT),
    ("short", -0.001, Correctness.INCONCLUSIVE),

    # neutral signals (correct iff |return| <= threshold)
    ("neutral", 0.001, Correctness.CORRECT),
    ("neutral", -0.001, Correctness.CORRECT),
    ("neutral", 0.002, Correctness.CORRECT),       # exactly threshold = correct (<=)
    ("neutral", 0.005, Correctness.INCORRECT),
    ("neutral", -0.005, Correctness.INCORRECT),
])
def test_verdict_matrix(direction, actual_return, expected):
    assert _verdict(direction, actual_return, DEFAULT_CORRECTNESS_THRESHOLD) == expected


def test_verdict_unknown_direction_treated_as_missing():
    assert _verdict("unknown", 0.01, 0.002) == Correctness.MISSING_PRICE


# ================================================================== #
# Price fetcher
# ================================================================== #
def test_price_fetcher_exact_match():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    fetcher = InMemoryPriceFetcher({("BTC", ts): 50_000.0})
    assert fetcher.get_close_at("BTC", ts) == 50_000.0


def test_price_fetcher_within_drift():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    fetcher = InMemoryPriceFetcher({("BTC", ts): 50_000.0})
    # Look up 30 minutes later, default drift is 1h → match
    later = ts + timedelta(minutes=30)
    assert fetcher.get_close_at("BTC", later) == 50_000.0


def test_price_fetcher_picks_closest():
    fetcher = InMemoryPriceFetcher()
    fetcher.add("BTC", datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), 50_000.0)
    fetcher.add("BTC", datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc), 51_000.0)
    fetcher.add("BTC", datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc), 52_000.0)

    # Query at 12:20 → closest is 12:30 (10min) vs 12:00 (20min)
    px = fetcher.get_close_at("BTC", datetime(2026, 1, 1, 12, 20, tzinfo=timezone.utc))
    assert px == 51_000.0


def test_price_fetcher_outside_drift_raises():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    fetcher = InMemoryPriceFetcher({("BTC", ts): 50_000.0})
    far = ts + timedelta(hours=3)
    with pytest.raises(PriceUnavailable):
        fetcher.get_close_at("BTC", far)


def test_price_fetcher_unknown_symbol_raises():
    fetcher = InMemoryPriceFetcher({("BTC", datetime(2026, 1, 1, tzinfo=timezone.utc)): 50_000})
    with pytest.raises(PriceUnavailable, match="ETH"):
        fetcher.get_close_at("ETH", datetime(2026, 1, 1, tzinfo=timezone.utc))


# ================================================================== #
# Stats helpers
# ================================================================== #
def test_stats_hit_rate_excludes_inconclusive():
    s = ValidationStats(examined=10, correct=4, incorrect=2, inconclusive=4)
    # Among decisive (4+2), correct = 4/6 = 0.667
    assert s.hit_rate == pytest.approx(4 / 6)


def test_stats_hit_rate_zero_when_no_decisive():
    s = ValidationStats(examined=5, inconclusive=5)
    assert s.hit_rate == 0.0


def test_stats_coverage_includes_inconclusive():
    s = ValidationStats(examined=10, correct=3, incorrect=2, inconclusive=2, missing_price=3)
    # validated = 3+2+2 = 7 / 10 = 0.7
    assert s.coverage == 0.7


# ================================================================== #
# End-to-end validate_signals
# ================================================================== #
class FakeReader:
    def __init__(self, rows):
        self.rows = rows

    def read_unvalidated(self, *, max_age_hours, limit):
        return self.rows[:limit]


class FakeUpdater:
    def __init__(self, raise_for_id: int | None = None):
        self.writes = []
        self.raise_for_id = raise_for_id

    def update_verdict(self, signal_id, *, was_correct, actual_return_pct, validated_at):
        if signal_id == self.raise_for_id:
            raise RuntimeError("DB hiccup")
        self.writes.append({
            "id": signal_id,
            "was_correct": was_correct,
            "actual_return_pct": actual_return_pct,
            "validated_at": validated_at,
        })


def make_row(id, symbol="BTC", horizon="1h", direction="long",
             ts=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)):
    return UnvalidatedRow(
        id=id, symbol=symbol, horizon=horizon, direction=direction,
        ts=ts, expires_at=ts + timedelta(hours=1),
    )


def test_e2e_long_correct_writes_true():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(1, ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 50_000.0,
        ("BTC", ts + timedelta(hours=1)): 50_500.0,   # +1% → correct long
    })
    reader, updater = FakeReader(rows), FakeUpdater()
    now = ts + timedelta(hours=24)

    stats = validate_signals(reader, updater, fetcher, now=now)

    assert stats.examined == 1
    assert stats.correct == 1
    assert updater.writes[0]["was_correct"] is True
    assert updater.writes[0]["actual_return_pct"] == pytest.approx(0.01)


def test_e2e_long_incorrect_writes_false():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(2, direction="long", ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 50_000.0,
        ("BTC", ts + timedelta(hours=1)): 49_500.0,   # -1% → wrong long
    })
    updater = FakeUpdater()

    validate_signals(FakeReader(rows), updater, fetcher,
                     now=ts + timedelta(hours=24))
    assert updater.writes[0]["was_correct"] is False


def test_e2e_inconclusive_writes_none():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(3, direction="long", ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 50_000.0,
        ("BTC", ts + timedelta(hours=1)): 50_050.0,   # +0.1% → inconclusive
    })
    updater = FakeUpdater()

    stats = validate_signals(FakeReader(rows), updater, fetcher,
                             now=ts + timedelta(hours=24))
    assert stats.inconclusive == 1
    # inconclusive → was_correct=None (still mark validated)
    assert updater.writes[0]["was_correct"] is None


def test_e2e_missing_price_skips_update():
    """When price unavailable, don't write — leave validated_at NULL so
    the next round will retry."""
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(4, direction="long", ts=ts)]
    fetcher = InMemoryPriceFetcher()  # empty book
    updater = FakeUpdater()

    stats = validate_signals(FakeReader(rows), updater, fetcher,
                             now=ts + timedelta(hours=24))
    assert stats.missing_price == 1
    assert len(updater.writes) == 0


def test_e2e_writer_error_counted_but_does_not_abort():
    """One bad write must not stop processing remaining rows."""
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(i, ts=ts) for i in (10, 20, 30)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 100.0,
        ("BTC", ts + timedelta(hours=1)): 101.0,
    })
    updater = FakeUpdater(raise_for_id=20)

    stats = validate_signals(FakeReader(rows), updater, fetcher,
                             now=ts + timedelta(hours=24))
    assert stats.examined == 3
    assert stats.write_errors == 1
    # Two rows still got written (ids 10 and 30)
    assert {w["id"] for w in updater.writes} == {10, 30}


def test_e2e_forward_window_not_yet_elapsed_marks_missing():
    """Defensive: if reader gave us a row whose horizon hasn't elapsed,
    mark MISSING_PRICE rather than computing a bogus return."""
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(99, horizon="4h", ts=ts)]
    fetcher = InMemoryPriceFetcher({("BTC", ts): 50_000})
    updater = FakeUpdater()
    # now = only 1h after signal, but horizon = 4h
    validate_signals(FakeReader(rows), updater, fetcher, now=ts + timedelta(hours=1))
    assert len(updater.writes) == 0


def test_e2e_short_correct():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(50, direction="short", ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 50_000.0,
        ("BTC", ts + timedelta(hours=1)): 49_000.0,   # -2% → correct short
    })
    updater = FakeUpdater()
    validate_signals(FakeReader(rows), updater, fetcher,
                     now=ts + timedelta(hours=24))
    assert updater.writes[0]["was_correct"] is True


def test_e2e_neutral_correct_when_flat():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(60, direction="neutral", ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 50_000.0,
        ("BTC", ts + timedelta(hours=1)): 50_010.0,   # +0.02% → flat → neutral correct
    })
    updater = FakeUpdater()
    validate_signals(FakeReader(rows), updater, fetcher,
                     now=ts + timedelta(hours=24))
    assert updater.writes[0]["was_correct"] is True


def test_e2e_limit_respected():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(i, ts=ts) for i in range(20)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 100.0,
        ("BTC", ts + timedelta(hours=1)): 101.0,
    })
    updater = FakeUpdater()
    stats = validate_signals(FakeReader(rows), updater, fetcher,
                             now=ts + timedelta(hours=24), limit=5)
    assert stats.examined == 5


def test_e2e_zero_entry_marks_missing_price():
    """Defensive: division-by-zero protection."""
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    rows = [make_row(70, ts=ts)]
    fetcher = InMemoryPriceFetcher({
        ("BTC", ts): 0.0,
        ("BTC", ts + timedelta(hours=1)): 100.0,
    })
    updater = FakeUpdater()
    stats = validate_signals(FakeReader(rows), updater, fetcher,
                             now=ts + timedelta(hours=24))
    assert stats.missing_price == 1
