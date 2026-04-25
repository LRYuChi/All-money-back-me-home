"""Tests for R72 — shadow daemon cold-start warmup.

Covers:
  - _parse_clearinghouse_positions parsing of HL response shapes
  - _warmup_wallet_positions iteration + error tolerance
  - WalletPosition fields populated correctly (side from szi sign, etc)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from smart_money.cli.shadow import (
    _parse_clearinghouse_positions,
    _warmup_wallet_positions,
)
from smart_money.store.schema import WalletPosition


def _now():
    return datetime.now(timezone.utc)


def _entry(address: str = "0x" + "a" * 40, wid=None, score: float = 1.0):
    """Mimic WhitelistEntry shape with the attrs warmup uses."""
    e = MagicMock()
    e.address = address
    e.wallet_id = wid or uuid4()
    e.score = score
    e.is_tradeable = True
    return e


# =================================================================== #
# _parse_clearinghouse_positions
# =================================================================== #
def test_parse_returns_empty_for_non_dict_input():
    wid = uuid4()
    assert _parse_clearinghouse_positions(None, wid, _now()) == []
    assert _parse_clearinghouse_positions("not a dict", wid, _now()) == []


def test_parse_returns_empty_when_no_asset_positions():
    wid = uuid4()
    assert _parse_clearinghouse_positions({}, wid, _now()) == []
    assert _parse_clearinghouse_positions(
        {"assetPositions": []}, wid, _now(),
    ) == []


def test_parse_extracts_long_position():
    wid = uuid4()
    state = {
        "assetPositions": [
            {"type": "oneWay",
             "position": {"coin": "BTC", "szi": "0.5", "entryPx": "50000.0"}},
        ],
    }
    out = _parse_clearinghouse_positions(state, wid, _now())
    assert len(out) == 1
    p = out[0]
    assert p.symbol == "BTC"
    assert p.side == "long"
    assert p.size == 0.5
    assert p.avg_entry_px == 50000.0
    assert p.wallet_id == wid


def test_parse_extracts_short_position_from_negative_szi():
    wid = uuid4()
    state = {
        "assetPositions": [
            {"position": {"coin": "ETH", "szi": "-2.5", "entryPx": "3000"}},
        ],
    }
    out = _parse_clearinghouse_positions(state, wid, _now())
    assert len(out) == 1
    assert out[0].side == "short"
    assert out[0].size == 2.5   # absolute value


def test_parse_skips_flat_positions():
    """szi exactly 0 → not a real position; HL usually omits these."""
    wid = uuid4()
    state = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0", "entryPx": "50000"}},
            {"position": {"coin": "ETH", "szi": "1.0", "entryPx": "3000"}},
        ],
    }
    out = _parse_clearinghouse_positions(state, wid, _now())
    assert len(out) == 1
    assert out[0].symbol == "ETH"


def test_parse_skips_malformed_entries():
    wid = uuid4()
    state = {
        "assetPositions": [
            "not a dict",
            {"position": "not a dict"},
            {"position": {}},                              # missing coin
            {"position": {"coin": "BTC"}},                 # missing szi
            {"position": {"coin": "X", "szi": "abc"}},     # unparseable szi
            {"position": {"coin": "OK", "szi": "1.0"}},    # valid (no entry_px)
        ],
    }
    out = _parse_clearinghouse_positions(state, wid, _now())
    assert len(out) == 1
    assert out[0].symbol == "OK"
    assert out[0].avg_entry_px is None   # tolerated when missing


def test_parse_handles_unparseable_entry_px():
    wid = uuid4()
    state = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "1", "entryPx": "garbage"}},
        ],
    }
    out = _parse_clearinghouse_positions(state, wid, _now())
    assert len(out) == 1
    assert out[0].avg_entry_px is None


# =================================================================== #
# _warmup_wallet_positions
# =================================================================== #
def test_warmup_seeds_each_wallet_position(monkeypatch):
    store = MagicMock()
    hl = MagicMock()
    wallet1, wallet2 = _entry("0xa1"), _entry("0xb2")
    hl.get_current_state.side_effect = [
        {"assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.5", "entryPx": "50000"}},
            {"position": {"coin": "ETH", "szi": "-2.0", "entryPx": "3000"}},
        ]},
        {"assetPositions": [
            {"position": {"coin": "SOL", "szi": "100", "entryPx": "150"}},
        ]},
    ]
    summary = _warmup_wallet_positions(store, hl, [wallet1, wallet2])
    assert summary["wallets_seeded"] == 2
    assert summary["positions_seeded"] == 3
    assert summary["fetch_errors"] == 0
    assert store.upsert_position.call_count == 3


def test_warmup_continues_after_single_wallet_fetch_failure():
    store = MagicMock()
    hl = MagicMock()
    bad, good = _entry("0xbad"), _entry("0xgood")
    hl.get_current_state.side_effect = [
        RuntimeError("connection refused"),
        {"assetPositions": [
            {"position": {"coin": "BTC", "szi": "1.0", "entryPx": "50000"}},
        ]},
    ]
    summary = _warmup_wallet_positions(store, hl, [bad, good])
    assert summary["fetch_errors"] == 1
    assert summary["wallets_seeded"] == 1
    assert summary["positions_seeded"] == 1
    assert store.upsert_position.call_count == 1


def test_warmup_continues_after_upsert_failure():
    """Store error on one position must not abort other inserts."""
    store = MagicMock()
    store.upsert_position.side_effect = [None, RuntimeError("db down"), None]
    hl = MagicMock()
    wallet = _entry("0xwallet")
    hl.get_current_state.return_value = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "1", "entryPx": "50000"}},
        {"position": {"coin": "ETH", "szi": "1", "entryPx": "3000"}},
        {"position": {"coin": "SOL", "szi": "1", "entryPx": "150"}},
    ]}
    summary = _warmup_wallet_positions(store, hl, [wallet])
    # 3 attempts, 2 succeed
    assert store.upsert_position.call_count == 3
    assert summary["positions_seeded"] == 2


def test_warmup_returns_zero_for_empty_whitelist():
    store = MagicMock()
    hl = MagicMock()
    summary = _warmup_wallet_positions(store, hl, [])
    assert summary["wallets_seeded"] == 0
    assert summary["positions_seeded"] == 0
    assert summary["fetch_errors"] == 0
    assert store.upsert_position.call_count == 0
    assert hl.get_current_state.call_count == 0


def test_warmup_handles_wallet_with_no_open_positions():
    """Wallet returns empty assetPositions → no skip, no insert."""
    store = MagicMock()
    hl = MagicMock()
    hl.get_current_state.return_value = {"assetPositions": []}
    summary = _warmup_wallet_positions(store, hl, [_entry("0xflat")])
    assert summary["wallets_seeded"] == 0
    assert summary["positions_seeded"] == 0
    assert summary["fetch_errors"] == 0
    # No upsert attempted (nothing to seed)
    assert store.upsert_position.call_count == 0


def test_warmup_per_wallet_summary_structure():
    store = MagicMock()
    hl = MagicMock()
    hl.get_current_state.side_effect = [
        {"assetPositions": [
            {"position": {"coin": "BTC", "szi": "1", "entryPx": "50000"}},
            {"position": {"coin": "ETH", "szi": "-1", "entryPx": "3000"}},
        ]},
        RuntimeError("timeout"),
    ]
    summary = _warmup_wallet_positions(
        store, hl, [_entry("0xa1234567890abcdef"), _entry("0xb1234567890")],
    )
    assert len(summary["per_wallet"]) == 2
    # First: 2 positions, no error
    assert summary["per_wallet"][0]["n_positions"] == 2
    assert summary["per_wallet"][0]["error"] is None
    # Second: 0 positions, error captured
    assert summary["per_wallet"][1]["n_positions"] == 0
    assert "timeout" in summary["per_wallet"][1]["error"]


def test_warmup_uses_consistent_timestamp_across_positions():
    """All positions for one wallet use the same last_updated_ts."""
    store = MagicMock()
    captured = []
    store.upsert_position.side_effect = lambda p: captured.append(p)
    hl = MagicMock()
    hl.get_current_state.return_value = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "1", "entryPx": "50000"}},
        {"position": {"coin": "ETH", "szi": "1", "entryPx": "3000"}},
    ]}
    _warmup_wallet_positions(store, hl, [_entry("0xa")])
    assert len(captured) == 2
    assert captured[0].last_updated_ts == captured[1].last_updated_ts
