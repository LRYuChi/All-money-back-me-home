"""CLI: weekly Supertrend performance review (P2-10, round 47).

Difference from daily_summary:
  - Compares THIS week vs LAST week (not just a snapshot)
  - Highlights notable changes (PF jumped/dropped, max DD, streak shifts)
  - Recommended action: per-pair winners/losers + suggested whitelist tweaks

Usage (cron weekly Mondays 00:10 UTC):
    python -m strategies.cli.weekly_review

    # Custom week length (default 7 days)
    python -m strategies.cli.weekly_review --days 14

    # Dry-run
    python -m strategies.cli.weekly_review --dry-run

Recommended cron:
    10 0 * * 1 cd /opt/ambmh && \\
        .venv/bin/python -m strategies.cli.weekly_review

Exit codes:
    0  — sent / printed
    1  — IO error
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategies.journal import TradeJournal
from strategies.performance import (
    PerformanceAggregator,
    PerformanceSnapshot,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategies.cli.weekly_review",
        description="Weekly Supertrend performance review with WoW comparison.",
    )
    p.add_argument(
        "--days", type=int, default=7,
        help="Window length in days (default 7).",
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


def _delta_arrow(curr: float, prev: float, *, higher_better: bool = True) -> str:
    if prev == 0 and curr == 0:
        return "→"
    if curr > prev:
        return "📈" if higher_better else "📉"
    if curr < prev:
        return "📉" if higher_better else "📈"
    return "→"


def _fmt_delta_pct(curr: float, prev: float, *, higher_better: bool = True) -> str:
    arrow = _delta_arrow(curr, prev, higher_better=higher_better)
    diff = curr - prev
    return f"{arrow} `{diff:+.2f}`"


def _format_review(curr: PerformanceSnapshot,
                   prev: PerformanceSnapshot,
                   days: int) -> str:
    lines = [f"📅 *Supertrend 週結 — 近 {days} 天 vs 前 {days} 天*", ""]

    if curr.n_trades == 0 and prev.n_trades == 0:
        lines.append("_本週與上週皆無交易 — 策略沒有觸發訊號。_")
        return "\n".join(lines)

    lines.append("🎯 *核心指標 (本週 vs 前週)*")
    lines.append(
        f"   交易數: `{curr.n_trades}` ↔ `{prev.n_trades}`  "
        f"({_delta_arrow(curr.n_trades, prev.n_trades)})"
    )
    lines.append(
        f"   勝率: `{curr.win_rate:.1%}` ↔ `{prev.win_rate:.1%}`  "
        f"{_fmt_delta_pct(curr.win_rate * 100, prev.win_rate * 100)}%"
    )
    lines.append(
        f"   獲利因子: `{_fmt_pf(curr.profit_factor)}` ↔ "
        f"`{_fmt_pf(prev.profit_factor)}`"
    )
    lines.append(
        f"   每筆預期: `{curr.expectancy_pct:+.2f}%` ↔ "
        f"`{prev.expectancy_pct:+.2f}%`  "
        f"{_fmt_delta_pct(curr.expectancy_pct, prev.expectancy_pct)}%"
    )
    lines.append(
        f"   累計 PnL: `${curr.sum_pnl_usd:+.2f}` ↔ "
        f"`${prev.sum_pnl_usd:+.2f}`  "
        f"{_fmt_delta_pct(curr.sum_pnl_usd, prev.sum_pnl_usd)}$"
    )
    lines.append(
        f"   最大回撤: `-{curr.max_drawdown_pct:.2f}%` ↔ "
        f"`-{prev.max_drawdown_pct:.2f}%`  "
        f"{_fmt_delta_pct(curr.max_drawdown_pct, prev.max_drawdown_pct, higher_better=False)}%"
    )

    # Notable changes
    notable: list[str] = []
    if prev.n_trades > 0 and curr.n_trades / max(prev.n_trades, 1) > 1.5:
        notable.append(
            f"⚠️ 交易數激增 {prev.n_trades} → {curr.n_trades} (+{((curr.n_trades / prev.n_trades) - 1) * 100:.0f}%) "
            f"— 注意 over-firing"
        )
    if curr.profit_factor < 1.0 and prev.profit_factor >= 1.0:
        notable.append(
            f"⚠️ 獲利因子由 {prev.profit_factor:.2f} 跌破 1.0 → {curr.profit_factor:.2f} "
            f"— 策略本週淨虧損"
        )
    if curr.max_drawdown_pct > prev.max_drawdown_pct * 1.5 and prev.max_drawdown_pct > 0:
        notable.append(
            f"⚠️ DD 擴大: {prev.max_drawdown_pct:.2f}% → {curr.max_drawdown_pct:.2f}%"
        )
    if curr.current_streak <= -3:
        notable.append(
            f"⚠️ 當前連負 {-curr.current_streak} — 接近斷路器閾值 (3)"
        )
    if curr.kelly_fraction < 0.05 and prev.kelly_fraction >= 0.05:
        notable.append(
            f"⚠️ Kelly 跌至 {curr.kelly_fraction:.1%} — 倉位 sizing 將縮小"
        )

    if notable:
        lines.append("")
        lines.append("🚨 *本週警示*")
        lines.extend([f"   {n}" for n in notable])

    # Per-pair winners + losers (current week only)
    if curr.by_pair:
        sorted_pairs = sorted(
            curr.by_pair.items(),
            key=lambda kv: kv[1].sum_pnl_pct,
            reverse=True,
        )
        winners = sorted_pairs[:3]
        losers = [p for p in sorted_pairs[-3:] if p[1].sum_pnl_pct < 0]
        if winners and winners[0][1].sum_pnl_pct > 0:
            lines.append("")
            lines.append("🏆 *本週前 3 名*")
            for pair, gs in winners:
                if gs.sum_pnl_pct > 0:
                    lines.append(
                        f"   `{pair:<22}` n=`{gs.n}` 累積=`{gs.sum_pnl_pct:+.2f}%`"
                    )
        if losers:
            lines.append("")
            lines.append("📉 *本週倒數 3 名 (建議檢視)*")
            for pair, gs in losers:
                lines.append(
                    f"   `{pair:<22}` n=`{gs.n}` 累積=`{gs.sum_pnl_pct:+.2f}%`"
                )

    # Per-tag (scout vs confirmed) WoW
    if curr.by_tag:
        lines.append("")
        lines.append("🏷️ *分階段表現*")
        for tag, gs in sorted(curr.by_tag.items()):
            prev_gs = prev.by_tag.get(tag)
            if prev_gs:
                lines.append(
                    f"   `{tag:<10}` 本週 n=`{gs.n}` 勝率=`{gs.win_rate:.1%}` "
                    f"vs 前週 n=`{prev_gs.n}` 勝率=`{prev_gs.win_rate:.1%}`"
                )
            else:
                lines.append(
                    f"   `{tag:<10}` 本週 n=`{gs.n}` 勝率=`{gs.win_rate:.1%}` (前週無)"
                )

    # Exit reason analysis (where are losses coming from?)
    if curr.by_exit_reason:
        lines.append("")
        lines.append("🚪 *本週出場原因*")
        for reason, gs in sorted(
            curr.by_exit_reason.items(),
            key=lambda kv: kv[1].n, reverse=True,
        )[:5]:
            tag = "✅" if gs.avg_pnl_pct > 0 else "❌"
            lines.append(
                f"   {tag} `{reason:<22}` n=`{gs.n}` 平均=`{gs.avg_pnl_pct:+.2f}%`"
            )

    return "\n".join(lines)


def _fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    journal_dir = _resolve_journal_dir(args.dir)
    if not journal_dir.exists():
        logger.warning("journal dir %s does not exist", journal_dir)

    journal = TradeJournal(journal_dir)
    agg = PerformanceAggregator(journal)
    now = datetime.now(timezone.utc)

    # Current week
    curr = agg.snapshot(
        from_date=now - timedelta(days=args.days),
        to_date=now,
    )
    # Prior week (immediately before current)
    prev = agg.snapshot(
        from_date=now - timedelta(days=args.days * 2),
        to_date=now - timedelta(days=args.days),
    )

    text = _format_review(curr, prev, args.days)

    if args.dry_run:
        print(text)
        return 0

    sent = _send_to_telegram(text)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
