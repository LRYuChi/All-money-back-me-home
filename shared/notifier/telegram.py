"""TelegramNotifier — stand-alone Bot API client.

Self-contained on purpose: doesn't import market_monitor.telegram_zh so
new code can use this without dragging in the legacy module. The split
also means we control the parse_mode / fallback / chunking ourselves
and don't accidentally inherit Markdown-escape bugs from telegram_zh.

Reads `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` from constructor args (so
factory can read settings) — falls back to env vars when args empty.

Behaviour:
  - Empty token/chat → send() returns False, no network call (graceful no-op)
  - Markdown failure → retry once as plain text (Telegram returns 400 on
    unescaped _ * [ ] etc.; same fallback as telegram_zh has long used)
  - Long messages chunked at 4000 chars (Telegram limit 4096, 96 chars
    margin for line breaks)
  - 10s HTTP timeout — fail open, don't hang the daemon

Level → emoji prefix is purely cosmetic but makes scanning Telegram fast.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Final

from shared.notifier.types import Level, Message

logger = logging.getLogger(__name__)

# Telegram message hard limit is 4096 chars; leave margin for level prefix + tags
_MAX_LEN: Final[int] = 4000

_LEVEL_EMOJI: Final[dict[Level, str]] = {
    Level.DEBUG: "🔧",
    Level.INFO: "ℹ️",
    Level.WARN: "⚠️",
    Level.ERROR: "🔴",
    Level.CRITICAL: "🚨",
}


class TelegramNotifier:
    """Telegram Bot API client. send() returns True on success."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        *,
        timeout_sec: int = 10,
    ) -> None:
        self._token = (token or os.environ.get("TELEGRAM_TOKEN", "")).strip()
        self._chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self._timeout = timeout_sec
        if not self._token or not self._chat_id:
            logger.info(
                "TelegramNotifier: token/chat_id empty → send() will be no-op",
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, message: Message) -> bool:
        if not self.is_configured:
            return False

        prefix = _LEVEL_EMOJI.get(message.level, "")
        title = f"{prefix} {message.title}".strip()
        body_lines = [title]
        if message.body:
            body_lines.append(message.body)
        if message.tags:
            body_lines.append(f"#{' #'.join(message.tags)}")
        text = "\n".join(body_lines)

        chunks = (
            [text]
            if len(text) <= _MAX_LEN
            else [text[i:i + _MAX_LEN] for i in range(0, len(text), _MAX_LEN)]
        )

        all_ok = True
        for chunk in chunks:
            # Try Markdown first; on failure retry as plain text.
            if not self._post(chunk, parse_mode="Markdown"):
                if not self._post(chunk, parse_mode=None):
                    logger.warning(
                        "telegram send failed (plain fallback): %s",
                        chunk[:80].replace("\n", " "),
                    )
                    all_ok = False
        return all_ok

    def _post(self, text: str, parse_mode: str | None) -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload: dict[str, object] = {"chat_id": self._chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status == 200
        except Exception:
            return False


__all__ = ["TelegramNotifier"]
