"""Backtest snapshots — full reproducibility for any historical run.

Every backtest (P3 gate / strategy DSL evaluation / Kronos finetune
evaluation) writes a `BacktestSnapshot` to `backtest_snapshots`. Six
months later, given just the row id, you can reconstruct exactly what
code/config/data window produced the result.

API:
    from shared.snapshots import (
        BacktestSnapshot, build_snapshot, write_snapshot, build_writer,
    )

Designed to be **always-optional**: if no writer is configured the
snapshot is logged at INFO and dropped — backtest jobs never fail because
snapshot persistence isn't available.
"""

from shared.snapshots.types import BacktestSnapshot
from shared.snapshots.writer import (
    BacktestSnapshotWriter,
    InMemorySnapshotWriter,
    NoOpSnapshotWriter,
    SupabaseSnapshotWriter,
    PostgresSnapshotWriter,
    build_writer,
    record_safe,
)
from shared.snapshots.builder import build_snapshot, current_git_commit

__all__ = [
    "BacktestSnapshot",
    "BacktestSnapshotWriter",
    "InMemorySnapshotWriter",
    "NoOpSnapshotWriter",
    "SupabaseSnapshotWriter",
    "PostgresSnapshotWriter",
    "build_writer",
    "record_safe",
    "build_snapshot",
    "current_git_commit",
]
