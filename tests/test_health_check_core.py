"""Tests for scripts/health_check_core.py — R74.

Exercises evaluate_supertrend, evaluate_shadow, render_report, and the
combined CLI behavior with synthetic /operations + /signal-health
payloads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Bootstrap scripts/ on path
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import health_check_core as hc


# =================================================================== #
# evaluate_supertrend — happy path
# =================================================================== #
def _healthy_supertrend_payload() -> dict:
    return {
        "bot": {"state": "running", "dry_run": True,
                "strategy": "SupertrendStrategy", "max_open_trades": 3},
        "whitelist": {"n_pairs": 17},
        "pipeline": {
            "journal_ok": True,
            "evaluations": {"n_evaluations": 200,
                            "tier_fired_count": {"confirmed": 1}},
            "recent_trades": 0,
        },
        "performance": {"n_trades": 0, "win_rate": 0.0, "sum_pnl_usd": 0.0},
        "errors": {},
        "alerts": [],
        "status": "ok",
    }


def test_supertrend_healthy_no_problems():
    res = hc.evaluate_supertrend(_healthy_supertrend_payload())
    assert res["problems"] == []
    assert res["namespace"] == "supertrend"
    assert res["status"] == "ok"


# =================================================================== #
# evaluate_supertrend — hard problems
# =================================================================== #
def test_supertrend_bot_stopped_is_hard_problem():
    p = _healthy_supertrend_payload()
    p["bot"]["state"] = "stopped"
    res = hc.evaluate_supertrend(p)
    assert any("bot.state=stopped" in pr for pr in res["problems"])


def test_supertrend_empty_whitelist_is_hard_problem():
    p = _healthy_supertrend_payload()
    p["whitelist"]["n_pairs"] = 0
    res = hc.evaluate_supertrend(p)
    assert any("whitelist empty" in pr for pr in res["problems"])


def test_supertrend_journal_not_ok_is_hard_problem():
    p = _healthy_supertrend_payload()
    p["pipeline"]["journal_ok"] = False
    res = hc.evaluate_supertrend(p)
    assert any("journal not OK" in pr for pr in res["problems"])


def test_supertrend_zero_evaluations_is_hard_problem():
    p = _healthy_supertrend_payload()
    p["pipeline"]["evaluations"]["n_evaluations"] = 0
    res = hc.evaluate_supertrend(p)
    assert any("0 evaluations" in pr for pr in res["problems"])


def test_supertrend_known_alert_not_hard_problem():
    """NO_FIRES_24H is informational only; iteration should proceed."""
    p = _healthy_supertrend_payload()
    p["alerts"] = ["NO_FIRES_24H — vol filter blocking"]
    p["status"] = "degraded"
    res = hc.evaluate_supertrend(p)
    assert res["problems"] == []
    assert "NO_FIRES_24H" in res["alerts"][0]


def test_supertrend_subcomponent_errors_are_hard_problem():
    p = _healthy_supertrend_payload()
    p["errors"] = {"performance": "field missing"}
    res = hc.evaluate_supertrend(p)
    assert any("errors" in pr for pr in res["problems"])


# =================================================================== #
# evaluate_shadow — happy + edge cases
# =================================================================== #
def _healthy_shadow_payload() -> dict:
    return {
        "configured": True,
        "health": "green",
        "health_reason": None,
        "density": {
            "1h": {"paper_open": 1, "paper_closed": 2, "skipped": 3},
            "6h": {"paper_open": 5, "paper_closed": 5, "skipped": 8},
            "24h": {"paper_open": 8, "paper_closed": 10, "skipped": 20},
        },
        "latency_24h": {"n": 30, "p50_ms": 1500, "p95_ms": 8000, "p99_ms": 12000},
        "skipped_by_reason_24h": {"unknown_symbol": 10},
        "positions": {"long": 4, "short": 3, "flat": 5,
                      "distinct_wallets": 12},
        "alerts": [],
        "alert_count": 0,
        "status": "ok",
    }


def test_shadow_healthy_no_problems():
    res = hc.evaluate_shadow(_healthy_shadow_payload())
    assert res["problems"] == []
    assert res["status"] == "ok"


def test_shadow_unconfigured_returns_neutral():
    """When supabase isn't set up (dev / fresh deploy), no problems flagged."""
    res = hc.evaluate_shadow({"configured": False})
    assert res["status"] == "unconfigured"
    assert res["problems"] == []


def test_shadow_red_health_is_hard_problem():
    p = _healthy_shadow_payload()
    p["health"] = "red"
    p["health_reason"] = "no pipeline activity in 24h"
    res = hc.evaluate_shadow(p)
    assert any("health=red" in pr for pr in res["problems"])


def test_shadow_red_pipeline_alert_is_hard_problem():
    p = _healthy_shadow_payload()
    p["alerts"] = ["RED_PIPELINE — WS dead"]
    res = hc.evaluate_shadow(p)
    assert any("RED_PIPELINE" in pr for pr in res["problems"])


def test_shadow_zero_wallets_alert_is_hard_problem():
    p = _healthy_shadow_payload()
    p["alerts"] = ["ZERO_TRADEABLE_WALLETS — empty"]
    res = hc.evaluate_shadow(p)
    assert any("ZERO_TRADEABLE_WALLETS" in pr for pr in res["problems"])


def test_shadow_all_skipped_no_paper_alert_is_hard_problem():
    p = _healthy_shadow_payload()
    p["alerts"] = ["ALL_SKIPPED_NO_PAPER — pipeline broken"]
    res = hc.evaluate_shadow(p)
    assert any("ALL_SKIPPED_NO_PAPER" in pr for pr in res["problems"])


def test_shadow_cold_start_drift_is_known_degraded_not_hard():
    """COLD_START_DRIFT_DOMINANT is forward-only fix territory (R72) —
    don't block iteration just because the 24h window still shows it."""
    p = _healthy_shadow_payload()
    p["alerts"] = ["COLD_START_DRIFT_DOMINANT — 66/66 skips"]
    res = hc.evaluate_shadow(p)
    assert res["problems"] == []
    assert "COLD_START_DRIFT_DOMINANT" in res["alerts"][0]


def test_shadow_latency_alert_is_known_degraded_not_hard():
    p = _healthy_shadow_payload()
    p["alerts"] = ["LATENCY_BUDGET_EXCEEDED — p95 18000ms"]
    res = hc.evaluate_shadow(p)
    assert res["problems"] == []


# =================================================================== #
# has_hard_problems
# =================================================================== #
def test_has_hard_problems_false_when_both_clean():
    sup = hc.evaluate_supertrend(_healthy_supertrend_payload())
    sh = hc.evaluate_shadow(_healthy_shadow_payload())
    assert hc.has_hard_problems(sup, sh) is False


def test_has_hard_problems_true_on_supertrend_only():
    bad = _healthy_supertrend_payload()
    bad["bot"]["state"] = "stopped"
    sup = hc.evaluate_supertrend(bad)
    sh = hc.evaluate_shadow(_healthy_shadow_payload())
    assert hc.has_hard_problems(sup, sh) is True


def test_has_hard_problems_true_on_shadow_only():
    sup = hc.evaluate_supertrend(_healthy_supertrend_payload())
    bad = _healthy_shadow_payload()
    bad["health"] = "red"
    sh = hc.evaluate_shadow(bad)
    assert hc.has_hard_problems(sup, sh) is True


# =================================================================== #
# render_report
# =================================================================== #
def test_render_report_includes_both_namespaces():
    sup = hc.evaluate_supertrend(_healthy_supertrend_payload())
    sh = hc.evaluate_shadow(_healthy_shadow_payload())
    out = hc.render_report(sup, sh)
    assert "SUPERTREND" in out
    assert "SHADOW" in out
    assert "✅" in out or "⚠️" in out


def test_render_report_lists_hard_problems_section_when_any():
    bad_sup = _healthy_supertrend_payload()
    bad_sup["bot"]["state"] = "stopped"
    sup = hc.evaluate_supertrend(bad_sup)
    sh = hc.evaluate_shadow(_healthy_shadow_payload())
    out = hc.render_report(sup, sh)
    assert "hard problems" in out
    assert "bot.state=stopped" in out


def test_render_report_handles_unconfigured_shadow():
    sup = hc.evaluate_supertrend(_healthy_supertrend_payload())
    sh = hc.evaluate_shadow({"configured": False})
    out = hc.render_report(sup, sh)
    assert "unconfigured" in out


# =================================================================== #
# main() CLI exit codes
# =================================================================== #
def _write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload))


def test_cli_exit_0_when_both_healthy(tmp_path, capsys):
    sup_f = tmp_path / "sup.json"; sh_f = tmp_path / "sh.json"
    _write_json(sup_f, _healthy_supertrend_payload())
    _write_json(sh_f, _healthy_shadow_payload())
    rc = hc.main([str(sup_f), str(sh_f)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "✅" in out


def test_cli_exit_1_when_supertrend_has_hard_problem(tmp_path, capsys):
    bad = _healthy_supertrend_payload()
    bad["bot"]["state"] = "stopped"
    sup_f = tmp_path / "sup.json"; sh_f = tmp_path / "sh.json"
    _write_json(sup_f, bad)
    _write_json(sh_f, _healthy_shadow_payload())
    rc = hc.main([str(sup_f), str(sh_f)])
    assert rc == 1


def test_cli_exit_1_when_shadow_has_hard_problem(tmp_path, capsys):
    bad = _healthy_shadow_payload()
    bad["health"] = "red"
    sup_f = tmp_path / "sup.json"; sh_f = tmp_path / "sh.json"
    _write_json(sup_f, _healthy_supertrend_payload())
    _write_json(sh_f, bad)
    rc = hc.main([str(sup_f), str(sh_f)])
    assert rc == 1


def test_cli_exit_1_on_supertrend_load_failure(tmp_path):
    # Non-existent file
    rc = hc.main([str(tmp_path / "missing.json")])
    assert rc == 1


def test_cli_treats_missing_shadow_arg_as_unconfigured(tmp_path):
    """Backward-compat: old usage with only SUPERTREND arg still works."""
    sup_f = tmp_path / "sup.json"
    _write_json(sup_f, _healthy_supertrend_payload())
    rc = hc.main([str(sup_f)])
    assert rc == 0


def test_cli_treats_unreadable_shadow_as_unconfigured(tmp_path):
    """If shadow JSON file is corrupt, just treat as unconfigured (don't fail)."""
    sup_f = tmp_path / "sup.json"; sh_f = tmp_path / "sh.json"
    _write_json(sup_f, _healthy_supertrend_payload())
    sh_f.write_text("not json")
    rc = hc.main([str(sup_f), str(sh_f)])
    assert rc == 0


def test_cli_quiet_suppresses_output(tmp_path, capsys):
    sup_f = tmp_path / "sup.json"
    _write_json(sup_f, _healthy_supertrend_payload())
    rc = hc.main([str(sup_f), "--quiet"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == ""
