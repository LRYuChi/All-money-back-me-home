"""Tests for DispatcherRegistry + NotifyOnlyDispatcher (round 24)."""
from __future__ import annotations

import pytest

from execution.pending_orders import (
    DispatcherRegistry,
    InMemoryPendingOrderQueue,
    LogOnlyDispatcher,
    NotifyOnlyDispatcher,
    PendingOrder,
    PendingOrderStatus,
    PendingOrderWorker,
    UnsupportedModeError,
    build_default_registry,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(mode="shadow", strategy="s1", notional=500.0) -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy,
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode=mode,
    )


class _CapturingNotifier:
    def __init__(self, *, ok: bool = True, raise_on_send: Exception | None = None):
        self.ok = ok
        self.raise_on_send = raise_on_send
        self.messages: list = []

    def send(self, message):
        self.messages.append(message)
        if self.raise_on_send:
            raise self.raise_on_send
        return self.ok


# ================================================================== #
# DispatcherRegistry.register / build / has / modes
# ================================================================== #
def test_register_and_build_returns_dispatcher_for_mode():
    reg = DispatcherRegistry()
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    d = reg.build("shadow")
    assert d.mode == "shadow"


def test_build_unknown_mode_raises_unsupported():
    reg = DispatcherRegistry()
    with pytest.raises(UnsupportedModeError) as ex:
        reg.build("live")
    assert ex.value.mode == "live"
    assert ex.value.available == []


def test_register_same_mode_twice_raises_unless_replace():
    reg = DispatcherRegistry()
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    with pytest.raises(ValueError, match="already registered"):
        reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))


def test_register_with_replace_overrides():
    reg = DispatcherRegistry()
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    sentinel = LogOnlyDispatcher("shadow")
    reg.register("shadow", lambda mode: sentinel, replace=True)
    assert reg.build("shadow") is sentinel


def test_unregister_removes_factory():
    reg = DispatcherRegistry()
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    reg.unregister("shadow")
    assert not reg.has("shadow")
    with pytest.raises(UnsupportedModeError):
        reg.build("shadow")


def test_unregister_unknown_mode_is_noop():
    reg = DispatcherRegistry()
    reg.unregister("live")  # must not raise


def test_has_returns_true_only_after_register():
    reg = DispatcherRegistry()
    assert not reg.has("shadow")
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    assert reg.has("shadow")


def test_modes_returns_registered_modes():
    reg = DispatcherRegistry()
    reg.register("shadow", lambda mode: LogOnlyDispatcher(mode))
    reg.register("paper", lambda mode: LogOnlyDispatcher(mode))
    assert sorted(reg.modes()) == ["paper", "shadow"]


def test_build_warns_when_dispatcher_mode_mismatches(caplog):
    """Sanity check: a factory returning a dispatcher whose .mode disagrees
    with the requested mode logs a warning (catches misconfiguration)."""
    reg = DispatcherRegistry()
    # Factory deliberately ignores `mode` param and returns a "live"-tagged
    # dispatcher even when shadow was requested.
    reg.register("shadow", lambda mode: LogOnlyDispatcher("live"))
    import logging
    with caplog.at_level(logging.WARNING):
        reg.build("shadow")
    assert any("reports mode=live" in m for m in caplog.messages)


# ================================================================== #
# NotifyOnlyDispatcher
# ================================================================== #
def test_notify_only_marks_filled():
    notifier = _CapturingNotifier()
    d = NotifyOnlyDispatcher("notify", notifier=notifier)
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED


def test_notify_only_sends_message_with_strategy_and_symbol():
    notifier = _CapturingNotifier()
    d = NotifyOnlyDispatcher("notify", notifier=notifier)
    d.dispatch(make_order(mode="notify", strategy="alpha"))
    assert len(notifier.messages) == 1
    msg = notifier.messages[0]
    assert "alpha" in msg.title
    assert "long" in msg.title
    assert "crypto:OKX:BTC/USDT:USDT" in msg.title
    assert "notional=$500.00" in msg.body


def test_notify_only_passes_structured_data():
    notifier = _CapturingNotifier()
    d = NotifyOnlyDispatcher("notify", notifier=notifier)
    d.dispatch(make_order(mode="notify", strategy="alpha", notional=750.0))
    msg = notifier.messages[0]
    assert msg.data["strategy_id"] == "alpha"
    assert msg.data["notional_usd"] == 750.0
    assert msg.data["side"] == "long"


def test_notify_only_records_send_status_in_detail():
    notifier = _CapturingNotifier(ok=True)
    d = NotifyOnlyDispatcher("notify", notifier=notifier)
    res = d.dispatch(make_order(mode="notify"))
    assert res.detail["notifier_sent"] is True

    notifier_fail = _CapturingNotifier(ok=False)
    d2 = NotifyOnlyDispatcher("notify", notifier=notifier_fail)
    res2 = d2.dispatch(make_order(mode="notify"))
    assert res2.detail["notifier_sent"] is False


def test_notify_only_swallows_notifier_exception():
    """A raising notifier still produces a FILLED order — best-effort delivery."""
    notifier = _CapturingNotifier(raise_on_send=ConnectionError("network down"))
    d = NotifyOnlyDispatcher("notify", notifier=notifier)
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED
    assert res.detail["notifier_sent"] is False


def test_notify_only_with_no_notifier_still_fills():
    """No notifier configured → log only. Order still completes."""
    d = NotifyOnlyDispatcher("notify", notifier=None)
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED
    assert res.detail["notifier_sent"] is False


def test_notify_only_uses_custom_title_prefix():
    notifier = _CapturingNotifier()
    d = NotifyOnlyDispatcher(
        "notify", notifier=notifier, title_prefix="[smart-money]",
    )
    d.dispatch(make_order(mode="notify"))
    assert notifier.messages[0].title.startswith("[smart-money]")


# ================================================================== #
# build_default_registry
# ================================================================== #
def test_default_registry_supports_shadow_paper_notify():
    reg = build_default_registry(settings=None)
    assert reg.has("shadow")
    assert reg.has("paper")
    assert reg.has("notify")


def test_default_registry_does_not_register_live():
    """live MUST NOT be silently bridged to LogOnly — that would write
    FILLED rows for trades that never happened. Phase F.1 adds it."""
    reg = build_default_registry(settings=None)
    assert not reg.has("live")
    with pytest.raises(UnsupportedModeError):
        reg.build("live")


def test_default_registry_shadow_builds_log_only():
    reg = build_default_registry(settings=None)
    d = reg.build("shadow")
    assert isinstance(d, LogOnlyDispatcher)
    assert d.mode == "shadow"


def test_default_registry_paper_builds_log_only_for_now():
    """Phase F.1.5 will swap paper to a real paper trader; until then it
    shares LogOnly."""
    reg = build_default_registry(settings=None)
    d = reg.build("paper")
    assert isinstance(d, LogOnlyDispatcher)
    assert d.mode == "paper"


def test_default_registry_notify_builds_notify_only():
    reg = build_default_registry(settings=None)
    d = reg.build("notify")
    assert isinstance(d, NotifyOnlyDispatcher)
    assert d.mode == "notify"


def test_default_registry_notify_dispatcher_works_end_to_end():
    """notify-mode dispatcher with NoOp notifier still marks orders FILLED."""
    reg = build_default_registry(settings=None)
    d = reg.build("notify")
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED


def test_default_registry_with_settings_none_falls_back_to_noop_notifier():
    """settings=None path: no shared.notifier needed for tests."""
    reg = build_default_registry(settings=None)
    d = reg.build("notify")
    # Should not raise
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED


def test_default_registry_with_broken_settings_falls_back_to_noop():
    """If build_notifier blows up, registry survives with NoOp."""
    class BrokenSettings:
        # Property accesses raise — exercises the broad except
        @property
        def telegram_bot_token(self):
            raise RuntimeError("config corrupt")

    reg = build_default_registry(settings=BrokenSettings())
    d = reg.build("notify")
    res = d.dispatch(make_order(mode="notify"))
    assert res.terminal_status == PendingOrderStatus.FILLED


# ================================================================== #
# Worker integration with registry-built dispatcher
# ================================================================== #
def test_worker_runs_with_registry_built_shadow_dispatcher():
    reg = build_default_registry(settings=None)
    q = InMemoryPendingOrderQueue()
    o = make_order(mode="shadow")
    q.enqueue(o)

    w = PendingOrderWorker(q, reg.build("shadow"))
    w.process_one()
    assert q.get(o.id).status == PendingOrderStatus.FILLED


def test_worker_runs_with_registry_built_notify_dispatcher():
    notifier = _CapturingNotifier()
    reg = DispatcherRegistry()
    reg.register("notify", lambda mode: NotifyOnlyDispatcher(mode, notifier=notifier))

    q = InMemoryPendingOrderQueue()
    o = make_order(mode="notify")
    q.enqueue(o)

    w = PendingOrderWorker(q, reg.build("notify"))
    w.process_one()
    assert q.get(o.id).status == PendingOrderStatus.FILLED
    assert len(notifier.messages) == 1
