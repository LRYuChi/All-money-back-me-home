"""DispatcherRegistry — map ExecutionMode → Dispatcher factory.

Phase F shipped a single hard-coded `LogOnlyDispatcher`. Phase F.1 will
introduce concrete exchange dispatchers (OKX live, IBKR, TW broker) per
mode. This registry is the seam between the worker (which only knows
"give me the dispatcher for `--mode live`") and the concrete classes
(which can register themselves at import time).

Design:
  - `DispatcherRegistry().register(mode, factory)` adds a factory
  - `registry.build(mode)` returns a `Dispatcher` instance
  - `build_default_registry(settings)` wires the standard mapping:
        shadow  → LogOnlyDispatcher
        paper   → LogOnlyDispatcher    (placeholder until F.1.5)
        notify  → NotifyOnlyDispatcher (push Telegram via shared.notifier)
        live    → not registered (raises until F.1 OKXLiveDispatcher lands)

Factories are `Callable[[ExecutionMode], Dispatcher]` so they can capture
shared state (a notifier, an exchange client, a credentials store) once
at construction time. The registry doesn't own those — it just remembers
how to build a dispatcher when asked.

Why a factory instead of a pre-built instance?
  - Some dispatchers (live exchange) want lazy connection — don't open
    sockets until a worker actually needs them
  - Lets a single registry serve multiple workers, each binding its own
    state (e.g. mode tag in `LogOnlyDispatcher.mode`)
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from execution.pending_orders.types import ExecutionMode
from execution.pending_orders.worker import (
    Dispatcher,
    LogOnlyDispatcher,
)

logger = logging.getLogger(__name__)


DispatcherFactory = Callable[[ExecutionMode], Dispatcher]


class UnsupportedModeError(LookupError):
    """Raised when build() is called for a mode with no registered factory."""

    def __init__(self, mode: ExecutionMode, available: list[ExecutionMode]):
        super().__init__(
            f"no dispatcher registered for mode={mode!r}; "
            f"available modes: {sorted(available)}"
        )
        self.mode = mode
        self.available = available


class DispatcherRegistry:
    """Mode → factory map. Single instance per process is the intended
    use; callers can pass it to multiple workers (each binds to its own
    mode at construction time)."""

    def __init__(self) -> None:
        self._factories: dict[ExecutionMode, DispatcherFactory] = {}

    def register(
        self,
        mode: ExecutionMode,
        factory: DispatcherFactory,
        *,
        replace: bool = False,
    ) -> None:
        """Register `factory` for `mode`.

        Idempotent guard: re-registering the same mode raises unless
        `replace=True` — protects against accidental double-registration
        from Phase F.1 plugin imports overriding shadow defaults.
        """
        if mode in self._factories and not replace:
            raise ValueError(
                f"mode {mode!r} already registered; pass replace=True to override"
            )
        self._factories[mode] = factory

    def unregister(self, mode: ExecutionMode) -> None:
        """Drop a factory. No-op if missing."""
        self._factories.pop(mode, None)

    def build(self, mode: ExecutionMode) -> Dispatcher:
        try:
            factory = self._factories[mode]
        except KeyError:
            raise UnsupportedModeError(mode, self.modes()) from None
        dispatcher = factory(mode)
        # Sanity check: mode tag on the built dispatcher should match the
        # requested mode. Catches bugs where a factory accidentally hard-codes
        # the wrong mode.
        if getattr(dispatcher, "mode", mode) != mode:
            logger.warning(
                "dispatcher for requested mode=%s reports mode=%s",
                mode, dispatcher.mode,
            )
        return dispatcher

    def has(self, mode: ExecutionMode) -> bool:
        return mode in self._factories

    def modes(self) -> list[ExecutionMode]:
        return list(self._factories.keys())


# ================================================================== #
# Built-in NotifyOnlyDispatcher
# ================================================================== #
class NotifyOnlyDispatcher:
    """For `mode=notify`: push a Telegram/Discord alert via shared.notifier
    and mark the order FILLED. No exchange interaction; useful when a
    strategy is "alert me, I'll trade manually" — the order row remains
    auditable but no real money moves.

    Notifier failures are logged but never block the worker — the order
    is still marked FILLED (we tried; ops can grep the worker log if a
    notification is missing). Caller can swap to a stricter dispatcher
    (REJECTED on notify failure) if they want hard delivery guarantees.
    """

    def __init__(
        self,
        mode: ExecutionMode = "notify",
        *,
        notifier: Any = None,    # shared.notifier.Notifier
        title_prefix: str = "[strategy]",
    ) -> None:
        self._mode = mode
        self._notifier = notifier
        self._title_prefix = title_prefix

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    def dispatch(self, order):
        # Late import to avoid hard dep on shared.notifier when notify mode
        # isn't being used.
        from execution.pending_orders.types import PendingOrderStatus
        from execution.pending_orders.worker import DispatchResult

        sent = False
        if self._notifier is not None:
            try:
                from shared.notifier import Level, Message
                msg = Message(
                    level=Level.INFO,
                    title=f"{self._title_prefix} {order.strategy_id}: "
                          f"{order.side} {order.symbol}",
                    body=(
                        f"notional=${order.target_notional_usd:.2f} "
                        f"mode={order.mode}"
                        + (f"\nentry≈{order.entry_price_ref}"
                           if order.entry_price_ref else "")
                    ),
                    tags=("notify-only", order.strategy_id, order.symbol),
                    data={
                        "order_id": order.id,
                        "strategy_id": order.strategy_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "notional_usd": order.target_notional_usd,
                    },
                )
                sent = bool(self._notifier.send(msg))
            except Exception as e:
                logger.warning(
                    "notify-only dispatch: notifier raised (%s) — order "
                    "still marked FILLED", e,
                )

        logger.info(
            "notify-only dispatch: id=%s strategy=%s notifier_sent=%s",
            order.id, order.strategy_id, sent,
        )
        return DispatchResult(
            terminal_status=PendingOrderStatus.FILLED,
            detail={
                "dispatcher": "notify_only",
                "mode": self._mode,
                "notifier_sent": sent,
            },
        )


# ================================================================== #
# Default registry factory
# ================================================================== #
def build_default_registry(settings=None) -> DispatcherRegistry:  # noqa: ANN001
    """Wire the standard four-mode mapping.

    `settings` (optional): smart_money.config.settings — used to construct
    the notifier for `notify` mode. If None, NotifyOnly uses NoOpNotifier
    (logs only).

    Round 41: `live` and `paper` now wire OKXLiveDispatcher when OKX
    credentials are available. `paper` enables ccxt sandbox/demo mode
    (writes orders to OKX demo trading); `live` hits real money. If
    credentials are absent, `live` stays unregistered (--mode live will
    exit 1 instead of running unauthenticated) and `paper` falls back to
    LogOnlyDispatcher (safe local simulation).
    """
    reg = DispatcherRegistry()

    # shadow is always local-only — never touches an exchange
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))

    # paper: try OKX demo first, fall back to LogOnly if no creds
    paper_dispatcher = _try_build_okx(settings, mode="paper")
    if paper_dispatcher is not None:
        reg.register("paper", lambda mode: paper_dispatcher)
    else:
        reg.register("paper", lambda mode: LogOnlyDispatcher(mode))

    # live: OKX real money. Only registered when creds present so that
    # --mode live without credentials exits 1 (vs silently logging FILLED).
    live_dispatcher = _try_build_okx(settings, mode="live")
    if live_dispatcher is not None:
        reg.register("live", lambda mode: live_dispatcher)

    # notify uses the shared notifier. Build once, share across worker calls.
    notifier = _build_notifier_for_notify(settings)
    reg.register(
        "notify",
        lambda mode: NotifyOnlyDispatcher(mode, notifier=notifier),
    )

    return reg


def _try_build_okx(settings, *, mode):  # noqa: ANN001
    """Return an OKX dispatcher when credentials are wired, None otherwise.
    Late-imports build_okx_dispatcher so the registry has no hard ccxt
    dependency — environments without ccxt still get shadow + notify."""
    if settings is None:
        return None
    try:
        from execution.exchanges.okx import build_okx_dispatcher
        return build_okx_dispatcher(settings, mode=mode)
    except Exception as e:
        logger.warning(
            "registry: build_okx_dispatcher(mode=%s) failed (%s) — "
            "skipping OKX registration", mode, e,
        )
        return None


def _build_notifier_for_notify(settings):  # noqa: ANN001
    """Try to build a real notifier; fall back to NoOp on import errors
    or missing config so the registry never crashes."""
    if settings is None:
        try:
            from shared.notifier import NoOpNotifier
            return NoOpNotifier()
        except ImportError:
            return None
    try:
        from shared.notifier import build_notifier
        return build_notifier(settings)
    except Exception as e:
        logger.warning(
            "dispatcher registry: notify-mode notifier build failed (%s) — "
            "using NoOp", e,
        )
        try:
            from shared.notifier import NoOpNotifier
            return NoOpNotifier()
        except ImportError:
            return None


__all__ = [
    "DispatcherFactory",
    "DispatcherRegistry",
    "NotifyOnlyDispatcher",
    "UnsupportedModeError",
    "build_default_registry",
]
