"""Tests for strategies/cli/cron_sidecar.py — R56."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# =================================================================== #
# R60: freqtrade autostart probe
# =================================================================== #
from strategies.cli.cron_sidecar import _ensure_freqtrade_running


def test_autostart_skips_in_dry_run(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "1")
    with patch("urllib.request.urlopen") as up:
        _ensure_freqtrade_running(dry_run=True)
    up.assert_not_called()


def test_autostart_skips_when_env_disabled(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "0")
    with patch("urllib.request.urlopen") as up:
        _ensure_freqtrade_running(dry_run=False)
    up.assert_not_called()


def test_autostart_no_action_when_already_running(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "1")
    monkeypatch.setenv("FREQTRADE_API_URL", "http://freqtrade:8080")
    monkeypatch.setenv("FT_USER", "u")
    monkeypatch.setenv("FT_PASS", "p")

    fake_resp = MagicMock()
    fake_resp.read.return_value = b'{"state": "running"}'
    fake_resp.__enter__ = lambda s: fake_resp
    fake_resp.__exit__ = lambda *a: None

    with patch("urllib.request.urlopen", return_value=fake_resp) as up:
        _ensure_freqtrade_running(dry_run=False)
    # Exactly one call (show_config), no /start
    assert up.call_count == 1


def test_autostart_posts_start_when_stopped(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "1")
    monkeypatch.setenv("FT_USER", "u")
    monkeypatch.setenv("FT_PASS", "p")

    show_resp = MagicMock()
    show_resp.read.return_value = b'{"state": "stopped"}'
    show_resp.__enter__ = lambda s: show_resp
    show_resp.__exit__ = lambda *a: None

    start_resp = MagicMock()
    start_resp.read.return_value = b'{"status": "starting trader"}'
    start_resp.__enter__ = lambda s: start_resp
    start_resp.__exit__ = lambda *a: None

    with patch("urllib.request.urlopen", side_effect=[show_resp, start_resp]) as up:
        _ensure_freqtrade_running(dry_run=False)
    # Two calls: show_config GET + /start POST
    assert up.call_count == 2
    # Second call must be POST
    second_req = up.call_args_list[1].args[0]
    assert second_req.method == "POST"
    assert "/api/v1/start" in second_req.full_url


def test_autostart_silent_on_show_config_failure(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "1")
    monkeypatch.setenv("FT_USER", "u")
    monkeypatch.setenv("FT_PASS", "p")
    import urllib.error
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ) as up:
        # Must not raise
        _ensure_freqtrade_running(dry_run=False)
    assert up.call_count == 1   # tried show_config, didn't try /start


def test_autostart_handles_start_endpoint_failure(monkeypatch):
    monkeypatch.setenv("SUPERTREND_AUTOSTART_FREQTRADE", "1")
    monkeypatch.setenv("FT_USER", "u")
    monkeypatch.setenv("FT_PASS", "p")

    show_resp = MagicMock()
    show_resp.read.return_value = b'{"state": "stopped"}'
    show_resp.__enter__ = lambda s: show_resp
    show_resp.__exit__ = lambda *a: None

    with patch(
        "urllib.request.urlopen",
        side_effect=[show_resp, RuntimeError("server error")],
    ):
        _ensure_freqtrade_running(dry_run=False)   # must not raise


def test_tick_invokes_autostart(monkeypatch):
    """Every tick must call _ensure_freqtrade_running."""
    state = CronState()
    now = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)   # quiet slot
    with patch(
        "strategies.cli.cron_sidecar._ensure_freqtrade_running",
    ) as ensure:
        tick(now, state, dry_run=False)
    ensure.assert_called_once()


# =================================================================== #
# R69: alert dispatch
# =================================================================== #
from strategies.cli.cron_sidecar import (
    _fetch_operations_alerts,
    check_operations_alerts,
)


def _stub_alerts(alerts: list[str] | None):
    """Patch _fetch_operations_alerts to return a fixed list (or None)."""
    return patch(
        "strategies.cli.cron_sidecar._fetch_operations_alerts",
        return_value=alerts,
    )


def test_alert_no_change_no_broadcast(monkeypatch):
    """Same alerts as last time → no Telegram messages."""
    state = CronState(last_alerts_seen=["A", "B"])
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["A", "B"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 0
    assert state.last_alerts_check_iso != ""


def test_alert_new_one_broadcasts(monkeypatch):
    state = CronState(last_alerts_seen=["A"])
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["A", "B"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 1
    msg = send.call_args.args[0]
    assert "NEW" in msg
    assert "B" in msg
    assert state.last_alerts_seen == ["A", "B"]


def test_alert_resolved_broadcasts(monkeypatch):
    state = CronState(last_alerts_seen=["A", "B"])
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["A"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 1
    msg = send.call_args.args[0]
    assert "RESOLVED" in msg
    assert "B" in msg
    assert state.last_alerts_seen == ["A"]


def test_alert_new_and_resolved_both_broadcast():
    state = CronState(last_alerts_seen=["A", "B"])
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["A", "C"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    # B resolved + C new → 2 messages
    assert send.call_count == 2
    msgs = " ".join(c.args[0] for c in send.call_args_list)
    assert "NEW" in msgs and "C" in msgs
    assert "RESOLVED" in msgs and "B" in msgs


def test_alert_first_run_broadcasts_all_as_new():
    state = CronState()   # last_alerts_seen empty
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["A", "B"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 2


def test_alert_probe_failure_does_not_change_state():
    """If /operations is unreachable, state must remain unchanged."""
    state = CronState(last_alerts_seen=["A"])
    state_before = list(state.last_alerts_seen)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(None):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 0
    assert state.last_alerts_seen == state_before


def test_alert_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SUPERTREND_ALERT_BROADCAST", "0")
    state = CronState(last_alerts_seen=[])
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with _stub_alerts(["X"]):
        with patch(
            "strategies.cli.cron_sidecar._send_telegram",
        ) as send:
            check_operations_alerts(state, dry_run=False, now_utc=now)
    assert send.call_count == 0


def test_tick_invokes_alert_check_on_5min_marks(monkeypatch):
    state = CronState()
    now = datetime(2026, 4, 25, 12, 5, tzinfo=timezone.utc)   # min%5 == 0
    with patch(
        "strategies.cli.cron_sidecar.check_operations_alerts",
        return_value=False,
    ) as ck:
        tick(now, state, dry_run=False)
    ck.assert_called_once()


def test_tick_skips_alert_check_off_minute(monkeypatch):
    state = CronState()
    now = datetime(2026, 4, 25, 12, 7, tzinfo=timezone.utc)   # min%5 != 0
    with patch(
        "strategies.cli.cron_sidecar.check_operations_alerts",
    ) as ck:
        tick(now, state, dry_run=False)
    ck.assert_not_called()


def test_state_tolerates_legacy_file_without_alert_keys(tmp_path):
    """Older state.json files (R60-era) lack the new alert fields.
    Loading must succeed with sensible defaults."""
    legacy = tmp_path / "state.json"
    legacy.write_text(json.dumps({
        "last_daily_date": "2026-04-25",
        "last_regime_value": "trending",
        # No last_alerts_seen / last_alerts_check_iso
    }))
    s = CronState.load(legacy)
    assert s.last_daily_date == "2026-04-25"
    assert s.last_alerts_seen == []
    assert s.last_alerts_check_iso == ""


def test_state_save_load_roundtrip_preserves_alert_state(tmp_path):
    s = CronState(last_alerts_seen=["alert-A", "alert-B"],
                  last_alerts_check_iso="2026-04-25T12:00:00Z")
    f = tmp_path / "state.json"
    s.save(f)
    loaded = CronState.load(f)
    assert loaded.last_alerts_seen == ["alert-A", "alert-B"]
    assert loaded.last_alerts_check_iso == "2026-04-25T12:00:00Z"
