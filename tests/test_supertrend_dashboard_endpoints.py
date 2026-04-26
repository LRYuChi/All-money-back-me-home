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
        _alignment_count, _build_ops_alerts, _closest_to_fire,
        _extract_last_row, _latest_eval_per_pair, _likely_side,
        _resolve_journal_dir, _verify_entry_in_journal, router,
        supertrend_evaluations, supertrend_force_entry, supertrend_health,
        supertrend_operations, supertrend_scanner, supertrend_skipped,
        supertrend_snapshot, supertrend_trades,
    )
    return {
        "router": router,
        "_resolve_journal_dir": _resolve_journal_dir,
        "snapshot": supertrend_snapshot,
        "trades": supertrend_trades,
        "skipped": supertrend_skipped,
        "scanner": supertrend_scanner,
        "evaluations": supertrend_evaluations,
        "operations": supertrend_operations,
        "force_entry": supertrend_force_entry,
        "health": supertrend_health,
        "alignment_count": _alignment_count,
        "likely_side": _likely_side,
        "extract_last_row": _extract_last_row,
        "build_ops_alerts": _build_ops_alerts,
        "verify_entry_in_journal": _verify_entry_in_journal,
        "closest_to_fire": _closest_to_fire,
        "latest_eval_per_pair": _latest_eval_per_pair,
    }


# =================================================================== #
# Router structure
# =================================================================== #
def test_router_has_10_endpoints():
    """R114 added /entries — total now 10."""
    mod = _import_router()
    paths = {r.path for r in mod["router"].routes}
    assert paths == {
        "/api/supertrend/snapshot",
        "/api/supertrend/regime",
        "/api/supertrend/trades",
        "/api/supertrend/entries",       # R114
        "/api/supertrend/skipped",
        "/api/supertrend/scanner",
        "/api/supertrend/evaluations",
        "/api/supertrend/operations",
        "/api/supertrend/force_entry",
        "/api/supertrend/health",
    }


def test_force_entry_route_is_post():
    mod = _import_router()
    fe_route = next(
        r for r in mod["router"].routes
        if r.path == "/api/supertrend/force_entry"
    )
    assert "POST" in fe_route.methods


def test_router_endpoints_method_invariants():
    """All read-only endpoints are GET; only force_entry (R70 — mutating
    smoke test) is POST. Asserts both invariants explicitly so a future
    accidental verb flip is caught."""
    mod = _import_router()
    for r in mod["router"].routes:
        if r.path == "/api/supertrend/force_entry":
            assert r.methods == {"POST"}
        else:
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


# =================================================================== #
# /scanner — R62 helpers
# =================================================================== #
def test_alignment_count_all_long():
    mod = _import_router()
    state = {
        "st_1d": 1, "dir_4h_score": 0.8, "st_1h": 1, "st_trend": 1,
    }
    assert mod["alignment_count"](state) == 4


def test_alignment_count_all_short():
    mod = _import_router()
    state = {
        "st_1d": -1, "dir_4h_score": -0.8, "st_1h": -1, "st_trend": -1,
    }
    assert mod["alignment_count"](state) == 4


def test_alignment_count_split():
    """Mixed signals → max(longs, shorts) = highest single-side count."""
    mod = _import_router()
    state = {
        "st_1d": 1, "dir_4h_score": 0.5, "st_1h": -1, "st_trend": 1,
    }
    # Longs: st_1d, dir_4h, st_15m = 3; shorts: st_1h = 1
    assert mod["alignment_count"](state) == 3


def test_alignment_count_4h_below_threshold_treated_neutral():
    """4h score must clear ±0.25 to count as a directional vote."""
    mod = _import_router()
    state = {
        "st_1d": 1, "dir_4h_score": 0.1,   # too small → neutral
        "st_1h": 1, "st_trend": 1,
    }
    assert mod["alignment_count"](state) == 3


def test_alignment_count_handles_missing_fields():
    mod = _import_router()
    assert mod["alignment_count"]({}) == 0


def test_likely_side_long():
    mod = _import_router()
    assert mod["likely_side"]({"direction_score": 0.6}) == "long"


def test_likely_side_short():
    mod = _import_router()
    assert mod["likely_side"]({"direction_score": -0.6}) == "short"


def test_likely_side_neutral_below_threshold():
    mod = _import_router()
    assert mod["likely_side"]({"direction_score": 0.1}) is None


def test_extract_last_row_pulls_supertrend_columns():
    mod = _import_router()
    candle_resp = {
        "columns": ["date", "open", "close", "st_1d", "direction_score",
                    "trend_quality", "adx", "funding_rate"],
        "data": [
            ["2026-04-25T00:00", 100, 101, 1, 0.5, 0.8, 32.5, 0.0001],
            ["2026-04-25T00:15", 101, 102, 1, 0.7, 0.9, 35.0, 0.0002],
        ],
    }
    out = mod["extract_last_row"](candle_resp)
    assert out["st_1d"] == 1
    assert out["direction_score"] == 0.7
    assert out["adx"] == 35.0


def test_extract_last_row_handles_empty():
    mod = _import_router()
    assert mod["extract_last_row"]({}) == {}
    assert mod["extract_last_row"]({"data": [], "columns": []}) == {}


# =================================================================== #
# /scanner — endpoint behavior with mocked freqtrade
# =================================================================== #
def _mock_ft_get(whitelist_resp, candle_resps_by_pair, whitelist_error=None):
    """Build a side_effect that proxies freqtrade GETs in tests.
    `candle_resps_by_pair` is {pair: response_dict_or_exception}."""
    def fake_get(path, **kwargs):
        if path == "/api/v1/whitelist":
            if whitelist_error:
                raise whitelist_error
            return whitelist_resp
        if path.startswith("/api/v1/pair_candles"):
            # Extract pair from query string
            import urllib.parse
            qs = urllib.parse.urlparse(path).query
            params = dict(urllib.parse.parse_qsl(qs))
            pair = params.get("pair", "")
            resp = candle_resps_by_pair.get(pair)
            if isinstance(resp, Exception):
                raise resp
            if resp is None:
                raise RuntimeError(f"unexpected pair {pair}")
            return resp
        raise RuntimeError(f"unexpected path {path}")
    return fake_get


def test_scanner_returns_pairs_sorted_by_alignment(monkeypatch):
    from unittest.mock import patch

    wl = {"whitelist": ["AAA/USDT:USDT", "BBB/USDT:USDT"]}
    candles = {
        # AAA: 4/4 aligned long
        "AAA/USDT:USDT": {
            "columns": ["st_1d", "dir_4h_score", "st_1h", "st_trend",
                        "direction_score", "trend_quality", "adx",
                        "atr", "funding_rate"],
            "data": [[1, 0.8, 1, 1, 0.9, 0.85, 38.0, 100.0, 0.0001]],
        },
        # BBB: 2/4 aligned
        "BBB/USDT:USDT": {
            "columns": ["st_1d", "dir_4h_score", "st_1h", "st_trend",
                        "direction_score", "trend_quality", "adx",
                        "atr", "funding_rate"],
            "data": [[1, 0.1, 1, -1, 0.2, 0.4, 22.0, 50.0, 0.0]],
        },
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=10)
    assert out["n_pairs"] == 2
    assert len(out["pairs"]) == 2
    # AAA (alignment 4) ahead of BBB (alignment 2)
    assert out["pairs"][0]["pair"] == "AAA/USDT:USDT"
    assert out["pairs"][0]["alignment_count"] == 4
    assert out["pairs"][0]["likely_side"] == "long"
    assert out["pairs"][1]["pair"] == "BBB/USDT:USDT"
    assert out["pairs"][1]["alignment_count"] == 2


def test_scanner_handles_whitelist_failure(monkeypatch):
    from unittest.mock import patch
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(
            None, {}, whitelist_error=RuntimeError("connection refused"),
        ),
    ):
        out = mod["scanner"](timeframe="15m", limit=10)
    assert out["pairs"] == []
    assert "error" in out
    assert "whitelist" in out["error"]


def test_scanner_records_per_pair_errors(monkeypatch):
    """One pair fails fetch → only that pair appears in errors; rest succeed."""
    from unittest.mock import patch
    wl = {"whitelist": ["GOOD/USDT:USDT", "BAD/USDT:USDT"]}
    candles = {
        "GOOD/USDT:USDT": {
            "columns": ["st_1d", "direction_score"],
            "data": [[1, 0.5]],
        },
        "BAD/USDT:USDT": RuntimeError("timeout"),
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=10)
    assert len(out["pairs"]) == 1
    assert out["pairs"][0]["pair"] == "GOOD/USDT:USDT"
    assert "BAD/USDT:USDT" in out["errors"]


def test_scanner_respects_limit(monkeypatch):
    from unittest.mock import patch
    wl = {"whitelist": [f"P{i}/USDT:USDT" for i in range(10)]}
    candles = {
        p: {"columns": ["st_1d"], "data": [[1]]}
        for p in wl["whitelist"]
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=3)
    # Only first 3 pairs queried + returned
    assert len(out["pairs"]) == 3
    assert out["n_pairs"] == 10   # full whitelist size still reported


def test_scanner_handles_empty_whitelist(monkeypatch):
    from unittest.mock import patch
    wl = {"whitelist": []}
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, {}),
    ):
        out = mod["scanner"](timeframe="15m", limit=10)
    assert out["pairs"] == []
    assert out["n_pairs"] == 0
    assert "error" not in out


# =================================================================== #
# /evaluations — R66
# =================================================================== #
def test_evaluations_handles_missing_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "missing"))
    mod = _import_router()
    out = mod["evaluations"](days=1, pair=None, tier="all")
    assert out["n_evaluations"] == 0


def test_evaluations_returns_empty_when_no_events(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    # Only an entry — should NOT appear in /evaluations
    j.write({
        "event_type": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "BTC/USDT:USDT", "side": "long",
    })
    mod = _import_router()
    out = mod["evaluations"](days=1, pair=None, tier="all")
    assert out["n_evaluations"] == 0
    assert out["failures_top"] == {}


def test_evaluations_aggregates_failure_reasons(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    base_event = {
        "event_type": "evaluation",
        "timestamp": now,
        "candle_ts": now,
        "state": {},
        "confirmed_fired": False,
        "scout_fired": False,
        "pre_scout_fired": False,
    }
    # 3 evals: each has confirmed_failures=[st_buy=False, vol<=1.2*ma]
    for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]:
        j.write({
            **base_event, "pair": pair,
            "confirmed_failures": ["st_buy=False", "vol<=1.2*ma"],
            "scout_failures": ["bull_just_formed=False"],
            "pre_scout_failures": ["pair_bullish_2tf_just_formed=False"],
        })
    mod = _import_router()
    out = mod["evaluations"](days=1, pair=None, tier="all")
    assert out["n_evaluations"] == 3
    assert out["n_pairs"] == 3
    # Each failure reason aggregated across the 3 events
    assert out["failures_top"]["st_buy=False"] == 3
    assert out["failures_top"]["vol<=1.2*ma"] == 3
    assert out["failures_top"]["bull_just_formed=False"] == 3
    # Tier-fired counts are zero (none fired)
    assert out["tier_fired_count"]["confirmed"] == 0


def test_evaluations_filter_by_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT", "BTC/USDT:USDT"]:
        j.write({
            "event_type": "evaluation",
            "timestamp": now, "candle_ts": now, "state": {},
            "pair": pair,
            "confirmed_fired": False, "confirmed_failures": ["adx<=25"],
            "scout_fired": False, "scout_failures": [],
            "pre_scout_fired": False, "pre_scout_failures": [],
        })
    mod = _import_router()
    # Filter to BTC only
    out = mod["evaluations"](days=1, pair="BTC/USDT:USDT", tier="all")
    assert out["n_evaluations"] == 2
    assert out["failures_top"]["adx<=25"] == 2


def test_evaluations_filter_by_tier(monkeypatch, tmp_path):
    """tier=confirmed only counts confirmed_failures, not scout/pre_scout."""
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    j.write({
        "event_type": "evaluation",
        "timestamp": now, "candle_ts": now, "state": {},
        "pair": "BTC/USDT:USDT",
        "confirmed_fired": False,
        "confirmed_failures": ["confirmed_only_reason"],
        "scout_fired": False,
        "scout_failures": ["scout_only_reason"],
        "pre_scout_fired": False, "pre_scout_failures": [],
    })
    mod = _import_router()
    out = mod["evaluations"](days=1, pair=None, tier="confirmed")
    assert "confirmed_only_reason" in out["failures_top"]
    assert "scout_only_reason" not in out["failures_top"]


def test_evaluations_counts_fired_tiers(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 1 confirmed fired, 2 scout fired, 0 pre_scout
    for fired_tier in ["confirmed", "scout", "scout"]:
        j.write({
            "event_type": "evaluation",
            "timestamp": now, "candle_ts": now, "state": {},
            "pair": "BTC/USDT:USDT",
            "confirmed_fired": fired_tier == "confirmed",
            "confirmed_failures": [],
            "scout_fired": fired_tier == "scout",
            "scout_failures": [],
            "pre_scout_fired": False, "pre_scout_failures": [],
        })
    mod = _import_router()
    out = mod["evaluations"](days=1, pair=None, tier="all")
    assert out["tier_fired_count"]["confirmed"] == 1
    assert out["tier_fired_count"]["scout"] == 2
    assert out["tier_fired_count"]["pre_scout"] == 0


# =================================================================== #
# /operations — R68 (alert helper)
# =================================================================== #
def test_alerts_empty_when_all_healthy():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={
            "n_evaluations": 100,
            "tier_fired_count": {"confirmed": 1, "scout": 2, "pre_scout": 0},
            "failures_top": {},
        },
        health={"ok": True}, recent_trades=3, journal_ok=True,
    )
    assert alerts == []


def test_alerts_flag_bot_stopped():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="stopped", n_pairs=17,
        eval_summary={}, health={"ok": True},
        recent_trades=0, journal_ok=True,
    )
    assert any("BOT_STATE" in a for a in alerts)


def test_alerts_flag_empty_whitelist():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=0,
        eval_summary={}, health={"ok": True},
        recent_trades=0, journal_ok=True,
    )
    assert any("WHITELIST_EMPTY" in a for a in alerts)


def test_alerts_flag_thin_whitelist():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=3,
        eval_summary={}, health={"ok": True},
        recent_trades=0, journal_ok=True,
    )
    assert any("WHITELIST_THIN" in a for a in alerts)


def test_alerts_flag_journal_stale():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={"n_evaluations": 5},
        health={"ok": False, "reason": "no events in last 1d"},
        recent_trades=0, journal_ok=False,
    )
    assert any("JOURNAL_STALE" in a for a in alerts)


def test_alerts_no_fires_24h_with_dominant_blocker():
    """R108: dominant blocker is named + actionable advice attached."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={
            "n_evaluations": 200,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {"vol<=1.2*ma": 178, "atr_not_rising": 152},
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    fire_alert = next((a for a in alerts if "NO_FIRES_24H" in a), None)
    assert fire_alert is not None
    assert "vol<=1.2*ma" in fire_alert
    # R108: vol<=1.2*ma is above the R89 floor (1.0) → suggests loosening
    assert "SUPERTREND_VOL_MULT" in fire_alert
    assert "1.0" in fire_alert


def test_alerts_no_pipeline_activity():
    """Bot running, but evaluations are zero → eval journal off / strategy stuck."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={"n_evaluations": 0},
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    assert any("NO_PIPELINE_ACTIVITY" in a for a in alerts)


def test_alerts_no_fires_alert_skipped_when_eval_count_low():
    """< 50 evaluations isn't enough sample to declare 'no fires' — silent."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={
            "n_evaluations": 10,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {"vol<=1.2*ma": 8},
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    assert not any("NO_FIRES_24H" in a for a in alerts)


# =================================================================== #
# R75: EVAL_RATE_LOW alert
# =================================================================== #
def test_eval_rate_low_fires_when_actual_under_50pct():
    """4h × 17 pairs × 4 evals/h = 272 expected. 100 actual = 37%, fires."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={
            "n_evaluations": 100,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {},
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=4.0,
    )
    rate_alert = next((a for a in alerts if "EVAL_RATE_LOW" in a), None)
    assert rate_alert is not None
    assert "100 evals" in rate_alert
    assert "4.0h" in rate_alert
    assert "17 pairs" in rate_alert


def test_eval_rate_low_silent_at_baseline():
    """4h × 17 pairs × 4 = 272 expected. 280 actual = 102%, silent."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={"n_evaluations": 280},
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=4.0,
    )
    assert not any("EVAL_RATE_LOW" in a for a in alerts)


def test_eval_rate_low_silent_when_uptime_below_30min():
    """Need 0.5h sample minimum to avoid false-fires on fresh starts."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={"n_evaluations": 1},
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=0.4,   # under threshold
    )
    assert not any("EVAL_RATE_LOW" in a for a in alerts)


def test_eval_rate_low_silent_when_no_pairs():
    """Can't compute expected rate without pairs; rule abstains."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=0,
        eval_summary={"n_evaluations": 0},
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=4.0,
    )
    assert not any("EVAL_RATE_LOW" in a for a in alerts)


def test_eval_rate_low_silent_at_default_zero_span():
    """When observed_span_hours not provided (legacy callers), no fire."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=17,
        eval_summary={"n_evaluations": 0},
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    assert not any("EVAL_RATE_LOW" in a for a in alerts)


def test_eval_rate_low_message_quotes_ratio():
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=10,
        eval_summary={"n_evaluations": 20},   # vs expected 1*10*4=40 → 50% boundary
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=1.0,
    )
    # Exactly 50% → does NOT fire (strict <0.5)
    assert not any("EVAL_RATE_LOW" in a for a in alerts)


def test_eval_rate_low_with_custom_expected_rate():
    """5m timeframe → 12 evals/h/pair baseline. Override should respect it."""
    mod = _import_router()
    alerts = mod["build_ops_alerts"](
        bot_state="running", n_pairs=10,
        eval_summary={"n_evaluations": 200},
        health={"ok": True}, recent_trades=0, journal_ok=True,
        observed_span_hours=4.0,
        expected_evals_per_hour_per_pair=12.0,   # 5m timeframe
    )
    # Expected = 4 × 10 × 12 = 480; actual 200 = 41% → fires
    assert any("EVAL_RATE_LOW" in a for a in alerts)


# =================================================================== #
# /operations — R68 (endpoint integration)
# =================================================================== #
def test_operations_handles_freqtrade_unreachable(monkeypatch, tmp_path):
    """When freqtrade is down, endpoint still returns a response; errors logged."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("connection refused"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert "bot" in out
    assert "errors" in out
    assert "bot" in out["errors"]
    # Status degraded because bot unknown + alerts fired
    assert out["status"] == "degraded"


def test_operations_returns_full_snapshot_when_healthy(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    j.write({
        "event_type": "evaluation", "timestamp": now,
        "candle_ts": now, "state": {}, "pair": "BTC/USDT:USDT",
        "confirmed_fired": True, "confirmed_failures": [],
        "scout_fired": False, "scout_failures": [],
        "pre_scout_fired": False, "pre_scout_failures": [],
    })
    mod = _import_router()
    fake_responses = {
        "/api/v1/show_config": {
            "state": "running", "dry_run": True,
            "strategy": "SupertrendStrategy", "max_open_trades": 3,
        },
        "/api/v1/whitelist": {"whitelist": ["BTC/USDT:USDT"] * 17},
    }
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=lambda path, **kw: fake_responses.get(path, {}),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert out["bot"]["state"] == "running"
    assert out["whitelist"]["n_pairs"] == 17
    assert out["pipeline"]["evaluations"]["n_evaluations"] == 1
    assert out["pipeline"]["evaluations"]["tier_fired_count"]["confirmed"] == 1
    assert out["status"] == "ok"
    assert out["alert_count"] == 0


def test_operations_includes_switchboard_view(monkeypatch, tmp_path):
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    monkeypatch.setenv("SUPERTREND_KELLY_MODE", "continuous")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("doesn't matter for this test"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    sw = out["switchboard"]
    assert sw["fr_alpha"] == "1"
    assert sw["kelly_mode"] == "continuous"
    # Defaults preserved for unset
    assert sw["live_mode"] == "0"
    assert sw["regime_filter"] == "1"


def test_operations_switchboard_exposes_entry_gate_envs(monkeypatch, tmp_path):
    """R94: deploy verification — operator must see the actual entry-gate
    env vars (R87 disable_confirmed / R89 vol_mult / R91 trio) in /operations,
    not just the risk/sizing block."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "1.0")
    monkeypatch.setenv("SUPERTREND_QUALITY_MIN", "0.4")
    monkeypatch.setenv("SUPERTREND_ADX_MIN", "20")
    monkeypatch.setenv("SUPERTREND_REQUIRE_ATR_RISING", "0")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    sw = out["switchboard"]
    assert sw["disable_confirmed"] == "1"
    assert sw["vol_mult"] == "1.0"
    assert sw["quality_min"] == "0.4"
    assert sw["adx_min"] == "20"
    assert sw["require_atr_rising"] == "0"


def test_operations_exposes_per_pair_productivity(monkeypatch, tmp_path):
    """R95: /operations.performance must expose per-pair counts so operator
    can identify dead-weight pairs (R90 found BTC=6/8 wins, others=0)."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 3 BTC trades (2W 1L), 1 ADA win, 0 ETH/SOL trades
    for pnl in (1.5, 0.8, -0.3):
        j.write({
            "event_type": "exit", "timestamp": now,
            "pair": "BTC/USDT:USDT", "entry_tag": "scout",
            "pnl_pct": pnl, "pnl_usd": pnl * 10, "duration_hours": 4,
            "exit_reason": "trailing_stop",
        })
    j.write({
        "event_type": "exit", "timestamp": now,
        "pair": "ADA/USDT:USDT", "entry_tag": "pre_scout",
        "pnl_pct": 0.31, "pnl_usd": 3.1, "duration_hours": 6,
        "exit_reason": "trailing_stop",
    })
    mod = _import_router()
    fake = {
        "/api/v1/show_config": {"state": "running", "dry_run": True},
        "/api/v1/whitelist": {
            "whitelist": [
                "BTC/USDT:USDT", "ADA/USDT:USDT",
                "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
            ],
        },
    }
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=lambda p, **kw: fake.get(p, {}),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)

    perf = out["performance"]
    assert perf["active_pair_count"] == 2
    assert perf["silent_pair_count"] == 3
    assert set(perf["silent_pairs"]) == {
        "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
    }
    # top_pairs sorted by n desc — BTC first (3), ADA second (1)
    assert perf["top_pairs"][0]["pair"] == "BTC/USDT:USDT"
    assert perf["top_pairs"][0]["n_trades"] == 3
    assert perf["top_pairs"][0]["wins"] == 2
    assert perf["top_pairs"][0]["losses"] == 1
    assert perf["top_pairs"][1]["pair"] == "ADA/USDT:USDT"
    assert perf["top_pairs"][1]["wins"] == 1


def test_operations_silent_pairs_empty_when_whitelist_unreachable(monkeypatch, tmp_path):
    """R95: when freqtrade /whitelist fails, silent_pairs falls back to []
    (don't infer false 'silent' from missing data)."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "exit", "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "BTC/USDT:USDT", "pnl_pct": 1.0, "pnl_usd": 10.0,
        "duration_hours": 1, "entry_tag": "scout", "exit_reason": "tp",
    })
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("freqtrade unreachable"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    perf = out["performance"]
    assert perf["silent_pairs"] == []
    assert perf["silent_pair_count"] == 0
    assert perf["active_pair_count"] == 1


def test_operations_breaks_down_guard_rejections(monkeypatch, tmp_path):
    """R101: pipeline.guard_rejections_top groups skipped events by [GuardName]."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 6 MaxPositionGuard rejections + 2 DailyLossGuard + 1 R57 (non-guard)
    for _ in range(6):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "pair": "BTC/USDT:USDT", "side": "long",
            "reason": "R97 guard: [L:strategy] [MaxPositionGuard] over 30%",
        })
    for _ in range(2):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "pair": "BTC/USDT:USDT", "side": "long",
            "reason": "R97 guard: [L:account] [DailyLossGuard] over 5%",
        })
    j.write({
        "event_type": "skipped", "timestamp": now,
        "pair": "BTC/USDT:USDT", "side": "long",
        "reason": "R57 pre-entry filter: orderbook microstructure adverse",
    })

    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    pipe = out["pipeline"]
    assert pipe["recent_skipped"] == 9
    assert pipe["guard_rejections_top"] == {
        "MaxPositionGuard": 6,
        "DailyLossGuard": 2,
    }
    # GUARD_REJECTING_HEAVILY fires for MaxPositionGuard (6 >= 5) but not DailyLoss (2 < 5)
    heavy_alerts = [a for a in out["alerts"] if "GUARD_REJECTING_HEAVILY" in a]
    assert len(heavy_alerts) == 1
    assert "MaxPositionGuard blocked 6" in heavy_alerts[0]


def test_operations_suppresses_consec_loss_rejection_spam(monkeypatch, tmp_path):
    """R102: ConsecutiveLossGuard heavy rejections are by-design during
    pause — already covered by GUARD_PAUSED (R98). Don't double-alert."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 12 ConsecutiveLossGuard rejections (way over 5 threshold) — but
    # because pause spams every check, this is expected, not actionable.
    for _ in range(12):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "pair": "BTC/USDT:USDT", "side": "long",
            "reason": "R97 guard: [L:account] [ConsecutiveLossGuard] paused 23h",
        })

    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    # Counter is still populated for visibility
    assert out["pipeline"]["guard_rejections_top"]["ConsecutiveLossGuard"] == 12
    # But no GUARD_REJECTING_HEAVILY for it (covered by GUARD_PAUSED)
    assert not any(
        "GUARD_REJECTING_HEAVILY" in a and "ConsecutiveLossGuard" in a
        for a in out["alerts"]
    )


def test_operations_suppresses_daily_loss_rejection_spam(monkeypatch, tmp_path):
    """R102: DailyLossGuard heavy rejections — covered by GUARD_NEAR_DAILY_LIMIT."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(8):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "reason": "R97 guard: [L:account] [DailyLossGuard] cap reached",
        })
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert out["pipeline"]["guard_rejections_top"]["DailyLossGuard"] == 8
    assert not any(
        "GUARD_REJECTING_HEAVILY" in a and "DailyLossGuard" in a
        for a in out["alerts"]
    )


def test_operations_no_guard_rejection_alert_below_threshold(monkeypatch, tmp_path):
    """R101: alert quiet when each guard rejects <5 in window."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(3):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "pair": "BTC/USDT:USDT", "side": "long",
            "reason": "R97 guard: [L:trade] [CooldownGuard] 30s remaining",
        })
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert not any("GUARD_REJECTING_HEAVILY" in a for a in out["alerts"])


def test_operations_skip_reasons_top_works_for_non_guard_skips(monkeypatch, tmp_path):
    """R101: skip_reasons_top groups all skipped events, not just guard ones."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(4):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "reason": "R57 pre-entry filter: funding contra-signal",
        })
    j.write({
        "event_type": "skipped", "timestamp": now,
        "reason": "R97 guard: [L:strategy] [MaxLeverageGuard] too high",
    })
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    top = out["pipeline"]["skip_reasons_top"]
    # Tag from " — " split — both reasons get truncated to 60 chars
    assert any("R57 pre-entry filter: funding contra-signal" in k for k in top.keys())
    assert any("R97 guard: [L:strategy] [MaxLeverageGuard] too high" in k for k in top.keys())


def test_operations_alerts_guards_never_fired_when_skips_no_guard_origin(monkeypatch, tmp_path):
    """R105: many SkippedEvents but ZERO from R97 guards → silent-failure pattern.
    This is the R104 incident exactly — pipeline rejects things but guards
    have somehow never been invoked. Operator must verify with docker exec."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # 12 skipped events — none from R97 guards (e.g. R57, regime, R58)
    for reason in [
        "R57 pre-entry filter: orderbook adverse",
        "R57 pre-entry filter: funding contra",
        "regime: DEAD",
        "direction_concentration: 2 open longs, cap 2",
        "R58 correlation_block: BTC concentrated",
    ] * 3:   # 15 events but we'll dedupe via uniqueness implicitly
        j.write({
            "event_type": "skipped", "timestamp": now,
            "pair": "BTC/USDT:USDT", "side": "long",
            "reason": reason,
        })

    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)

    assert out["pipeline"]["recent_skipped"] >= 10
    assert out["pipeline"]["guard_rejections_top"] == {}
    nf = [a for a in out["alerts"] if "GUARDS_NEVER_FIRED" in a]
    assert len(nf) == 1
    assert "R104 silent-failure pattern" in nf[0]
    assert "docker exec" in nf[0]


def test_operations_no_guards_never_fired_alert_when_guards_active(monkeypatch, tmp_path):
    """If at least one R97 guard rejection appears, the silent-failure
    pattern is broken — don't alert."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # Mix: 11 non-guard skips + 1 guard skip → guards proven alive
    for _ in range(11):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "reason": "R57 pre-entry filter: orderbook adverse",
        })
    j.write({
        "event_type": "skipped", "timestamp": now,
        "reason": "R97 guard: [L:trade] [CooldownGuard] 30s remaining",
    })

    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert not any("GUARDS_NEVER_FIRED" in a for a in out["alerts"])


def test_operations_no_guards_never_fired_alert_at_low_skip_count(monkeypatch, tmp_path):
    """Fewer than 10 skips → too few to declare silent-failure."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(3):
        j.write({
            "event_type": "skipped", "timestamp": now,
            "reason": "R57 pre-entry filter: orderbook adverse",
        })
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert not any("GUARDS_NEVER_FIRED" in a for a in out["alerts"])


def test_operations_switchboard_exposes_guards_safety_envs(monkeypatch, tmp_path):
    """R105: switchboard must surface SUPERTREND_GUARDS_ENABLED and
    SUPERTREND_GUARDS_REQUIRE_LOAD so operator can verify LIVE-mode safety
    settings without ssh-ing into the container."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("SUPERTREND_GUARDS_ENABLED", "1")
    monkeypatch.setenv("SUPERTREND_GUARDS_REQUIRE_LOAD", "1")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    sw = out["switchboard"]
    assert sw["guards_enabled"] == "1"
    assert sw["guards_require_load"] == "1"


def test_operations_switchboard_guards_safety_defaults(monkeypatch, tmp_path):
    """R105 defaults: guards_enabled=1 (on), guards_require_load=0 (fail-open
    for dry-run safety)."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    monkeypatch.delenv("SUPERTREND_GUARDS_REQUIRE_LOAD", raising=False)
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    sw = out["switchboard"]
    assert sw["guards_enabled"] == "1"
    assert sw["guards_require_load"] == "0"


def test_operations_includes_guard_state_block(monkeypatch, tmp_path):
    """R98: /operations.guards must surface guard state so operator can
    see daily_loss, consecutive_losses, paused_until without VPS shell."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    fake_state = {
        "daily_loss": 12.5,
        "daily_loss_limit_pct": 5.0,
        "consecutive_losses": 2,
        "max_streak": 5,
        "paused_until": 0,
        "cooldown_symbols": 1,
        "drawdown_peak_equity": 1100.0,
        "drawdown_max_pct": 10.0,
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ), patch(
        "guards.pipeline.get_state_summary",
        return_value=fake_state,
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    g = out["guards"]
    assert g["available"] is True
    assert g["daily_loss"] == 12.5
    assert g["consecutive_losses"] == 2
    assert g["max_streak"] == 5
    # paused_until=0 → no GUARD_PAUSED alert
    assert not any("GUARD_PAUSED" in a for a in out["alerts"])


def test_operations_guards_unavailable_when_module_missing(monkeypatch, tmp_path):
    """R98: guards.available=False when the module can't be imported.
    The /operations call must NOT crash — fail-soft per safety convention."""
    import sys
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    real_mod = sys.modules.get("guards.pipeline")
    sys.modules["guards.pipeline"] = None
    try:
        mod = _import_router()
        with patch(
            "src.routers.supertrend._ft_get",
            side_effect=RuntimeError("not relevant"),
        ):
            out = mod["operations"](eval_window_days=1, perf_window_days=7)
    finally:
        if real_mod is not None:
            sys.modules["guards.pipeline"] = real_mod
        else:
            sys.modules.pop("guards.pipeline", None)
    assert out["guards"]["available"] is False
    assert "error" in out["guards"]


def test_operations_alerts_guard_paused_when_streak_tripped(monkeypatch, tmp_path):
    """R98: GUARD_PAUSED alert fires when paused_until > now."""
    import time
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    fake_state = {
        "daily_loss": 0,
        "daily_loss_limit_pct": 5.0,
        "consecutive_losses": 5,
        "max_streak": 5,
        "paused_until": time.time() + 7200,   # paused for 2 more hours
        "drawdown_peak_equity": 1000.0,
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ), patch(
        "guards.pipeline.get_state_summary",
        return_value=fake_state,
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    paused_alerts = [a for a in out["alerts"] if "GUARD_PAUSED" in a]
    assert len(paused_alerts) == 1
    assert "5 consecutive losses" in paused_alerts[0]


def test_operations_alerts_guard_near_daily_limit(monkeypatch, tmp_path):
    """R98: GUARD_NEAR_DAILY_LIMIT fires when daily loss > 80% of cap."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    # peak=$1000, cap=5% = $50, daily_loss=$45 → 90% of cap
    fake_state = {
        "daily_loss": 45.0,
        "daily_loss_limit_pct": 5.0,
        "consecutive_losses": 0,
        "paused_until": 0,
        "drawdown_peak_equity": 1000.0,
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ), patch(
        "guards.pipeline.get_state_summary",
        return_value=fake_state,
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    near_limit = [a for a in out["alerts"] if "GUARD_NEAR_DAILY_LIMIT" in a]
    assert len(near_limit) == 1
    assert "90%" in near_limit[0]


def test_operations_no_guard_alerts_when_state_clean(monkeypatch, tmp_path):
    """R98: when no guards are tripped or near limit, no GUARD_* alerts."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    fake_state = {
        "daily_loss": 5.0,                   # 10% of $50 cap → far from 80%
        "daily_loss_limit_pct": 5.0,
        "consecutive_losses": 1,
        "paused_until": 0,
        "drawdown_peak_equity": 1000.0,
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ), patch(
        "guards.pipeline.get_state_summary",
        return_value=fake_state,
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    assert not any("GUARD_PAUSED" in a for a in out["alerts"])
    assert not any("GUARD_NEAR_DAILY_LIMIT" in a for a in out["alerts"])


def test_operations_switchboard_entry_gate_defaults(monkeypatch, tmp_path):
    """R94: when no env override is set, switchboard reports the safe defaults."""
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    for k in (
        "SUPERTREND_DISABLE_CONFIRMED",
        "SUPERTREND_VOL_MULT",
        "SUPERTREND_QUALITY_MIN",
        "SUPERTREND_ADX_MIN",
        "SUPERTREND_REQUIRE_ATR_RISING",
    ):
        monkeypatch.delenv(k, raising=False)
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=RuntimeError("not relevant"),
    ):
        out = mod["operations"](eval_window_days=1, perf_window_days=7)
    sw = out["switchboard"]
    assert sw["disable_confirmed"] == "0"
    assert sw["vol_mult"] == "1.2"
    assert sw["quality_min"] == "0.5"
    assert sw["adx_min"] == "default"   # sentinel — strategy uses self.adx_threshold
    assert sw["require_atr_rising"] == "1"


# =================================================================== #
# /force_entry — R70
# =================================================================== #
def test_force_entry_disabled_by_default(monkeypatch):
    """SUPERTREND_FORCE_ENTRY_ENABLED unset → 403."""
    from fastapi import HTTPException
    monkeypatch.delenv("SUPERTREND_FORCE_ENTRY_ENABLED", raising=False)
    mod = _import_router()
    with pytest.raises(HTTPException) as exc:
        mod["force_entry"](
            pair="BTC/USDT:USDT", side="long",
            stake_amount=None, verify_journal=False,
        )
    assert exc.value.status_code == 403
    assert "force_entry disabled" in str(exc.value.detail).lower()


def test_force_entry_disabled_when_env_zero(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("SUPERTREND_FORCE_ENTRY_ENABLED", "0")
    mod = _import_router()
    with pytest.raises(HTTPException) as exc:
        mod["force_entry"](
            pair="BTC/USDT:USDT", side="long",
            stake_amount=None, verify_journal=False,
        )
    assert exc.value.status_code == 403


def test_force_entry_refuses_in_live_mode(monkeypatch):
    """If freqtrade reports dry_run=False, refuse."""
    from fastapi import HTTPException
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_FORCE_ENTRY_ENABLED", "1")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        return_value={"dry_run": False, "state": "running"},
    ):
        with pytest.raises(HTTPException) as exc:
            mod["force_entry"](
                pair="BTC/USDT:USDT", side="long",
                stake_amount=None, verify_journal=False,
            )
    assert exc.value.status_code == 403
    assert "live mode" in str(exc.value.detail).lower()


def test_force_entry_succeeds_in_dry_run(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_FORCE_ENTRY_ENABLED", "1")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        return_value={"dry_run": True, "state": "running"},
    ), patch(
        "src.routers.supertrend._ft_post",
        return_value={"status": "Order forced"},
    ):
        out = mod["force_entry"](
            pair="BTC/USDT:USDT", side="long",
            stake_amount=None, verify_journal=False,
        )
    assert out["pair"] == "BTC/USDT:USDT"
    assert out["side"] == "long"
    assert out["forceenter_response"] == {"status": "Order forced"}
    assert "duration_ms" in out


def test_force_entry_passes_stake_override(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_FORCE_ENTRY_ENABLED", "1")
    mod = _import_router()
    captured_body = {}

    def fake_post(path, body, **kw):
        captured_body.update({"path": path, "body": body})
        return {"status": "OK"}

    with patch(
        "src.routers.supertrend._ft_get",
        return_value={"dry_run": True},
    ), patch(
        "src.routers.supertrend._ft_post",
        side_effect=fake_post,
    ):
        mod["force_entry"](
            pair="ETH/USDT:USDT", side="short",
            stake_amount=250.0, verify_journal=False,
        )
    assert captured_body["path"] == "/api/v1/forceenter"
    assert captured_body["body"]["stakeamount"] == 250.0
    assert captured_body["body"]["side"] == "short"


def test_force_entry_returns_error_on_freqtrade_failure(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("SUPERTREND_FORCE_ENTRY_ENABLED", "1")
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        return_value={"dry_run": True},
    ), patch(
        "src.routers.supertrend._ft_post",
        side_effect=RuntimeError("max_open_trades reached"),
    ):
        out = mod["force_entry"](
            pair="BTC/USDT:USDT", side="long",
            stake_amount=None, verify_journal=False,
        )
    assert "error" in out
    assert "max_open_trades" in out["error"]
    assert out["verified"] is False


def test_verify_entry_in_journal_finds_match(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "BTC/USDT:USDT", "side": "long",
    })
    mod = _import_router()
    res = mod["verify_entry_in_journal"]("BTC/USDT:USDT", timeout_sec=2.0,
                                          poll_interval=0.1)
    assert res is not None
    assert res["pair"] == "BTC/USDT:USDT"


def test_verify_entry_in_journal_returns_none_on_no_match(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from strategies.journal import TradeJournal
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": "ETH/USDT:USDT", "side": "long",   # different pair
    })
    mod = _import_router()
    res = mod["verify_entry_in_journal"]("BTC/USDT:USDT", timeout_sec=1.0,
                                          poll_interval=0.1)
    assert res is None


def test_verify_entry_handles_missing_journal_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "nope"))
    mod = _import_router()
    res = mod["verify_entry_in_journal"]("BTC/USDT:USDT", timeout_sec=0.5,
                                          poll_interval=0.1)
    assert res is None


# =================================================================== #
# /scanner — R71 closest_to_fire enrichment
# =================================================================== #
def test_closest_to_fire_returns_none_for_no_event():
    mod = _import_router()
    assert mod["closest_to_fire"](None) is None


def test_closest_to_fire_already_fired_returns_zero_distance():
    mod = _import_router()
    ev = {
        "confirmed_fired": True, "confirmed_failures": [],
        "scout_fired": False, "scout_failures": ["bull_just_formed=False"],
        "pre_scout_fired": False, "pre_scout_failures": [],
    }
    res = mod["closest_to_fire"](ev)
    assert res["tier"] == "confirmed"
    assert res["fire_distance"] == 0
    assert res["already_fired"] is True


def test_closest_to_fire_picks_tier_with_fewest_failures():
    mod = _import_router()
    ev = {
        "confirmed_fired": False,
        "confirmed_failures": ["a", "b", "c", "d"],   # 4 fails
        "scout_fired": False,
        "scout_failures": ["x"],                       # 1 fail ← closest
        "pre_scout_fired": False,
        "pre_scout_failures": ["y", "z"],              # 2 fails
    }
    res = mod["closest_to_fire"](ev)
    assert res["tier"] == "scout"
    assert res["fire_distance"] == 1
    assert res["remaining"] == ["x"]
    assert res["already_fired"] is False


def test_closest_to_fire_tiebreak_prefers_first_tier_alphabetically():
    """When two tiers tie, sort puts confirmed first by enumeration order."""
    mod = _import_router()
    ev = {
        "confirmed_fired": False, "confirmed_failures": ["a"],
        "scout_fired": False, "scout_failures": ["b"],
        "pre_scout_fired": False, "pre_scout_failures": [],
    }
    # pre_scout has 0 fails so it wins
    res = mod["closest_to_fire"](ev)
    assert res["tier"] == "pre_scout"
    assert res["fire_distance"] == 0
    # already_fired False because pre_scout_fired wasn't True
    assert res["already_fired"] is False


def test_latest_eval_per_pair_uses_newest_per_pair(monkeypatch, tmp_path):
    """When same pair has multiple evaluations, only the newest wins."""
    from datetime import datetime, timedelta, timezone
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    j.write({
        "event_type": "evaluation",
        "timestamp": (now - timedelta(minutes=30)).isoformat(),
        "pair": "BTC/USDT:USDT",
        "candle_ts": "old", "state": {},
        "confirmed_fired": False, "confirmed_failures": ["a", "b", "c"],
        "scout_fired": False, "scout_failures": [],
        "pre_scout_fired": False, "pre_scout_failures": [],
    })
    j.write({
        "event_type": "evaluation",
        "timestamp": (now - timedelta(minutes=5)).isoformat(),
        "pair": "BTC/USDT:USDT",
        "candle_ts": "new", "state": {},
        "confirmed_fired": False, "confirmed_failures": ["a"],
        "scout_fired": False, "scout_failures": [],
        "pre_scout_fired": False, "pre_scout_failures": [],
    })
    mod = _import_router()
    latest = mod["latest_eval_per_pair"](window_hours=1.0)
    assert "BTC/USDT:USDT" in latest
    # Newest event wins → confirmed_failures has 1 entry
    assert latest["BTC/USDT:USDT"]["confirmed_failures"] == ["a"]


def test_latest_eval_per_pair_returns_empty_on_missing_journal(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path / "missing"))
    mod = _import_router()
    assert mod["latest_eval_per_pair"]() == {}


def test_scanner_sorts_by_fire_distance(monkeypatch, tmp_path):
    """Pair with smaller fire_distance lands at top, even if alignment is lower."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    # CLOSE pair has 1 failure; FAR pair has 4 failures (but higher alignment)
    j.write({
        "event_type": "evaluation", "timestamp": now,
        "pair": "CLOSE/USDT:USDT", "candle_ts": now, "state": {},
        "confirmed_fired": False, "confirmed_failures": ["st_buy=False"],
        "scout_fired": False, "scout_failures": ["bull_just_formed=False"],
        "pre_scout_fired": False, "pre_scout_failures": ["pair_bullish_2tf_just_formed=False"],
    })
    j.write({
        "event_type": "evaluation", "timestamp": now,
        "pair": "FAR/USDT:USDT", "candle_ts": now, "state": {},
        "confirmed_fired": False,
        "confirmed_failures": ["a", "b", "c", "d"],
        "scout_fired": False,
        "scout_failures": ["a", "b", "c", "d"],
        "pre_scout_fired": False,
        "pre_scout_failures": ["a", "b", "c", "d"],
    })

    wl = {"whitelist": ["CLOSE/USDT:USDT", "FAR/USDT:USDT"]}
    candles = {
        "CLOSE/USDT:USDT": {
            "columns": ["st_1d", "dir_4h_score", "st_1h", "st_trend",
                        "direction_score"],
            "data": [[1, 0.5, 1, 1, 0.5]],   # alignment 4
        },
        "FAR/USDT:USDT": {
            "columns": ["st_1d", "dir_4h_score", "st_1h", "st_trend",
                        "direction_score"],
            "data": [[1, 0.8, 1, 1, 0.9]],   # alignment 4 + higher dir
        },
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=10)
    # CLOSE first because fire_distance=1 vs FAR's 4
    assert out["pairs"][0]["pair"] == "CLOSE/USDT:USDT"
    assert out["pairs"][0]["closest_to_fire"]["fire_distance"] == 1
    assert out["pairs"][1]["pair"] == "FAR/USDT:USDT"


def test_scanner_includes_pairs_near_fire_summary(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    j.write({
        "event_type": "evaluation", "timestamp": now,
        "pair": "BTC/USDT:USDT", "candle_ts": now, "state": {},
        "confirmed_fired": False, "confirmed_failures": ["st_buy=False"],
        "scout_fired": False, "scout_failures": [],
        "pre_scout_fired": False, "pre_scout_failures": ["x"],
    })

    wl = {"whitelist": ["BTC/USDT:USDT"]}
    candles = {
        "BTC/USDT:USDT": {
            "columns": ["st_1d", "direction_score"],
            "data": [[1, 0.5]],
        },
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=5)
    assert "pairs_near_fire" in out
    # Scout has 0 failures → already_fired=False but distance=0 → top
    near = out["pairs_near_fire"]
    assert len(near) == 1
    assert near[0]["pair"] == "BTC/USDT:USDT"
    assert near[0]["tier"] == "scout"
    assert near[0]["fire_distance"] == 0


def test_scanner_pairs_without_eval_sink_to_bottom(monkeypatch, tmp_path):
    """Pair with no recent EvaluationEvent → fire_distance=999 → bottom."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from strategies.journal import TradeJournal

    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", str(tmp_path))
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    j.write({
        "event_type": "evaluation", "timestamp": now,
        "pair": "WITH_EVAL/USDT:USDT", "candle_ts": now, "state": {},
        "confirmed_fired": False, "confirmed_failures": ["st_buy=False"],
        "scout_fired": False, "scout_failures": [],
        "pre_scout_fired": False, "pre_scout_failures": [],
    })

    wl = {"whitelist": ["WITH_EVAL/USDT:USDT", "NO_EVAL/USDT:USDT"]}
    candles = {
        "WITH_EVAL/USDT:USDT": {
            "columns": ["st_1d", "direction_score"], "data": [[1, 0.5]],
        },
        "NO_EVAL/USDT:USDT": {
            "columns": ["st_1d", "direction_score"], "data": [[1, 0.9]],
        },
    }
    mod = _import_router()
    with patch(
        "src.routers.supertrend._ft_get",
        side_effect=_mock_ft_get(wl, candles),
    ):
        out = mod["scanner"](timeframe="15m", limit=5)
    # WITH_EVAL first (fire_distance=0); NO_EVAL last (closest_to_fire=None)
    assert out["pairs"][0]["pair"] == "WITH_EVAL/USDT:USDT"
    assert out["pairs"][1]["pair"] == "NO_EVAL/USDT:USDT"
    assert out["pairs"][1]["closest_to_fire"] is None
