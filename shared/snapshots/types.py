"""Snapshot dataclass — pure data, no IO.

Mirrors `backtest_snapshots` migration 017 columns. Builder + writer
modules consume this; serialisation lives here for one-stop maintenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class BacktestSnapshot:
    """One backtest run, captured for replay.

    `id` is set by the writer on insert (None until then).

    Discipline:
      - `kind` is a stable taxonomy. Don't free-form here. Examples:
        'smart_money_p3_gate', 'strategy:btc_kronos_v1',
        'kronos_finetune_eval'.
      - `config` should serialise to deterministic JSON (sorted keys,
        ISO timestamps). The builder helps with this; callers should
        avoid passing live datetime objects without converting.
      - `report` shape varies by `kind` — consumers must dispatch on
        kind before reading fields. Document the per-kind shape in the
        builder.
    """

    kind: str
    config: dict[str, Any]
    report: dict[str, Any]
    git_commit: str | None = None
    git_dirty: bool | None = None
    cutoffs: list[str] | None = None              # ISO date strings
    data_window: dict[str, str] | None = None     # {"from": "...", "to": "..."}
    rng_seed: int | None = None
    decision_pass: bool | None = None
    decision_reason: str | None = None
    n_trades: int | None = None
    median_pnl_pct: float | None = None
    max_drawdown: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int | None = None

    def to_row(self) -> dict[str, Any]:
        """Serialise for DB insert."""
        return {
            "kind": self.kind,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "config": dict(self.config),
            "cutoffs": list(self.cutoffs) if self.cutoffs is not None else None,
            "data_window": dict(self.data_window) if self.data_window else None,
            "rng_seed": self.rng_seed,
            "report": dict(self.report),
            "decision_pass": self.decision_pass,
            "decision_reason": self.decision_reason,
            "n_trades": self.n_trades,
            "median_pnl_pct": self.median_pnl_pct,
            "max_drawdown": self.max_drawdown,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
        }


__all__ = ["BacktestSnapshot"]
