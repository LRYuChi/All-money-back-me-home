"""Tests for apps/api/src/routers/supertrend.py — R55 dashboard endpoints."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Bootstrap apps/api on the path so we can import the router
_API_SRC = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))


def _import_router():
    from src.routers.supertrend import (
        _resolve_journal_dir, router,
        supertrend_health, supertrend_skipped, supertrend_snapshot,
        supertrend_trades,
    )
    return {
        "router": router,
        "_resolve_journal_dir": _resolve_journal_dir,
        "snapshot": supertrend_snapshot,
        "trades": supertrend_trades,
        "skipped": supertrend_skipped,
        "health": supertrend_health,
    }


# =================================================================== #
# Router structure
# =================================================================== #
def test_router_has_5_endpoints():
    mod = _import_router()
    paths = {r.path for r in mod["router"].routes}
    assert paths == {
        "/api/supertrend/snapshot",
        "/api/supertrend/regime",
        "/api/supertrend/trades",
        "/api/supertrend/skipped",
        "/api/supertrend/health",
    }


def test_router_endpoints_all_GET():
    mod = _import_router()
    for r in mod["router"].routes:
        assert "GET" in r.methods


# =================================================================== #
# _resolve_journal_dir
# =================================================================== #
def test_resolve_uses_env_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    mod = _import_router()
    assert mod["_resolve_journal_dir"]() == tmp_path


def test_resolve_falls_back_to_default_paths(monkeypatch):
    monkeypatch.delenv("SUPERTREND_JOURNAL_DIR", raising=False)
    mod = _import_router()
    p = mod["_resolve_journal_dir"]()
    # Should pick one of the candidate paths
    assert p.name == "journal"


# =================================================================== #
# /snapshot endpoint
# =================================================================== #
def test_snapshot_handles_missing_journal(monkeypatch, tmp_path):
    """Empty journal dir → graceful payload (not 500)."""
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "doesnt_exist"))
    mod = _import_router()
    out = mod["snapshot"](days=7)
    assert "error" in out
    assert out.get("n_trades", 0) == 0


def test_snapshot_with_seeded_journal(monkeypatch, tmp_path):
    """Pre-seed the journal then read snapshot."""
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    # Seed 2 winning trades inside 7-day window
    for i in range(2):
        j.write({
            "event_type": "exit",
            "timestamp": (now - timedelta(hours=2 + i)).isoformat(),
            "pair": "BTC/USDT:USDT",
            "side": "long",
            "entry_price": 50_000, "exit_price": 51_000,
            "pnl_pct": 2.0, "pnl_usd": 10.0,
            "duration_hours": 4.0,
            "exit_reason": "trailing_stop",
            "max_profit_pct": 2.5, "trailing_phase_at_exit": 1,
            "n_partials_taken": 0, "state": {},
            "entry_tag": "confirmed",
        })

    mod = _import_router()
    out = mod["snapshot"](days=7)
    assert out["n_trades"] == 2
    assert out["n_wins"] == 2
    assert out["win_rate"] == 1.0


def test_snapshot_query_parameter_validation():
    """days param is bounded — Query validates."""
    mod = _import_router()
    # Function-level: just verify it accepts ints in valid range
    # (FastAPI Query validation happens at HTTP layer; here we test
    # the underlying function works for boundary values)
    out_min = mod["snapshot"](days=1)
    out_max = mod["snapshot"](days=365)
    # Both should return a dict (regardless of whether trades exist)
    assert isinstance(out_min, dict)
    assert isinstance(out_max, dict)


# =================================================================== #
# /trades endpoint
# =================================================================== #
def test_trades_returns_empty_for_missing_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "missing"))
    mod = _import_router()
    out = mod["trades"](limit=50, days=30)
    assert out["trades"] == []


def test_trades_returns_only_exit_events(monkeypatch, tmp_path):
    """Mix of entry + exit events, only exits returned."""
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    j.write({
        "event_type": "entry",
        "timestamp": (now - timedelta(hours=10)).isoformat(),
        "pair": "BTC", "side": "long",
    })
    j.write({
        "event_type": "exit",
        "timestamp": (now - timedelta(hours=5)).isoformat(),
        "pair": "BTC", "side": "long",
        "pnl_pct": 1.0, "pnl_usd": 5.0,
        "duration_hours": 5, "exit_reason": "X",
    })

    mod = _import_router()
    out = mod["trades"](limit=50, days=30)
    assert len(out["trades"]) == 1
    assert out["trades"][0]["event_type"] == "exit"


def test_trades_newest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(3):
        j.write({
            "event_type": "exit",
            "timestamp": (now - timedelta(hours=i + 1)).isoformat(),
            "pair": f"PAIR_{i}",
            "pnl_pct": 1.0, "pnl_usd": 5,
            "duration_hours": 1, "exit_reason": "X",
        })

    mod = _import_router()
    out = mod["trades"](limit=50, days=30)
    # PAIR_0 is most recent (1 hour ago), PAIR_2 oldest (3 hours ago)
    assert out["trades"][0]["pair"] == "PAIR_0"
    assert out["trades"][-1]["pair"] == "PAIR_2"


def test_trades_respects_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(20):
        j.write({
            "event_type": "exit",
            "timestamp": (now - timedelta(hours=i + 1)).isoformat(),
            "pair": "X", "pnl_pct": 0, "pnl_usd": 0,
            "duration_hours": 1, "exit_reason": "X",
        })

    mod = _import_router()
    out = mod["trades"](limit=5, days=30)
    assert len(out["trades"]) == 5
    assert out["n_total_exits_in_window"] == 20


# =================================================================== #
# /health endpoint
# =================================================================== #
def test_health_reports_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "missing"))
    mod = _import_router()
    out = mod["health"]()
    assert out["ok"] is False
    assert out["journal_dir_exists"] is False


def test_health_reports_no_recent_events(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    mod = _import_router()
    out = mod["health"]()
    assert out["ok"] is False
    assert "no events" in out.get("reason", "").lower()


def test_health_ok_with_recent_event(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "X", "side": "long",
    })

    mod = _import_router()
    out = mod["health"]()
    assert out["ok"] is True
    assert out["events_last_7d"] == 1


def test_health_stale_when_event_older_than_24h(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    # Event 30 hours old
    j.write({
        "event_type": "entry",
        "timestamp": (datetime.now(timezone.utc)
                      - timedelta(hours=30)).isoformat(),
        "pair": "X", "side": "long",
    })

    mod = _import_router()
    out = mod["health"]()
    assert out["ok"] is False
    assert "24h" in out.get("reason", "")


# =================================================================== #
# /skipped — R61
# =================================================================== #
def test_skipped_handles_missing_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "missing"))
    mod = _import_router()
    out = mod["skipped"](limit=50, days=7)
    assert out["events"] == []


def test_skipped_returns_empty_when_no_skipped_events(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    # Only an entry — should not appear in skipped
    j.write({
        "event_type": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "BTC/USDT:USDT", "side": "long",
    })

    mod = _import_router()
    out = mod["skipped"](limit=50, days=7)
    assert out["events"] == []
    assert out["n_total_in_window"] == 0
    assert out["by_category"] == {}


def test_skipped_groups_by_category(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()

    # Mix of skip reasons mirroring R47/R48/R57/R58 strategy outputs
    skips = [
        ("BTC/USDT:USDT", "R57 pre-entry filter: FR contra-signal: fr=+0.0010"),
        ("ETH/USDT:USDT", "R57 pre-entry filter: orderbook strongly against long"),
        ("AVAX/USDT:USDT", "R58 correlation concentration: mean ρ=0.92"),
        ("SOL/USDT:USDT", "regime: choppy (ATR=1.2%, ADX=18.0, H=0.45)"),
        ("LINK/USDT:USDT", "direction_concentration: already 2 open long, cap 2"),
    ]
    for pair, reason in skips:
        j.write({
            "event_type": "skipped",
            "timestamp": now,
            "pair": pair, "side": "long",
            "reason": reason, "state": {},
        })
    # Plus a CB event
    j.write({
        "event_type": "circuit_breaker",
        "timestamp": now,
        "pair": "DOGE/USDT:USDT", "side": "short",
        "streak_length": 3, "cooldown_remaining_hours": 12.0,
    })

    mod = _import_router()
    out = mod["skipped"](limit=50, days=7)

    assert out["n_total_in_window"] == 6
    cats = out["by_category"]
    assert cats.get("alpha_filter") == 2
    assert cats.get("correlation") == 1
    assert cats.get("regime") == 1
    assert cats.get("direction_concentration") == 1
    assert cats.get("circuit_breaker") == 1


def test_skipped_respects_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(15):
        j.write({
            "event_type": "skipped",
            "timestamp": (now - timedelta(minutes=i)).isoformat(),
            "pair": f"P_{i}", "side": "long",
            "reason": "regime: dead", "state": {},
        })

    mod = _import_router()
    out = mod["skipped"](limit=5, days=7)
    assert len(out["events"]) == 5
    assert out["n_total_in_window"] == 15


def test_skipped_newest_first(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(3):
        j.write({
            "event_type": "skipped",
            "timestamp": (now - timedelta(hours=i + 1)).isoformat(),
            "pair": f"P_{i}", "side": "long",
            "reason": "regime: dead", "state": {},
        })

    mod = _import_router()
    out = mod["skipped"](limit=10, days=7)
    assert out["events"][0]["pair"] == "P_0"
    assert out["events"][-1]["pair"] == "P_2"


def test_skipped_groups_by_pair_top_10(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 12 distinct pairs each blocked once → only top 10 in by_pair
    for i in range(12):
        j.write({
            "event_type": "skipped",
            "timestamp": now,
            "pair": f"P{i:02d}", "side": "long",
            "reason": "regime: choppy", "state": {},
        })

    mod = _import_router()
    out = mod["skipped"](limit=50, days=7)
    assert len(out["by_pair"]) == 10
    assert out["n_total_in_window"] == 12
