"""Tests for smart_money/cli/inject_signal.py — R81.

Exercises the safety gating + arg validation. The actual signal
synthesis + simulator call requires extensive mocking; we focus on
the CLI gates that prevent accidental misuse.
"""
from __future__ import annotations

import pytest

from smart_money.cli.inject_signal import _build_parser, main


# =================================================================== #
# Parser
# =================================================================== #
def test_parser_requires_wallet_symbol_side_size_price():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--wallet", "0x" + "a" * 40])
    with pytest.raises(SystemExit):
        parser.parse_args(["--wallet", "0x" + "a" * 40, "--symbol", "BTC"])


def test_parser_validates_side_choices():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--wallet", "0x" + "a" * 40,
            "--symbol", "BTC", "--side", "invalid",
            "--size", "0.1", "--price", "50000",
        ])


def test_parser_accepts_long_and_short():
    parser = _build_parser()
    a = parser.parse_args([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert a.side == "long"
    a = parser.parse_args([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "short",
        "--size", "0.1", "--price", "50000",
    ])
    assert a.side == "short"


def test_parser_execute_flag_defaults_false():
    parser = _build_parser()
    a = parser.parse_args([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert a.execute is False


# =================================================================== #
# main() safety gates
# =================================================================== #
def test_main_disabled_by_default(monkeypatch, capsys):
    monkeypatch.delenv("SHADOW_INJECT_ENABLED", raising=False)
    rc = main([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "DISABLED" in out
    assert "SHADOW_INJECT_ENABLED" in out


def test_main_disabled_when_env_zero(monkeypatch, capsys):
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "0")
    rc = main([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 1


def test_main_rejects_invalid_wallet_address(monkeypatch, capsys):
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "1")
    rc = main([
        "--wallet", "not-a-valid-address",
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 2
    out = capsys.readouterr().out
    assert "Invalid wallet" in out


def test_main_rejects_short_wallet_address(monkeypatch, capsys):
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "1")
    rc = main([
        "--wallet", "0x123",   # too short
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 2


def test_main_rejects_unknown_symbol(monkeypatch, capsys, tmp_path):
    """When symbol not in symbol_map.yaml, exit 1 with helpful message."""
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "1")
    # Point to an empty mapper file
    empty_map = tmp_path / "empty_symbol_map.yaml"
    empty_map.write_text("# empty\n")
    monkeypatch.setenv("SYMBOL_MAP_PATH", str(empty_map))
    rc = main([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "NEVERHEARDOFTHIS",
        "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "not in mapper" in out


def test_main_dry_run_does_not_call_simulator(monkeypatch, capsys, tmp_path):
    """Without --execute, must only synthesize + log (no store write)."""
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "1")
    # Use real default symbol_map (HYPE added in R80, BTC always present)
    rc = main([
        "--wallet", "0x" + "a" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.1", "--price", "50000",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Synthesized FollowOrder" in out
    assert "DRY RUN" in out


def test_main_dry_run_synthesizes_correct_side(monkeypatch, capsys):
    """Long → side=buy + symbol mapper lookup."""
    monkeypatch.setenv("SHADOW_INJECT_ENABLED", "1")
    rc = main([
        "--wallet", "0x" + "b" * 40,
        "--symbol", "BTC", "--side", "long",
        "--size", "0.05", "--price", "60000",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "side=buy" in out
    assert "BTC/USDT:USDT" in out
    assert "size_coin=0.05" in out
    # 0.05 × 60000 = 3000 notional
    assert "$3000" in out or "3000.00" in out
