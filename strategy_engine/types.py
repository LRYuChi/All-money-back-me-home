"""Strategy DSL dataclasses — pure types, no evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PositionSizingMethod = Literal["fixed_usd", "fixed_pct", "kelly"]


@dataclass(slots=True, frozen=True)
class PositionSizing:
    """How much capital to deploy per signal.

    fixed_usd  — constant USD amount per trade
    fixed_pct  — constant % of total capital
    kelly      — fractional Kelly using win_rate × avg_win/avg_loss from
                 reflection.history. kelly_fraction caps the Kelly result
                 (0.25 = quarter-Kelly, conservative).
    """

    method: PositionSizingMethod = "fixed_usd"
    fixed_usd: float | None = None       # required if method=fixed_usd
    fixed_pct: float | None = None       # required if method=fixed_pct
    kelly_fraction: float | None = None  # required if method=kelly
    max_size_usd: float | None = None    # absolute cap
    max_leverage: float = 1.0


@dataclass(slots=True, frozen=True)
class EntryRules:
    """Conjunction-of-block predicate set.

    All three blocks must pass for entry to fire:
      all_of   — every predicate must hold
      any_of   — at least one predicate must hold (skip block if empty)
      none_of  — none of these predicates may hold

    Empty blocks evaluate as PASS (vacuously true). So a rule with only
    `all_of` set behaves as expected.
    """

    all_of: tuple[str, ...] = field(default_factory=tuple)
    any_of: tuple[str, ...] = field(default_factory=tuple)
    none_of: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class ExitRules:
    """Exit triggers. Stop-loss / take-profit are pct (0.02 = 2%); set
    `take_profit=None` for signal-driven exits only. `exit_on` is a list
    of predicates evaluated each tick — any one matching closes the position.
    `time_stop_hours` closes positions older than this regardless.
    """

    stop_loss: float | None = None
    take_profit: float | None = None
    exit_on: tuple[str, ...] = field(default_factory=tuple)
    time_stop_hours: int | None = None


@dataclass(slots=True, frozen=True)
class StrategyDef:
    """Top-level strategy. Loaded from YAML, evaluated by the engine."""

    id: str
    market: str                       # 'crypto' | 'us' | 'tw' | 'fx' | ...
    symbol: str                       # canonical, e.g. 'crypto:OKX:BTC/USDT:USDT'
    timeframe: str                    # '15m' | '1h' | '4h' | '1d'
    enabled: bool = True
    mode: Literal["shadow", "paper", "live", "notify"] = "shadow"

    entry_long: EntryRules | None = None
    entry_short: EntryRules | None = None

    position_sizing: PositionSizing = field(default_factory=PositionSizing)
    exit: ExitRules = field(default_factory=ExitRules)

    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "PositionSizingMethod",
    "PositionSizing",
    "EntryRules",
    "ExitRules",
    "StrategyDef",
]
