"""StrategyRuntime — bridges live signal streams to strategy intents.

Designed to be plugged into any signal-producing daemon (currently the
smart_money shadow daemon; later the multi-market daemon). Pure async-
friendly: no internal threads, no subscriptions, just a thread-safe
buffer + an explicit `evaluate_all()` tick.

Daemon responsibilities (caller):
  - Call `runtime.ingest(signal)` for each new UniversalSignal
  - Call `runtime.evaluate_all()` on a tick (e.g. every 30s) — returns
    fired intents (and invokes `on_intent` callback per fire for I/O)
  - Provide `regime_provider` + `capital_provider` callbacks so the
    runtime stays free of L1 data-layer dependencies

Buffer policy:
  - Keyed by (symbol, horizon) since fuser groups by both
  - Expired signals (`is_expired`) trimmed on each ingest + each evaluate
  - No per-source dedup: caller's responsibility (signals from different
    sources at the same bar = legitimate parallel signals to fuse)

Intent dispatch:
  - `on_intent(intent)` is invoked SYNCHRONOUSLY per fire — failures
    are logged but never propagated, so a failing pending_orders write
    can't poison the strategy loop.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from fusion import Regime, SignalFuser
from shared.signals.types import StrategyIntent, UniversalSignal
from strategy_engine.evaluator import evaluate
from strategy_engine.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# Type aliases for callbacks
RegimeProvider = Callable[[], Regime]
CapitalProvider = Callable[[], float]
IntentCallback = Callable[[StrategyIntent], None]


class StrategyRuntime:
    """In-process strategy evaluator. Thread-safe ingest, single-threaded eval."""

    def __init__(
        self,
        registry: StrategyRegistry,
        fuser: SignalFuser,
        regime_provider: RegimeProvider,
        capital_provider: CapitalProvider,
        on_intent: IntentCallback | None = None,
    ) -> None:
        self._registry = registry
        self._fuser = fuser
        self._regime_provider = regime_provider
        self._capital_provider = capital_provider
        self._on_intent = on_intent or _log_intent

        # Per-(symbol, horizon) signal buffer
        self._buffers: dict[tuple[str, str], list[UniversalSignal]] = defaultdict(list)
        self._lock = threading.Lock()

        # Stats counters (reset on `reset_stats()`)
        self._stats = {
            "ingested": 0,
            "expired_dropped": 0,
            "ticks": 0,
            "intents_fired": 0,
            "intent_callback_errors": 0,
        }

    # ---------------------------------------------------------------- #
    # Producer API
    # ---------------------------------------------------------------- #
    def ingest(self, signal: UniversalSignal) -> None:
        """Add a signal to its (symbol, horizon) bucket; thread-safe.

        Callers can call this from any thread (including non-async) — the
        actual evaluation happens later on `evaluate_all()`.
        """
        with self._lock:
            key = (signal.symbol, signal.horizon)
            self._buffers[key].append(signal)
            self._stats["ingested"] += 1
            # Opportunistic trim — keep buffer from growing unbounded
            self._trim_expired_locked(key)

    # ---------------------------------------------------------------- #
    # Consumer API
    # ---------------------------------------------------------------- #
    def evaluate_all(self) -> list[StrategyIntent]:
        """Run one evaluation tick. For each non-empty (symbol, horizon)
        bucket: fuse, then evaluate every matching enabled strategy.

        Returns the list of fired intents (also dispatched via on_intent
        as a side effect).
        """
        self._stats["ticks"] += 1
        regime = self._regime_provider()
        capital = self._capital_provider()
        active = self._registry.list_active()

        # Snapshot buffers under lock to keep eval lock-free
        with self._lock:
            for k in list(self._buffers.keys()):
                self._trim_expired_locked(k)
            snapshot = {k: list(v) for k, v in self._buffers.items() if v}

        intents: list[StrategyIntent] = []
        for (symbol, horizon), signals in snapshot.items():
            fused = self._fuser.fuse(signals, regime, symbol=symbol, horizon=horizon)

            for rec in active:
                strat = rec.parsed
                if strat.symbol != symbol or strat.timeframe != horizon:
                    continue
                ctx = self._build_context(fused, regime, capital)
                intent = evaluate(strat, ctx, fused_signal=fused)
                if intent is None:
                    continue

                intents.append(intent)
                self._stats["intents_fired"] += 1
                try:
                    self._on_intent(intent)
                except Exception as e:
                    self._stats["intent_callback_errors"] += 1
                    logger.warning(
                        "on_intent callback raised for %s: %s",
                        intent.strategy_id, e,
                    )

        return intents

    # ---------------------------------------------------------------- #
    # Introspection
    # ---------------------------------------------------------------- #
    def buffer_size(self, symbol: str, horizon: str) -> int:
        with self._lock:
            return len(self._buffers.get((symbol, horizon), []))

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        for k in self._stats:
            self._stats[k] = 0

    # ---------------------------------------------------------------- #
    # Helpers
    # ---------------------------------------------------------------- #
    def _trim_expired_locked(self, key: tuple[str, str]) -> None:
        """Caller must hold self._lock."""
        bucket = self._buffers.get(key)
        if not bucket:
            return
        before = len(bucket)
        # is_expired uses datetime.now(); fine for both ingest + eval paths
        kept = [s for s in bucket if not s.is_expired]
        self._buffers[key] = kept
        self._stats["expired_dropped"] += before - len(kept)

    def _build_context(
        self,
        fused,
        regime: Regime,
        capital: float,
    ) -> dict:
        """Construct strategy DSL context from fused signal + market state."""
        return {
            "fused": {
                "direction": fused.direction.value,
                "ensemble_score": fused.ensemble_score,
                "sources_count": fused.sources_count,
                "contributions": fused.contributions,
                "conflict": fused.conflict,
            },
            "regime": regime.value,
            "capital": capital,
        }


def _log_intent(intent: StrategyIntent) -> None:
    """Default on_intent: just log. Daemon overrides with pending_orders writer."""
    logger.info(
        "INTENT %s %s %s notional=%.2f sl=%s",
        intent.strategy_id, intent.symbol, intent.direction.value,
        intent.target_notional_usd, intent.stop_loss_pct,
    )


__all__ = [
    "StrategyRuntime",
    "RegimeProvider",
    "CapitalProvider",
    "IntentCallback",
]
