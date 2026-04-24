"""Notifier abstraction — decouple alerts from transports.

One `Notifier` Protocol, multiple backends (Telegram / Discord / Webhook /
NoOp). `MultiChannelNotifier` fans out to N backends in parallel so a
single backend failure (rate limit, network blip, token expiry) never
blocks the others.

New modules from Phase B onward MUST use `shared.notifier` rather than
directly calling `telegram_zh.send_message`. Legacy callers continue
working — we don't touch `market_monitor/telegram_zh.py`.

Usage:
    from shared.notifier import build_notifier, Message, Level
    notifier = build_notifier(settings)
    notifier.send(Message(level=Level.INFO, title="SM signal", body="..."))
"""

from shared.notifier.types import Level, Message
from shared.notifier.base import (
    MultiChannelNotifier,
    NoOpNotifier,
    Notifier,
)
from shared.notifier.telegram import TelegramNotifier
from shared.notifier.factory import build_notifier

__all__ = [
    "Level",
    "Message",
    "Notifier",
    "MultiChannelNotifier",
    "NoOpNotifier",
    "TelegramNotifier",
    "build_notifier",
]
