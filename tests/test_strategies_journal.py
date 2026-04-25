"""Tests for strategies.journal — JSONL writer + dataclasses (round 46)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategies.journal import (
    CircuitBreakerEvent,
    EntryEvent,
    ExitEvent,
    MultiTfState,
    PartialExitEvent,
    SkippedEvent,
    StoplossPlan,
    TakeProfitPlan,
    TradeJournal,
    TrailingUpdateEvent,
    default_stoploss_plan,
    default_take_profit_plan,
    now_iso,
)


# ================================================================== #
# Helpers
# ================================================================== #
def _state(**kw) -> MultiTfState:
    defaults = dict(
        st_1d=1, st_1d_duration=5, dir_4h_score=0.6,
        st_1h=1, st_15m=1, adx=30.0, atr=120.0,
        trend_quality=0.7, direction_score=0.65, funding_rate=0.0001,
    )
    defaults.update(kw)
    return MultiTfState(**defaults)


def _entry(pair="BTC/USDT:USDT", side="long", tag="confirmed",
           ts=None) -> EntryEvent:
    return EntryEvent(
        timestamp=ts or now_iso(),
        pair=pair, side=side, entry_tag=tag,
        entry_price=50_000.0, amount=0.01, notional_usd=500.0,
        leverage=2.0, stake_usd=250.0,
        state=_state(),
        stoploss_plan=default_stoploss_plan(side),
        take_profit_plan=default_take_profit_plan(),
        kelly_fraction=0.10, kelly_window=60, quality_scale=1.12,
        cb_active=False,
    )


def _exit(pair="BTC/USDT:USDT", side="long", pnl_pct=2.5,
          pnl_usd=12.5, exit_reason="trailing_stop", ts=None,
          max_profit=3.0, phase=2) -> ExitEvent:
    return ExitEvent(
        timestamp=ts or now_iso(),
        pair=pair, side=side,
        entry_price=50_000, exit_price=51_250,
        pnl_pct=pnl_pct, pnl_usd=pnl_usd,
        duration_hours=4.5,
        exit_reason=exit_reason,
        max_profit_pct=max_profit, trailing_phase_at_exit=phase,
        n_partials_taken=0, state=_state(),
    )


# ================================================================== #
# StoplossPlan / TakeProfitPlan defaults
# ================================================================== #
def test_default_stoploss_plan_long_thresholds():
    p = default_stoploss_plan("long")
    assert p.initial_sl_pct == -5.0
    assert p.phase_1_trigger_pct == 1.5
    assert p.phase_2_trigger_pct == 3.0
    assert p.phase_3_trigger_pct == 6.0


def test_default_stoploss_plan_short_tighter():
    """Shorts lock faster (asymmetric design choice)."""
    p_long = default_stoploss_plan("long")
    p_short = default_stoploss_plan("short")
    assert p_short.phase_1_trigger_pct < p_long.phase_1_trigger_pct
    assert p_short.phase_2_trigger_pct < p_long.phase_2_trigger_pct
    assert p_short.phase_3_trigger_pct < p_long.phase_3_trigger_pct


def test_default_take_profit_plan_has_phases():
    tp = default_take_profit_plan()
    assert tp.partial_1_at_profit_pct > 0
    assert tp.partial_2_at_profit_pct > tp.partial_1_at_profit_pct
    assert tp.final_exit_trigger
    assert tp.partial_1_off_pct > 0


# ================================================================== #
# JSONL writer round-trip
# ================================================================== #
def test_writes_entry_event_to_dated_file(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-25T12:00:00+00:00"))
    expected = tmp_path / "2026-04-25.jsonl"
    assert expected.exists()
    rows = expected.read_text().strip().split("\n")
    assert len(rows) == 1
    parsed = json.loads(rows[0])
    assert parsed["event_type"] == "entry"
    assert parsed["pair"] == "BTC/USDT:USDT"


def test_appends_multiple_events_same_day(tmp_path):
    j = TradeJournal(tmp_path)
    ts = "2026-04-25T12:00:00+00:00"
    for _ in range(5):
        j.write(_entry(ts=ts))
    rows = j.read_day("2026-04-25")
    assert len(rows) == 5


def test_partitions_by_utc_date(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-25T23:00:00+00:00"))
    j.write(_entry(ts="2026-04-26T01:00:00+00:00"))
    assert (tmp_path / "2026-04-25.jsonl").exists()
    assert (tmp_path / "2026-04-26.jsonl").exists()


def test_handles_naive_or_invalid_timestamp(tmp_path):
    """If timestamp is missing/unparseable, defaults to today's UTC."""
    j = TradeJournal(tmp_path)
    j.write({"event_type": "test", "pair": "X", "timestamp": "not-a-date"})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert (tmp_path / f"{today}.jsonl").exists()


def test_supports_dict_input(tmp_path):
    """Caller may pass a plain dict (e.g. from external system)."""
    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "custom", "pair": "ABC",
        "timestamp": "2026-04-25T12:00:00+00:00",
        "extra_field": 42,
    })
    rows = j.read_day("2026-04-25")
    assert rows[0]["extra_field"] == 42


def test_rejects_non_dataclass_non_dict(tmp_path):
    j = TradeJournal(tmp_path)
    with pytest.raises(TypeError, match="dataclass or dict"):
        j.write("not an event")


# ================================================================== #
# Read range across days
# ================================================================== #
def test_read_range_inclusive_both_ends(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-23T12:00:00+00:00"))
    j.write(_entry(ts="2026-04-24T12:00:00+00:00"))
    j.write(_entry(ts="2026-04-25T12:00:00+00:00"))
    j.write(_entry(ts="2026-04-26T12:00:00+00:00"))

    rows = j.read_range(
        from_date=datetime(2026, 4, 24, tzinfo=timezone.utc),
        to_date=datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    assert len(rows) == 2


def test_read_range_open_ended_lower(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-20T12:00:00+00:00"))
    j.write(_entry(ts="2026-04-25T12:00:00+00:00"))
    rows = j.read_range(
        to_date=datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    assert len(rows) == 2


def test_read_range_open_ended_upper(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-20T12:00:00+00:00"))
    j.write(_entry(ts="2026-04-25T12:00:00+00:00"))
    rows = j.read_range(
        from_date=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )
    assert len(rows) == 1


def test_read_day_missing_returns_empty(tmp_path):
    j = TradeJournal(tmp_path)
    assert j.read_day("2026-04-01") == []


def test_read_day_skips_malformed_rows(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(ts="2026-04-25T12:00:00+00:00"))
    # Append a corrupt line manually
    with open(tmp_path / "2026-04-25.jsonl", "a") as f:
        f.write("not-json-at-all\n")
    j.write(_entry(ts="2026-04-25T13:00:00+00:00"))

    rows = j.read_day("2026-04-25")
    assert len(rows) == 2   # 2 good rows, 1 corrupt skipped


# ================================================================== #
# All event types persist correctly (full schema round-trip)
# ================================================================== #
def test_partial_exit_event_round_trip(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(PartialExitEvent(
        timestamp="2026-04-25T12:00:00+00:00",
        pair="BTC/USDT:USDT", side="long",
        entry_price=50_000, exit_price=57_500,
        portion_pct=50.0, profit_pct_at_partial=15.0,
        profit_usd_at_partial=75.0,
        trigger="15% target + 1H against",
        state=_state(st_1h=-1),
    ))
    row = j.read_day("2026-04-25")[0]
    assert row["event_type"] == "partial_exit"
    assert row["portion_pct"] == 50.0
    assert row["state"]["st_1h"] == -1


def test_trailing_update_event_round_trip(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(TrailingUpdateEvent(
        timestamp="2026-04-25T12:00:00+00:00",
        pair="X", side="long", phase=2,
        new_sl_pct=-1.5, max_profit_seen_pct=4.0,
        current_profit_pct=3.5,
    ))
    row = j.read_day("2026-04-25")[0]
    assert row["event_type"] == "trailing_update"
    assert row["phase"] == 2


def test_circuit_breaker_event_round_trip(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(CircuitBreakerEvent(
        timestamp="2026-04-25T12:00:00+00:00",
        pair="X", side="long",
        streak_length=3, cooldown_remaining_hours=12.0,
    ))
    row = j.read_day("2026-04-25")[0]
    assert row["event_type"] == "circuit_breaker"
    assert row["streak_length"] == 3


def test_skipped_event_round_trip(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(SkippedEvent(
        timestamp="2026-04-25T12:00:00+00:00",
        pair="X", side="long",
        reason="quality < 0.5",
        state=_state(trend_quality=0.4),
    ))
    row = j.read_day("2026-04-25")[0]
    assert row["event_type"] == "skipped"
    assert "quality" in row["reason"]


# ================================================================== #
# Entry event captures FULL plan (the user's explicit ask)
# ================================================================== #
def test_entry_event_captures_full_sl_plan(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry(side="short"))
    row = j.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))[0]
    sl = row["stoploss_plan"]
    assert sl["initial_sl_pct"] == -5.0
    assert sl["phase_1_trigger_pct"] == 1.0   # short
    assert sl["phase_2_trigger_pct"] == 2.5
    assert sl["phase_3_trigger_pct"] == 5.0


def test_entry_event_captures_full_tp_plan(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry())
    row = j.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))[0]
    tp = row["take_profit_plan"]
    assert tp["partial_1_at_profit_pct"] == 15.0
    assert tp["partial_2_at_profit_pct"] == 30.0
    assert "1H" in tp["partial_1_trigger"]
    assert "1D" in tp["final_exit_trigger"]


def test_entry_event_captures_multi_tf_state(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry())
    row = j.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))[0]
    s = row["state"]
    assert s["st_1d"] == 1
    assert s["dir_4h_score"] == 0.6
    assert s["st_1h"] == 1
    assert s["st_15m"] == 1
    assert s["adx"] == 30.0
    assert s["trend_quality"] == 0.7
    assert s["funding_rate"] == 0.0001


def test_entry_event_captures_kelly_and_quality_scale(tmp_path):
    j = TradeJournal(tmp_path)
    j.write(_entry())
    row = j.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))[0]
    assert row["kelly_fraction"] == 0.10
    assert row["kelly_window"] == 60
    assert row["quality_scale"] == 1.12


# ================================================================== #
# Concurrency
# ================================================================== #
def test_thread_safe_concurrent_writes(tmp_path):
    """Multiple threads writing simultaneously don't corrupt rows."""
    import threading

    j = TradeJournal(tmp_path)
    errors = []
    ts = "2026-04-25T12:00:00+00:00"

    def writer():
        try:
            for _ in range(10):
                j.write(_entry(ts=ts))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    rows = j.read_day("2026-04-25")
    assert len(rows) == 50
    # All rows are well-formed
    assert all(r["event_type"] == "entry" for r in rows)
