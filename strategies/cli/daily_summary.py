"""CLI: send daily Supertrend performance summary to Telegram (P1-5).

Usage (cron-friendly, runs once and exits):
    # Last 24h summary
    python -m strategies.cli.daily_summary

    # Custom window
    python -m strategies.cli.daily_summary --hours 168     # weekly
    python -m strategies.cli.daily_summary --days 30       # monthly

    # Dry-run: print to stdout without sending Telegram
    python -m strategies.cli.daily_summary --dry-run

    # Custom journal location (defaults to SUPERTREND_JOURNAL_DIR env or
    # trading_log/journal/)
    python -m strategies.cli.daily_summary --dir /path/to/journal

Recommended cron (daily 00:05 UTC, prepended with "summary at midnight"
plus a separator):
    5 0 * * * cd /opt/ambmh && \\
        .venv/bin/python -m strategies.cli.daily_summary --hours 24

Exit codes:
    0  — sent / printed (or no trades but heartbeat sent)
    1  — IO error
    2  — invalid args
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategies.journal import TradeJournal
from strategies.performance import (
    PerformanceAggregator,
    format_snapshot_md,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategies.cli.daily_summary",
        description="Send Supertrend performance summary to Telegram.",
    )
    window = p.add_mutually_exclusive_group()
    window.add_argument(
        "--hours", type=float, default=24,
        help="Window in hours (default 24).",
    )
    window.add_argument(
        "--days", type=int,
        help="Window in days (alternative to --hours).",
    )
    p.add_argument(
        "--dir", type=Path, default=None,
        help="Journal directory (default $SUPERTREND_JOURNAL_DIR or "
             "trading_log/journal).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print to stdout instead of sending Telegram.",
    )
    p.add_argument(
        "--include-cumulative", action="store_true",
        help="Also append all-time cumulative stats (separate section).",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def _resolve_journal_dir(arg_dir: Path | None) -> Path:
    if arg_dir is not None:
        return arg_dir
    env = os.environ.get("SUPERTREND_JOURNAL_DIR", "").strip()
    if env:
        return Path(env)
    return Path("trading_log/journal")


def _send_to_telegram(text: str) -> bool:
    """Send text to Telegram via direct Bot API call.

    R120: 不再 import strategies.supertrend (cron container 沒裝 talib,
    那邊 top-level import talib 會 ImportError → silent fail).
    用 inline urllib POST 完全 self-contained, 跟 R119 cron_sidecar 修復
    同模式. 環境變數未設或 send 失敗時印到 stdout, 永不 crash cron.
    """
    import os
    import json as _json
    import urllib.error
    import urllib.request

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("TELEGRAM_TOKEN/CHAT_ID 未設定 — 印到 stdout instead")
        print(text)
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for parse_mode in ("Markdown", None):
            payload = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            data = _json.dumps(payload).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"},
            )
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                if resp.status == 200:
                    return True
            except urllib.error.HTTPError as e:
                if e.code == 400 and parse_mode is not None:
                    continue   # markdown escape issue → retry plain
                raise
        logger.warning("telegram all attempts failed — printing instead")
        print(text)
        return False
    except Exception as e:
        logger.warning("send to telegram failed (%s) — printing instead", e)
        print(text)
        return False


def _format_combined(window_md: str, cumulative_md: str | None,
                     window_label: str) -> str:
    """Compose final message with section headers."""
    parts = [
        f"📅 *Supertrend 日結 — {window_label}*",
        "",
        window_md,
    ]
    if cumulative_md:
        parts.extend([
            "",
            "─────────────────────────",
            "📊 *全期累計*",
            "",
            cumulative_md,
        ])
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve window
    if args.days is not None:
        hours = args.days * 24.0
        window_label = f"近 {args.days} 天"
    else:
        hours = args.hours
        window_label = (
            f"近 {hours:.0f}h" if hours < 48
            else f"近 {hours/24:.0f} 天"
        )

    journal_dir = _resolve_journal_dir(args.dir)
    if not journal_dir.exists():
        logger.warning(
            "journal dir %s does not exist — sending empty heartbeat",
            journal_dir,
        )

    journal = TradeJournal(journal_dir)
    agg = PerformanceAggregator(journal)

    now = datetime.now(timezone.utc)
    snap_window = agg.snapshot(
        from_date=now - timedelta(hours=hours),
        to_date=now,
    )
    window_md = format_snapshot_md(snap_window)

    cumulative_md = None
    if args.include_cumulative:
        snap_all = agg.snapshot()
        cumulative_md = format_snapshot_md(snap_all)

    final = _format_combined(window_md, cumulative_md, window_label)

    if args.dry_run:
        print(final)
        return 0

    sent = _send_to_telegram(final)
    if not sent:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
