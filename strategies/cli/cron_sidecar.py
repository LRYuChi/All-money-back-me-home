"""Supertrend cron sidecar — R56.

Long-running scheduler that fires three recurring jobs without an external
cron daemon. Designed to run inside a docker compose service that has the
strategies/ + market_monitor/ packages mounted plus access to the journal
volume.

Schedule (all times UTC):
  * 00:05  — daily_summary  (window: 24h)
  * Mon 00:30 — weekly_review
  * Every 6h on the hour (00,06,12,18) — regime_check
       posts to Telegram only if classification changed since last fire

State persisted in $CRON_STATE_FILE (default /app/data/cron_state.json) so a
restart inside the same minute does not double-fire jobs.

Run:
    python -m strategies.cli.cron_sidecar
    python -m strategies.cli.cron_sidecar --once   # single tick, exit
    python -m strategies.cli.cron_sidecar --dry-run

Exit codes:
    0  — graceful stop (SIGTERM / --once)
    1  — fatal config error
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("supertrend.cron")

DEFAULT_STATE_FILE = Path(
    os.environ.get("CRON_STATE_FILE", "/app/data/cron_state.json"),
)
TICK_SECONDS = 30


# =================================================================== #
# State persistence
# =================================================================== #
@dataclass
class CronState:
    """Last-fire markers + last-known regime for change detection."""

    last_daily_date: str = ""        # YYYY-MM-DD UTC
    last_weekly_date: str = ""       # YYYY-MM-DD UTC of last Monday fire
    last_regime_slot: str = ""       # YYYY-MM-DDTHH (00/06/12/18 hour bucket)
    last_regime_value: str = ""      # last regime string e.g. "trending"

    @classmethod
    def load(cls, path: Path) -> "CronState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(**{k: data.get(k, "") for k in cls.__dataclass_fields__})
        except Exception as e:
            logger.warning("state file %s unreadable (%s) — starting fresh",
                           path, e)
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2))
        tmp.replace(path)


# =================================================================== #
# Job dispatchers
# =================================================================== #
def _run_module(module: str, args: list[str], dry_run: bool) -> int:
    """Invoke `python -m <module> ...` as a subprocess so any single job
    crash never kills the sidecar loop."""
    cmd = [sys.executable, "-m", module, *args]
    logger.info("→ exec %s", " ".join(cmd))
    if dry_run:
        return 0
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("%s exited %d\nstdout=%s\nstderr=%s",
                         module, result.returncode,
                         result.stdout[-500:], result.stderr[-500:])
        else:
            logger.info("%s ok", module)
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.error("%s timed out after 300s", module)
        return 124
    except Exception as e:
        logger.exception("%s dispatch failed: %s", module, e)
        return 1


def _fetch_current_regime() -> tuple[str, str | None]:
    """Compute current BTC regime via the same path /api/supertrend/regime
    uses. Returns (regime_value, error_or_none)."""
    try:
        import ccxt   # noqa: F401  (ensures runtime install present)
        import pandas as pd
        from strategies.market_regime import (
            classify_regime,
            compute_adx_30d_median,
            compute_atr_price_ratio,
            compute_hurst_exponent,
        )
        import ccxt as _ccxt
        ex = _ccxt.okx({"enableRateLimit": True})
        ohlcv = ex.fetch_ohlcv("BTC/USDT:USDT", timeframe="1d", limit=200)
        if not ohlcv or len(ohlcv) < 50:
            return ("", "insufficient OHLCV")
        df = pd.DataFrame(
            ohlcv, columns=["ts", "open", "high", "low", "close", "volume"],
        )
        atr = compute_atr_price_ratio(df)
        adx = compute_adx_30d_median(df)
        hurst = compute_hurst_exponent(df)
        regime = classify_regime(atr, adx, hurst)
        return (regime.value, None)
    except Exception as e:
        return ("", str(e))


def _send_telegram(text: str, dry_run: bool) -> None:
    """Reuse the strategy's broadcast helper so messages land on the same
    Telegram bots ops already monitors."""
    if dry_run:
        print(f"[dry-run telegram]\n{text}")
        return
    try:
        from strategies.supertrend import _send_to_all_bots
        _send_to_all_bots(text)
    except Exception as e:
        logger.warning("telegram send failed (%s) — printing instead", e)
        print(text)


# =================================================================== #
# Tick logic — checks all jobs each minute
# =================================================================== #
def tick(now_utc: datetime, state: CronState, *, dry_run: bool) -> bool:
    """Run any due jobs. Returns True if state changed (caller persists)."""
    changed = False
    today = now_utc.strftime("%Y-%m-%d")
    hour = now_utc.hour
    minute = now_utc.minute
    weekday = now_utc.weekday()   # 0 = Mon

    # --- daily_summary at 00:05 UTC ---------------------------------- #
    if hour == 0 and minute >= 5 and state.last_daily_date != today:
        rc = _run_module(
            "strategies.cli.daily_summary",
            ["--hours", "24"],
            dry_run=dry_run,
        )
        if rc == 0:
            state.last_daily_date = today
            changed = True

    # --- weekly_review Mondays 00:30 UTC ----------------------------- #
    if weekday == 0 and hour == 0 and minute >= 30 \
            and state.last_weekly_date != today:
        rc = _run_module(
            "strategies.cli.weekly_review",
            [],
            dry_run=dry_run,
        )
        if rc == 0:
            state.last_weekly_date = today
            changed = True

    # --- regime check every 6h on the hour (00/06/12/18) ------------- #
    if hour in (0, 6, 12, 18) and minute < 5:
        slot = f"{today}T{hour:02d}"
        if state.last_regime_slot != slot:
            current, err = _fetch_current_regime()
            if err:
                logger.warning("regime fetch failed: %s", err)
            else:
                logger.info("regime=%s (prev=%s)",
                            current, state.last_regime_value or "—")
                if state.last_regime_value and state.last_regime_value != current:
                    msg = (
                        f"⚠️ *Regime 變更*\n"
                        f"`{state.last_regime_value}` → `{current}`\n"
                        f"_{now_utc.strftime('%Y-%m-%d %H:%M UTC')}_"
                    )
                    _send_telegram(msg, dry_run=dry_run)
                state.last_regime_value = current
                state.last_regime_slot = slot
                changed = True

    return changed


# =================================================================== #
# Entry
# =================================================================== #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategies.cli.cron_sidecar",
        description="Recurring Supertrend ops jobs (daily/weekly/regime).",
    )
    p.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE_FILE,
        help=f"Where to persist last-fire markers (default {DEFAULT_STATE_FILE})",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Run one tick and exit (useful for one-off/cron hybrid).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Resolve & log jobs without executing subprocesses or Telegram.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


_stop = False


def _install_signal_handlers() -> None:
    def _handle(signum, _frame):   # noqa: ANN001
        global _stop
        logger.info("received signal %s — exiting after current tick", signum)
        _stop = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    _install_signal_handlers()

    state = CronState.load(args.state_file)
    logger.info("cron sidecar started — state=%s", state.__dict__)

    if args.once:
        if tick(datetime.now(timezone.utc), state, dry_run=args.dry_run):
            state.save(args.state_file)
        return 0

    while not _stop:
        try:
            if tick(datetime.now(timezone.utc), state, dry_run=args.dry_run):
                state.save(args.state_file)
        except Exception:
            logger.exception("tick raised — continuing")
        time.sleep(TICK_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
