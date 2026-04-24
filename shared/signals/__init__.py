"""Universal signal schema — the common language across all signal sources.

Every signal producer (Kronos, Smart Money, TA, AI LLM, Macro) emits
`UniversalSignal`. The fusion layer (L3) consumes these and produces a
`FusedSignal`. Strategy layer (L4) consumes FusedSignal and produces
`StrategyIntent` which becomes a `SizedOrder` at L5.

This package contains:
- `types.py` — dataclasses + enums
- `history.py` — persistence layer (writes to signal_history table)
"""
