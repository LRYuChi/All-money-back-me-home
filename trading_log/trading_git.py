"""Trading-as-Git: immutable commit tracking for every trade decision (inspired by OpenAlice)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TradeOperation:
    """A single trade operation within a commit."""

    action: str  # "open_long", "open_short", "close_long", "close_short"
    symbol: str
    amount: float
    price: float | None = None
    leverage: float = 1.0
    result: str = "pending"  # "filled", "rejected", "failed"
    execution_price: float | None = None


@dataclass
class AccountSnapshot:
    """Account state snapshot taken after commit execution."""

    balance: float
    equity: float
    open_positions: dict[str, Any] = field(default_factory=dict)
    unrealized_pnl: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class TradeCommit:
    """An immutable trade commit — analogous to a git commit."""

    hash: str
    message: str
    strategy: str
    operations: list[TradeOperation]
    snapshot: AccountSnapshot
    timestamp: float
    parent_hash: str | None = None


def _compute_hash(data: dict) -> str:
    """Compute an 8-char hash for the commit."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def safe_json_write(path: Path, data: Any) -> None:
    """Atomic JSON write: write to temp file, then os.replace (from trump-code pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def append_event(events_path: Path, event: dict) -> None:
    """Append an event to the JSONL event log."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "a") as f:
        f.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")


class TradingGit:
    """Git-like commit tracking for trades."""

    def __init__(self, base_dir: Path):
        self.commits_dir = base_dir / "commits"
        self.snapshots_dir = base_dir / "snapshots"
        self.events_path = base_dir / "events.jsonl"
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._last_hash: str | None = None

    def commit(
        self,
        message: str,
        strategy: str,
        operations: list[TradeOperation],
        snapshot: AccountSnapshot,
    ) -> TradeCommit:
        """Create and persist a new trade commit."""
        now = time.time()
        commit_data = {
            "message": message,
            "strategy": strategy,
            "operations": [asdict(op) for op in operations],
            "timestamp": now,
            "parent": self._last_hash,
        }

        commit_hash = _compute_hash(commit_data)

        trade_commit = TradeCommit(
            hash=commit_hash,
            message=message,
            strategy=strategy,
            operations=operations,
            snapshot=snapshot,
            timestamp=now,
            parent_hash=self._last_hash,
        )

        # Persist commit
        safe_json_write(
            self.commits_dir / f"{commit_hash}.json",
            asdict(trade_commit),
        )

        # Persist snapshot
        safe_json_write(
            self.snapshots_dir / f"{commit_hash}.json",
            asdict(snapshot),
        )

        # Append to event log
        append_event(self.events_path, {
            "type": "trade.commit",
            "hash": commit_hash,
            "message": message,
            "strategy": strategy,
            "timestamp": now,
        })

        self._last_hash = commit_hash
        return trade_commit

    def log(self, limit: int = 20) -> list[dict]:
        """Read recent commits (newest first)."""
        commits = []
        for f in sorted(self.commits_dir.glob("*.json"), key=os.path.getmtime, reverse=True):
            with open(f) as fp:
                commits.append(json.load(fp))
            if len(commits) >= limit:
                break
        return commits

    def show(self, commit_hash: str) -> dict | None:
        """Show a specific commit by hash."""
        path = self.commits_dir / f"{commit_hash}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None
