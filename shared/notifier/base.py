"""Notifier Protocol + generic implementations (NoOp, MultiChannel)."""
from __future__ import annotations

import logging
from typing import Protocol

from shared.notifier.types import Level, Message

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """All backends implement this. `send()` MUST NOT raise — return bool
    to indicate delivery success/failure. Callers treat False as
    "escalate to logs" not "abort the thing I was doing".
    """

    def send(self, message: Message) -> bool: ...


class NoOpNotifier:
    """Accepts all messages, does nothing. Used when no backend is
    configured so call sites don't need null checks."""

    def send(self, message: Message) -> bool:
        logger.debug("NoOp notifier swallowing: %s", message.title)
        return True


class MultiChannelNotifier:
    """Fan out to N backends. One failing doesn't block the others.

    `min_level_by_channel` (optional): per-backend level threshold. Passing
    `{TelegramNotifier: Level.INFO, DiscordNotifier: Level.WARN}` sends
    every INFO+ to Telegram but only WARN+ to Discord.

    Returns True iff at least one backend reported success. If every
    backend fails we log ERROR and return False — caller may decide
    whether to retry or continue.
    """

    def __init__(
        self,
        channels: list[Notifier],
        *,
        min_level_by_channel: dict[type, Level] | None = None,
    ) -> None:
        self._channels = channels
        self._min_level_by_channel = min_level_by_channel or {}

    def send(self, message: Message) -> bool:
        if not self._channels:
            return False

        any_ok = False
        for ch in self._channels:
            floor = self._min_level_by_channel.get(type(ch))
            if floor is not None and message.level < floor:
                continue
            try:
                ok = ch.send(message)
            except Exception as e:  # defensive: backend must not raise, but belt+braces
                logger.warning(
                    "notifier backend %s raised: %s", type(ch).__name__, e,
                )
                ok = False
            any_ok = any_ok or ok

        if not any_ok:
            logger.error(
                "all notifier channels failed for msg=%r (level=%s)",
                message.title, message.level.name,
            )
        return any_ok


__all__ = ["Notifier", "NoOpNotifier", "MultiChannelNotifier"]
