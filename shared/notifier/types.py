"""Notifier payload types — Message + Level.

Separate from base.py so backends can import types without the protocol
plumbing (avoids circular imports when e.g. a backend wants to format
structured payloads differently per Level).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any


class Level(IntEnum):
    """Ordered by escalation. Backends may filter — e.g. Discord only
    sends WARN+ to reduce noise, Telegram gets everything."""

    DEBUG = 10        # ~never sent in prod, for dev troubleshooting
    INFO = 20         # routine events (signal fired, trade opened)
    WARN = 30         # guard triggered, latency exceeded, retry needed
    ERROR = 40        # component failure, transport error
    CRITICAL = 50     # circuit-breaker, data loss, needs human NOW


@dataclass(slots=True, frozen=True)
class Message:
    """What gets sent. Backends format differently:
      - Telegram: title on line 1 + body, no structured data
      - Discord:  embed with title + body + tags as chips
      - Webhook:  full JSON including structured `data`

    Tags are for routing/filtering: `tags=["sm","signal"]` lets a backend
    configured with `tag_filter=["sm"]` pick it up while others skip.
    """

    level: Level
    title: str
    body: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    data: dict[str, Any] = field(default_factory=dict)   # backend-specific structured payload
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_plain_text(self) -> str:
        """Flat text render — used by Telegram / log fallback."""
        out = [self.title] if self.title else []
        if self.body:
            out.append(self.body)
        if self.tags:
            out.append(f"tags: {' '.join(self.tags)}")
        return "\n".join(out)


__all__ = ["Level", "Message"]
