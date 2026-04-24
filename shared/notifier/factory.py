"""Notifier factory — assembles MultiChannelNotifier from settings.

Reads `NOTIFIER_CHANNELS` env (comma-separated) and constructs the
matching backends with their respective env vars.

Examples:
    NOTIFIER_CHANNELS=telegram        # default — single backend
    NOTIFIER_CHANNELS=telegram,discord
    NOTIFIER_CHANNELS=                # explicit empty → NoOp

If a requested backend is mis-configured (missing token), the factory
logs a warning and skips it; the resulting MultiChannelNotifier still
works for other channels. If all skip, returns NoOpNotifier.
"""
from __future__ import annotations

import logging
import os

from shared.notifier.base import MultiChannelNotifier, NoOpNotifier, Notifier
from shared.notifier.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


def build_notifier(settings=None) -> Notifier:  # noqa: ANN001 (allow None for back-compat)
    """Construct the active notifier.

    `settings` is currently unused — env vars are the source of truth.
    Param kept so callers can pass `smart_money.config.settings` for
    forward compatibility (Phase D may move to settings-driven channels).
    """
    del settings  # explicitly unused

    raw = os.environ.get("NOTIFIER_CHANNELS", "telegram")
    requested = [c.strip().lower() for c in raw.split(",") if c.strip()]

    if not requested:
        logger.info("NOTIFIER_CHANNELS empty → NoOpNotifier")
        return NoOpNotifier()

    channels: list[Notifier] = []
    for name in requested:
        if name == "telegram":
            tn = TelegramNotifier()
            if tn.is_configured:
                channels.append(tn)
            else:
                logger.warning("notifier: telegram requested but token/chat_id missing")
        elif name == "discord":
            logger.warning("notifier: discord backend not yet implemented (Phase D)")
        elif name == "webhook":
            logger.warning("notifier: webhook backend not yet implemented (Phase D)")
        else:
            logger.warning("notifier: unknown channel %r — skipping", name)

    if not channels:
        logger.warning("notifier: no usable channels → NoOpNotifier")
        return NoOpNotifier()

    if len(channels) == 1:
        # Skip the multi-channel wrapper for a single backend (cleaner logs)
        return channels[0]

    return MultiChannelNotifier(channels)


__all__ = ["build_notifier"]
