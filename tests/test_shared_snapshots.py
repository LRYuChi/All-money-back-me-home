"""Tests for shared.snapshots — types/builder/writer."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.snapshots import (
    BacktestSnapshot,
    InMemorySnapshotWriter,
    NoOpSnapshotWriter,
    build_snapshot,
    build_writer,
    current_git_commit,
    record_safe,
)


# ================================================================== #
# BacktestSnapshot.to_row
# ================================================================== #
def test_to_row_minimum_fields():
    snap = BacktestSnapshot(
        kind="smart_money_p3_gate",
        config={"top_n": 50, "cutoffs": ["2026-01-01"]},
        report={"results": [], "summary": {}},
    )
    row = snap.to_row()
    assert row["kind"] == "smart_money_p3_gate"
    assert row["config"]["top_n"] == 50
    assert row["report"]["results"] == []
    assert "created_at" in row
    # Optional fields are None
    assert row["git_commit"] is None
    assert row["decision_pass"] is None


def test_to_row_decision_fields():
    snap = BacktestSnapshot(
        kind="x", config={}, report={},
        decision_pass=True, decision_reason="median PnL > naive baseline",
        n_trades=42, median_pnl_pct=0.123, max_drawdown=0.085,
    )
    row = snap.to_row()
    assert row["decision_pass"] is True
    assert row["n_trades"] == 42
    assert row["median_pnl_pct"] == 0.123
    assert row["max_drawdown"] == 0.085


def test_to_row_lists_are_copied():
    """Caller mutating the returned dict must NOT mutate the snapshot."""
    snap = BacktestSnapshot(
        kind="x", config={"k": "v"}, report={"r": 1},
        cutoffs=["2026-01-01", "2026-02-01"],
        data_window={"from": "2026-01-01", "to": "2026-02-01"},
    )
    row = snap.to_row()
    row["config"]["k"] = "MUTATED"
    row["cutoffs"].append("XXX")
    row["data_window"]["new"] = "XXX"

    assert snap.config["k"] == "v"
    assert snap.cutoffs == ["2026-01-01", "2026-02-01"]
    assert "new" not in snap.data_window


# ================================================================== #
# build_snapshot helper
# ================================================================== #
def test_build_snapshot_auto_fills_git(monkeypatch):
    """When git_commit/dirty not supplied, builder calls current_git_commit."""
    from shared.snapshots import builder

    monkeypatch.setattr(builder, "current_git_commit", lambda: ("abc1234", False))
    snap = build_snapshot(kind="x", config={}, report={})
    assert snap.git_commit == "abc1234"
    assert snap.git_dirty is False


def test_build_snapshot_explicit_git_skips_lookup(monkeypatch):
    from shared.snapshots import builder

    called = {"n": 0}
    def mock_git():
        called["n"] += 1
        return "X", True
    monkeypatch.setattr(builder, "current_git_commit", mock_git)

    build_snapshot(kind="x", config={}, report={}, git_commit="explicit", git_dirty=True)
    # Auto-lookup should not have run
    assert called["n"] == 0


def test_build_snapshot_passes_through_metrics():
    snap = build_snapshot(
        kind="x", config={}, report={},
        n_trades=10, median_pnl_pct=0.05, max_drawdown=0.12,
        decision_pass=False, decision_reason="median negative",
    )
    assert snap.n_trades == 10
    assert snap.decision_reason == "median negative"


# ================================================================== #
# current_git_commit — runs against real repo
# ================================================================== #
def test_current_git_commit_inside_repo():
    """In this repo, git is available and we should get a short SHA."""
    sha, dirty = current_git_commit()
    if sha is None:
        pytest.skip("not inside a git repo (CI sandbox?)")
    assert isinstance(sha, str)
    assert 4 <= len(sha) <= 40
    assert dirty in (True, False)


# ================================================================== #
# NoOpSnapshotWriter
# ================================================================== #
def test_noop_returns_zero_id():
    w = NoOpSnapshotWriter()
    snap = BacktestSnapshot(kind="x", config={}, report={})
    assert w.record(snap) == 0


# ================================================================== #
# InMemorySnapshotWriter
# ================================================================== #
def test_inmemory_assigns_incrementing_ids():
    w = InMemorySnapshotWriter()
    a = BacktestSnapshot(kind="x", config={}, report={})
    b = BacktestSnapshot(kind="x", config={}, report={})

    id_a = w.record(a)
    id_b = w.record(b)

    assert id_a == 1 and id_b == 2
    assert a.id == 1 and b.id == 2


def test_inmemory_by_kind_filters():
    w = InMemorySnapshotWriter()
    w.record(BacktestSnapshot(kind="A", config={}, report={}))
    w.record(BacktestSnapshot(kind="B", config={}, report={}))
    w.record(BacktestSnapshot(kind="A", config={}, report={}))

    assert len(w.by_kind("A")) == 2
    assert len(w.by_kind("B")) == 1


# ================================================================== #
# record_safe — never raises
# ================================================================== #
class BrokenWriter:
    def record(self, snap):
        raise RuntimeError("DB exploded")


def test_record_safe_swallows_exceptions():
    snap = BacktestSnapshot(kind="x", config={}, report={})
    res = record_safe(BrokenWriter(), snap)
    assert res is None


def test_record_safe_returns_id_on_success():
    snap = BacktestSnapshot(kind="x", config={}, report={})
    w = InMemorySnapshotWriter()
    res = record_safe(w, snap)
    assert res == 1


def test_record_safe_continues_after_previous_failure():
    """Critical invariant: snapshot N failure must not block snapshot N+1."""
    class FlakyWriter:
        def __init__(self):
            self.calls = 0
            self.records = []
        def record(self, s):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("hiccup")
            self.records.append(s)
            return self.calls

    w = FlakyWriter()
    snaps = [BacktestSnapshot(kind="x", config={}, report={}) for _ in range(3)]
    results = [record_safe(w, s) for s in snaps]
    assert results == [None, 2, 3]
    assert len(w.records) == 2


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""

    w = build_writer(S())
    assert isinstance(w, NoOpSnapshotWriter)


def test_factory_postgres_when_dsn_set():
    from shared.snapshots.writer import PostgresSnapshotWriter

    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""

    w = build_writer(S())
    assert isinstance(w, PostgresSnapshotWriter)


def test_factory_postgres_priority_over_supabase():
    from shared.snapshots.writer import PostgresSnapshotWriter

    class S:
        database_url = "postgresql://x"
        supabase_url = "https://x.supabase.co"
        supabase_service_key = "key"

    w = build_writer(S())
    assert isinstance(w, PostgresSnapshotWriter)
