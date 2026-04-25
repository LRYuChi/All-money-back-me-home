"""Tests for strategies/cli/cron_sidecar.py — R56."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from strategies.cli.cron_sidecar import CronState, tick


# =================================================================== #
# CronState round-trip
# =================================================================== #
def test_state_load_missing_returns_default(tmp_path: Path):
    s = CronState.load(tmp_path / "missing.json")
    assert s.last_daily_date == ""
    assert s.last_regime_value == ""


def test_state_save_then_load(tmp_path: Path):
    s = CronState(last_daily_date="2026-04-25", last_regime_value="trending")
    f = tmp_path / "state.json"
    s.save(f)
    loaded = CronState.load(f)
    assert loaded.last_daily_date == "2026-04-25"
    assert loaded.last_regime_value == "trending"


def test_state_load_corrupt_falls_back(tmp_path: Path):
    f = tmp_path / "state.json"
    f.write_text("not json {{{")
    s = CronState.load(f)
    assert s.last_daily_date == ""


def test_state_save_atomic(tmp_path: Path, monkeypatch):
    f = tmp_path / "state.json"
    s = CronState(last_daily_date="2026-04-25")
    s.save(f)
    assert not f.with_suffix(".tmp").exists()
    assert f.exists()


# =================================================================== #
# tick — daily summary trigger
# =================================================================== #
def _stub_run(success: bool = True):
    rc = 0 if success else 1
    return patch(
        "strategies.cli.cron_sidecar._run_module",
        return_value=rc,
    )


def test_daily_fires_at_00_05(tmp_path):
    state = CronState()
    now = datetime(2026, 4, 25, 0, 5, tzinfo=timezone.utc)
    with _stub_run() as m:
        changed = tick(now, state, dry_run=False)
    assert changed is True
    assert state.last_daily_date == "2026-04-25"
    # daily_summary should have been called
    calls = [c for c in m.call_args_list if "daily_summary" in c.args[0]]
    assert len(calls) == 1


def test_daily_does_not_fire_twice_same_day():
    state = CronState(last_daily_date="2026-04-25")
    now = datetime(2026, 4, 25, 0, 7, tzinfo=timezone.utc)
    with _stub_run() as m:
        changed = tick(now, state, dry_run=False)
    daily_calls = [c for c in m.call_args_list if "daily_summary" in c.args[0]]
    assert len(daily_calls) == 0


def test_daily_does_not_fire_at_23_59():
    state = CronState()
    now = datetime(2026, 4, 25, 23, 59, tzinfo=timezone.utc)
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    daily_calls = [c for c in m.call_args_list if "daily_summary" in c.args[0]]
    assert len(daily_calls) == 0


def test_daily_failure_keeps_state_unfired():
    state = CronState()
    now = datetime(2026, 4, 25, 0, 5, tzinfo=timezone.utc)
    with _stub_run(success=False):
        tick(now, state, dry_run=False)
    # Daily failed → date NOT marked → next tick will retry
    assert state.last_daily_date == ""


# =================================================================== #
# tick — weekly review trigger
# =================================================================== #
def test_weekly_fires_monday_00_30():
    # 2026-04-27 is Monday
    state = CronState()
    now = datetime(2026, 4, 27, 0, 30, tzinfo=timezone.utc)
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    weekly_calls = [c for c in m.call_args_list if "weekly_review" in c.args[0]]
    assert len(weekly_calls) == 1
    assert state.last_weekly_date == "2026-04-27"


def test_weekly_does_not_fire_on_tuesday():
    # 2026-04-28 is Tuesday
    state = CronState()
    now = datetime(2026, 4, 28, 0, 30, tzinfo=timezone.utc)
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    weekly_calls = [c for c in m.call_args_list if "weekly_review" in c.args[0]]
    assert len(weekly_calls) == 0


def test_weekly_does_not_fire_twice_same_monday():
    state = CronState(last_weekly_date="2026-04-27")
    now = datetime(2026, 4, 27, 0, 35, tzinfo=timezone.utc)
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    weekly_calls = [c for c in m.call_args_list if "weekly_review" in c.args[0]]
    assert len(weekly_calls) == 0


# =================================================================== #
# tick — regime check trigger
# =================================================================== #
def test_regime_fires_at_6h_slot():
    state = CronState()
    now = datetime(2026, 4, 25, 6, 2, tzinfo=timezone.utc)
    with patch(
        "strategies.cli.cron_sidecar._fetch_current_regime",
        return_value=("trending", None),
    ):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            tick(now, state, dry_run=False)
    # First fire — no prev value → no telegram sent
    assert send.call_count == 0
    assert state.last_regime_value == "trending"
    assert state.last_regime_slot == "2026-04-25T06"


def test_regime_does_not_send_when_unchanged():
    state = CronState(last_regime_value="trending",
                      last_regime_slot="2026-04-25T00")
    now = datetime(2026, 4, 25, 6, 2, tzinfo=timezone.utc)
    with patch(
        "strategies.cli.cron_sidecar._fetch_current_regime",
        return_value=("trending", None),
    ):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            tick(now, state, dry_run=False)
    assert send.call_count == 0
    assert state.last_regime_value == "trending"


def test_regime_sends_on_change():
    state = CronState(last_regime_value="trending",
                      last_regime_slot="2026-04-25T00")
    now = datetime(2026, 4, 25, 6, 2, tzinfo=timezone.utc)
    with patch(
        "strategies.cli.cron_sidecar._fetch_current_regime",
        return_value=("choppy", None),
    ):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            tick(now, state, dry_run=False)
    assert send.call_count == 1
    msg = send.call_args.args[0]
    assert "trending" in msg
    assert "choppy" in msg
    assert state.last_regime_value == "choppy"


def test_regime_does_not_fire_outside_6h_slots():
    state = CronState()
    now = datetime(2026, 4, 25, 3, 0, tzinfo=timezone.utc)
    with patch(
        "strategies.cli.cron_sidecar._fetch_current_regime",
    ) as fetch:
        tick(now, state, dry_run=False)
    assert fetch.call_count == 0


def test_regime_skips_on_fetch_error():
    state = CronState()
    now = datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc)
    with patch(
        "strategies.cli.cron_sidecar._fetch_current_regime",
        return_value=("", "ccxt timeout"),
    ):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            tick(now, state, dry_run=False)
    assert send.call_count == 0
    # State NOT updated → next slot will retry
    assert state.last_regime_slot == ""


# =================================================================== #
# tick — multi-job interaction
# =================================================================== #
def test_monday_00_05_fires_daily_only_not_weekly():
    state = CronState()
    now = datetime(2026, 4, 27, 0, 5, tzinfo=timezone.utc)   # Mon 00:05
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    modules = [c.args[0] for c in m.call_args_list]
    assert "strategies.cli.daily_summary" in modules
    assert "strategies.cli.weekly_review" not in modules


def test_monday_00_30_fires_weekly_and_daily_already_done():
    # Daily already done at 00:05; now 00:30 fires weekly
    state = CronState(last_daily_date="2026-04-27")
    now = datetime(2026, 4, 27, 0, 30, tzinfo=timezone.utc)
    with _stub_run() as m:
        tick(now, state, dry_run=False)
    modules = [c.args[0] for c in m.call_args_list]
    assert "strategies.cli.daily_summary" not in modules
    assert "strategies.cli.weekly_review" in modules


def test_dry_run_does_not_invoke_subprocess():
    state = CronState()
    now = datetime(2026, 4, 25, 0, 5, tzinfo=timezone.utc)
    with patch("subprocess.run") as sp:
        tick(now, state, dry_run=True)
    assert sp.call_count == 0
    # State still updates (dry-run still records the fire)
    assert state.last_daily_date == "2026-04-25"
