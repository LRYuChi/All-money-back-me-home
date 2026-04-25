"""Tests for strategy_engine.cli.loader (round 33)."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_engine import InMemoryStrategyRegistry
from strategy_engine.cli.loader import _cmd_load, _cmd_validate, _collect_files


# ================================================================== #
# Fixtures
# ================================================================== #
def make_yaml(name: str, *, enabled: bool = True, mode: str = "shadow") -> str:
    flag_line = f"enabled: {str(enabled).lower()}"
    return (
        f"id: {name}\n"
        f"market: crypto\n"
        f"symbol: BTC\n"
        f"timeframe: 1h\n"
        f"mode: {mode}\n"
        f"{flag_line}\n"
        f"entry:\n"
        f"  long:\n"
        f"    all_of:\n"
        f"      - 'fused.direction == \"long\"'\n"
    )


def write_strategy(tmp_path: Path, name: str, **kwargs) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(make_yaml(name, **kwargs))
    return p


# ================================================================== #
# _collect_files
# ================================================================== #
def test_collect_files_returns_sorted_yaml_in_dir(tmp_path):
    write_strategy(tmp_path, "z_last")
    write_strategy(tmp_path, "a_first")
    write_strategy(tmp_path, "m_middle")

    class A:
        file = None
        dir = tmp_path
        pattern = "*.yaml"
    files = _collect_files(A())
    assert [f.stem for f in files] == ["a_first", "m_middle", "z_last"]


def test_collect_files_respects_custom_pattern(tmp_path):
    write_strategy(tmp_path, "a")
    (tmp_path / "skip.txt").write_text("not yaml")

    class A:
        file = None
        dir = tmp_path
        pattern = "*.yaml"
    assert len(_collect_files(A())) == 1


def test_collect_files_single_file_path(tmp_path):
    f = write_strategy(tmp_path, "only")

    class A:
        file = f
        dir = None
        pattern = "*.yaml"
    files = _collect_files(A())
    assert files == [f]


def test_collect_files_returns_empty_for_missing_dir(tmp_path):
    class A:
        file = None
        dir = tmp_path / "nope"
        pattern = "*.yaml"
    assert _collect_files(A()) == []


def test_collect_files_returns_empty_for_missing_file(tmp_path):
    class A:
        file = tmp_path / "nope.yaml"
        dir = None
        pattern = "*.yaml"
    assert _collect_files(A()) == []


# ================================================================== #
# _cmd_validate
# ================================================================== #
def test_validate_returns_zero_when_all_ok(tmp_path, capsys):
    f = write_strategy(tmp_path, "good_one")
    rc = _cmd_validate([f])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "good_one" in out


def test_validate_returns_one_on_dsl_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmarket: crypto\nsymbol: BTC\ntimeframe: BAD_TF\n")
    rc = _cmd_validate([bad])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out


def test_validate_returns_two_for_empty_input(capsys):
    rc = _cmd_validate([])
    assert rc == 2


def test_validate_continues_after_failure(tmp_path, capsys):
    """One FAIL doesn't stop others from being checked."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nmarket: crypto\nsymbol: BTC\ntimeframe: BAD\n")
    good = write_strategy(tmp_path, "good_after")

    rc = _cmd_validate([bad, good])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "OK" in out
    assert "1 OK / 1 FAIL" in out


# ================================================================== #
# _cmd_load — happy path
# ================================================================== #
def test_load_upserts_new_strategies(tmp_path, capsys):
    files = [
        write_strategy(tmp_path, "alpha"),
        write_strategy(tmp_path, "beta"),
    ]
    reg = InMemoryStrategyRegistry()
    rc = _cmd_load(files, reg, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert reg.get("alpha").id == "alpha"
    assert reg.get("beta").id == "beta"
    assert "2 new" in out


def test_load_marks_existing_as_updated(tmp_path, capsys):
    f = write_strategy(tmp_path, "alpha")
    reg = InMemoryStrategyRegistry()
    reg.upsert(f.read_text())
    rc = _cmd_load([f], reg, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "UPD" in out
    assert "1 updated" in out


def test_load_distinguishes_new_vs_updated_in_mixed_batch(tmp_path, capsys):
    a = write_strategy(tmp_path, "alpha")
    b = write_strategy(tmp_path, "beta")
    reg = InMemoryStrategyRegistry()
    reg.upsert(a.read_text())   # alpha pre-existing

    rc = _cmd_load([a, b], reg, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 new" in out
    assert "1 updated" in out


# ================================================================== #
# _cmd_load — dry-run
# ================================================================== #
def test_load_dry_run_does_not_modify_registry(tmp_path, capsys):
    files = [write_strategy(tmp_path, "ghost")]
    reg = InMemoryStrategyRegistry()
    rc = _cmd_load(files, reg, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY" in out
    assert reg.list_all() == []


# ================================================================== #
# _cmd_load — atomicity (parse failure aborts batch)
# ================================================================== #
def test_load_aborts_batch_when_any_file_fails_validation(tmp_path, capsys):
    """All-or-nothing: if any file fails parse, DB is not touched at all.
    Prevents half-loaded state where some strategies are upserted and
    others aren't."""
    good = write_strategy(tmp_path, "good")
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\ntimeframe: BAD_TF\n")   # missing required + bad TF

    reg = InMemoryStrategyRegistry()
    rc = _cmd_load([bad, good], reg, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Registry not modified" in out
    assert reg.list_all() == []   # critical: nothing written


# ================================================================== #
# _cmd_load — upsert error doesn't abort surviving rows
# ================================================================== #
def test_load_continues_after_individual_upsert_failure(tmp_path, capsys):
    """If a single file's upsert raises (e.g. DB blip on row N), keep
    going on the rest. Per-file failure surfaces in summary."""
    files = [
        write_strategy(tmp_path, "alpha"),
        write_strategy(tmp_path, "beta"),
    ]

    class FlakyRegistry:
        def __init__(self):
            self.calls = 0
            self.upserted: list[str] = []
        def get(self, sid):
            from strategy_engine.registry import StrategyNotFound
            raise StrategyNotFound(sid)
        def upsert(self, text):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient blip")
            from strategy_engine.dsl import load_strategy_str
            self.upserted.append(load_strategy_str(text).id)

    reg = FlakyRegistry()
    rc = _cmd_load(files, reg, dry_run=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "1 new" in out
    assert "1 failed" in out
    assert reg.upserted == ["beta"]


def test_load_returns_two_for_empty_input(capsys):
    reg = InMemoryStrategyRegistry()
    assert _cmd_load([], reg, dry_run=False) == 2
