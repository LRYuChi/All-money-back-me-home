"""Tests for trading-as-git commit tracking."""

import json
import tempfile
from pathlib import Path

from trading_log.trading_git import (
    AccountSnapshot,
    TradeOperation,
    TradingGit,
    safe_json_write,
)


def test_safe_json_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.json"
        data = {"key": "value", "number": 42}
        safe_json_write(path, data)

        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data


def test_trading_git_commit():
    with tempfile.TemporaryDirectory() as tmpdir:
        git = TradingGit(Path(tmpdir))

        op = TradeOperation(
            action="open_long",
            symbol="BTC/USDT:USDT",
            amount=0.01,
            price=67500.0,
            leverage=3.0,
        )
        snapshot = AccountSnapshot(balance=1000.0, equity=1000.0)

        commit = git.commit(
            message="Open long BTC: RSI oversold + uptrend",
            strategy="AdaptiveRSI",
            operations=[op],
            snapshot=snapshot,
        )

        assert len(commit.hash) == 8
        assert commit.message == "Open long BTC: RSI oversold + uptrend"
        assert commit.strategy == "AdaptiveRSI"
        assert len(commit.operations) == 1
        assert commit.parent_hash is None


def test_trading_git_chain():
    with tempfile.TemporaryDirectory() as tmpdir:
        git = TradingGit(Path(tmpdir))

        # First commit
        c1 = git.commit(
            message="Open long BTC",
            strategy="AdaptiveRSI",
            operations=[TradeOperation("open_long", "BTC/USDT:USDT", 0.01)],
            snapshot=AccountSnapshot(balance=1000, equity=1000),
        )

        # Second commit should reference first
        c2 = git.commit(
            message="Close long BTC: take profit",
            strategy="AdaptiveRSI",
            operations=[TradeOperation("close_long", "BTC/USDT:USDT", 0.01)],
            snapshot=AccountSnapshot(balance=1050, equity=1050),
        )

        assert c2.parent_hash == c1.hash


def test_trading_git_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        git = TradingGit(Path(tmpdir))

        for i in range(5):
            git.commit(
                message=f"Trade {i}",
                strategy="Test",
                operations=[TradeOperation("open_long", "ETH/USDT:USDT", 0.1)],
                snapshot=AccountSnapshot(balance=1000 + i * 10, equity=1000 + i * 10),
            )

        log = git.log(limit=3)
        assert len(log) == 3


def test_trading_git_show():
    with tempfile.TemporaryDirectory() as tmpdir:
        git = TradingGit(Path(tmpdir))

        commit = git.commit(
            message="Test commit",
            strategy="Test",
            operations=[TradeOperation("open_long", "SOL/USDT:USDT", 1.0)],
            snapshot=AccountSnapshot(balance=500, equity=500),
        )

        shown = git.show(commit.hash)
        assert shown is not None
        assert shown["hash"] == commit.hash
        assert shown["message"] == "Test commit"

        # Non-existent hash
        assert git.show("deadbeef") is None


def test_event_log_created():
    with tempfile.TemporaryDirectory() as tmpdir:
        git = TradingGit(Path(tmpdir))

        git.commit(
            message="Test",
            strategy="Test",
            operations=[TradeOperation("open_long", "BTC/USDT:USDT", 0.01)],
            snapshot=AccountSnapshot(balance=1000, equity=1000),
        )

        events_path = Path(tmpdir) / "events.jsonl"
        assert events_path.exists()

        with open(events_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        event = json.loads(lines[0])
        assert event["type"] == "trade.commit"
