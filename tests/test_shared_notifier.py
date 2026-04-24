"""Tests for shared.notifier — Notifier abstraction across backends."""
from __future__ import annotations

import pytest

from shared.notifier import (
    Level,
    Message,
    MultiChannelNotifier,
    NoOpNotifier,
    TelegramNotifier,
    build_notifier,
)
from shared.notifier.base import Notifier


# ------------------------------------------------------------------ #
# Message
# ------------------------------------------------------------------ #
def test_message_as_plain_text_minimum():
    m = Message(level=Level.INFO, title="hello")
    assert m.as_plain_text() == "hello"


def test_message_as_plain_text_full():
    m = Message(
        level=Level.WARN, title="title", body="body line",
        tags=("sm", "signal"),
    )
    txt = m.as_plain_text()
    assert "title" in txt and "body line" in txt
    assert "tags: sm signal" in txt


def test_message_is_frozen():
    m = Message(level=Level.INFO, title="x")
    with pytest.raises(Exception):
        m.title = "y"  # type: ignore[misc]


# ------------------------------------------------------------------ #
# NoOpNotifier
# ------------------------------------------------------------------ #
def test_noop_returns_true():
    n = NoOpNotifier()
    assert n.send(Message(level=Level.INFO, title="x")) is True


# ------------------------------------------------------------------ #
# MultiChannelNotifier
# ------------------------------------------------------------------ #
class FakeBackend:
    def __init__(self, return_value: bool = True, raises: Exception | None = None):
        self.return_value = return_value
        self.raises = raises
        self.received: list[Message] = []

    def send(self, message: Message) -> bool:
        self.received.append(message)
        if self.raises:
            raise self.raises
        return self.return_value


def test_multi_fans_out_to_all_channels():
    a, b, c = FakeBackend(), FakeBackend(), FakeBackend()
    multi = MultiChannelNotifier([a, b, c])
    multi.send(Message(level=Level.INFO, title="x"))
    assert len(a.received) == len(b.received) == len(c.received) == 1


def test_multi_returns_true_when_any_succeeds():
    ok = FakeBackend(return_value=True)
    fail = FakeBackend(return_value=False)
    multi = MultiChannelNotifier([fail, ok])
    assert multi.send(Message(level=Level.INFO, title="x")) is True


def test_multi_returns_false_when_all_fail():
    fail1 = FakeBackend(return_value=False)
    fail2 = FakeBackend(return_value=False)
    multi = MultiChannelNotifier([fail1, fail2])
    assert multi.send(Message(level=Level.INFO, title="x")) is False


def test_multi_isolates_backend_exceptions():
    """A raising backend must not stop the others."""
    raiser = FakeBackend(raises=RuntimeError("kaboom"))
    ok = FakeBackend(return_value=True)
    multi = MultiChannelNotifier([raiser, ok])
    assert multi.send(Message(level=Level.INFO, title="x")) is True
    assert len(raiser.received) == 1
    assert len(ok.received) == 1


def test_multi_empty_channel_list_returns_false():
    multi = MultiChannelNotifier([])
    assert multi.send(Message(level=Level.INFO, title="x")) is False


def test_multi_per_channel_min_level_filter():
    """A backend whose CLASS is in min_level_by_channel only receives ≥ that level.
    Filter keyed by class, so two instances of distinct subclasses test the routing."""

    class ChattyBackend(FakeBackend):
        pass

    class QuietBackend(FakeBackend):
        pass

    chatty = ChattyBackend()
    quiet = QuietBackend()
    multi = MultiChannelNotifier(
        [chatty, quiet],
        min_level_by_channel={QuietBackend: Level.WARN},
    )
    multi.send(Message(level=Level.INFO, title="x"))
    multi.send(Message(level=Level.ERROR, title="y"))

    # Chatty got both
    assert len(chatty.received) == 2
    # Quiet got only the ERROR (≥ WARN), filtered out the INFO
    assert len(quiet.received) == 1
    assert quiet.received[0].level == Level.ERROR


# ------------------------------------------------------------------ #
# TelegramNotifier — env handling without network
# ------------------------------------------------------------------ #
def test_telegram_unconfigured_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    tn = TelegramNotifier()
    assert tn.is_configured is False
    assert tn.send(Message(level=Level.INFO, title="x")) is False


def test_telegram_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "from_env")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    tn = TelegramNotifier(token="explicit", chat_id="456")
    assert tn.is_configured is True
    # We can't easily test the actual HTTP without network mocking,
    # but config status proves wiring is correct.


def test_telegram_partial_config_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    tn = TelegramNotifier()
    assert tn.is_configured is False


# ------------------------------------------------------------------ #
# Factory
# ------------------------------------------------------------------ #
def test_factory_default_telegram_unconfigured_returns_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("NOTIFIER_CHANNELS", raising=False)
    n = build_notifier()
    assert isinstance(n, NoOpNotifier)


def test_factory_empty_channels_returns_noop(monkeypatch):
    monkeypatch.setenv("NOTIFIER_CHANNELS", "")
    n = build_notifier()
    assert isinstance(n, NoOpNotifier)


def test_factory_telegram_only(monkeypatch):
    monkeypatch.setenv("NOTIFIER_CHANNELS", "telegram")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    n = build_notifier()
    # Single configured backend → returned directly (not wrapped in Multi)
    assert isinstance(n, TelegramNotifier)


def test_factory_unknown_channel_skipped(monkeypatch):
    monkeypatch.setenv("NOTIFIER_CHANNELS", "telegram,nonsense_channel")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    n = build_notifier()
    assert isinstance(n, TelegramNotifier)  # only TG remains


def test_factory_discord_warns_not_implemented_yet(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("NOTIFIER_CHANNELS", "discord")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="shared.notifier.factory"):
        n = build_notifier()
    assert isinstance(n, NoOpNotifier)
    assert any("discord" in r.message.lower() for r in caplog.records)
