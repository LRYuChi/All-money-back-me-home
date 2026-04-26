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
    # R69: alert dispatch state (SUPERTREND)
    last_alerts_seen: list = None    # type: ignore[assignment]  # list[str]
    last_alerts_check_iso: str = ""  # iso timestamp of last poll
    # R73: alert dispatch state (SHADOW pipeline)
    last_shadow_alerts_seen: list = None    # type: ignore[assignment]
    last_shadow_alerts_check_iso: str = ""

    def __post_init__(self):
        if self.last_alerts_seen is None:
            self.last_alerts_seen = []
        if self.last_shadow_alerts_seen is None:
            self.last_shadow_alerts_seen = []

    @classmethod
    def load(cls, path: Path) -> "CronState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            # Coerce missing keys to defaults; tolerate older state files
            kwargs = {}
            for k in cls.__dataclass_fields__:
                if k in data:
                    kwargs[k] = data[k]
            return cls(**kwargs)
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
    # R119: 不再 import strategies.supertrend (cron container 沒裝 talib,
    # supertrend module top-level import talib 會 ModuleNotFoundError →
    # fallback 到 print → TG 收不到 alert = silent failure).
    # 改用 inline urllib POST 完全 self-contained, 跟 cron sidecar 既有
    # 的 freqtrade /start API 呼叫同模式.
    import json as _json
    import urllib.error
    import urllib.request
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 未設定 — 印到 stdout instead")
        print(text)
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        # 第一次嘗試帶 Markdown
        for attempt_parse_mode in ("Markdown", None):
            payload = {"chat_id": chat_id, "text": text}
            if attempt_parse_mode:
                payload["parse_mode"] = attempt_parse_mode
            data = _json.dumps(payload).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"},
            )
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                if resp.status == 200:
                    return   # success
            except urllib.error.HTTPError as e:
                if e.code == 400 and attempt_parse_mode is not None:
                    continue   # markdown escape issue → retry plain
                raise
        logger.warning("telegram all attempts failed — printing instead")
        print(text)
    except Exception as e:
        logger.warning("telegram send failed (%s) — printing instead", e)
        print(text)


# =================================================================== #
# Tick logic — checks all jobs each minute
# =================================================================== #
def _ensure_freqtrade_running(*, dry_run: bool) -> None:
    """R60: poll freqtrade REST API; POST /start if state=stopped.

    Runs every tick (30s). Cheap — single GET. Defensive: any error is
    logged at debug and silently swallowed (freqtrade may be booting,
    auth may be misconfigured, network may be flaky — never fatal).

    Reads freqtrade host/auth from FREQTRADE_API_URL / FT_USER / FT_PASS.
    Defaults to docker compose-internal http://freqtrade:8080 + freqtrade/freqtrade.
    """
    if dry_run:
        return
    if os.environ.get("SUPERTREND_AUTOSTART_FREQTRADE", "1") != "1":
        return
    import base64
    import json as _json
    import urllib.error
    import urllib.request

    api = os.environ.get("FREQTRADE_API_URL", "http://freqtrade:8080").rstrip("/")
    user = os.environ.get("FT_USER", "freqtrade")
    pwd = os.environ.get("FT_PASS", "freqtrade")
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    try:
        req = urllib.request.Request(
            f"{api}/api/v1/show_config", headers=headers,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        st = data.get("state", "")
        if st == "running":
            return
        if st != "stopped":
            logger.debug("freqtrade state=%s — not stopped, leaving alone", st)
            return
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("freqtrade show_config probe failed: %s", e)
        return
    except Exception as e:
        logger.debug("freqtrade probe unexpected error: %s", e)
        return

    # State is "stopped" — issue /start
    try:
        req = urllib.request.Request(
            f"{api}/api/v1/start", headers=headers, method="POST",
            data=b"",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        logger.info("freqtrade was stopped — issued /start (R60 autostart)")
    except Exception as e:
        logger.warning("freqtrade /start failed: %s", e)


# =================================================================== #
# R69: poll /api/supertrend/operations + diff-broadcast alerts
# =================================================================== #
def _fetch_operations_alerts() -> list[str] | None:
    """GET /operations from compose-internal API. Returns alert list or
    None on failure (caller should skip — don't broadcast on probe error)."""
    import json as _json
    import urllib.error
    import urllib.request

    api = os.environ.get("OPERATIONS_API_URL",
                         "http://api:8000/api/supertrend/operations")
    try:
        req = urllib.request.Request(api)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        alerts = data.get("alerts", [])
        if not isinstance(alerts, list):
            return []
        return [str(a) for a in alerts]
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("ops alerts probe failed: %s", e)
        return None
    except Exception as e:
        logger.debug("ops alerts probe unexpected error: %s", e)
        return None


# R83: Alert tag extraction for stable dedup. Alert messages include
# dynamic counters (e.g., "297/989 skips") that change every poll;
# diffing full strings caused spurious NEW + RESOLVED spam every 5min
# for the same logical alert family. Dedup by tag (text before " — ")
# instead — the tag is the alert RULE (CLOSE_WITHOUT_OPEN_DOMINANT,
# NO_FIRES_24H, RED_PIPELINE etc) which is stable across polls.
def _alert_tag(alert: str) -> str:
    """Extract stable tag from alert message.

    Examples:
      "NO_FIRES_24H — 612 evaluations, dominant blocker: vol<=1.2*ma..."
        → "NO_FIRES_24H"
      "ZERO_TRADEABLE_WALLETS"   (no body)
        → "ZERO_TRADEABLE_WALLETS"
      "BOT_STATE=stopped — issue POST..."
        → "BOT_STATE=stopped"   (state change is itself the identity)
    """
    if not alert:
        return ""
    # First " — " (em-dash) separates tag from descriptive body
    return alert.split(" — ", 1)[0].strip()


def check_operations_alerts(state: CronState, *, dry_run: bool,
                             now_utc: datetime) -> bool:
    """Diff current /operations alerts against last-seen, broadcast deltas.

    Default poll cadence: every 5 min. Caller (tick) gates by minute mark.
    Returns True if state changed.

    R83: dedup by stable tag (not full message) so changing counter
    numbers in the message body don't spam Telegram every 5min.
    """
    if os.environ.get("SUPERTREND_ALERT_BROADCAST", "1") != "1":
        return False
    current = _fetch_operations_alerts()
    if current is None:
        return False   # probe failure — leave state unchanged

    # R83: tag → most-recent full message (for broadcast detail)
    current_by_tag: dict[str, str] = {}
    for a in current:
        tag = _alert_tag(a)
        if tag:
            current_by_tag[tag] = a

    last_tags = set(state.last_alerts_seen or [])
    cur_tags = set(current_by_tag.keys())

    new_tags = cur_tags - last_tags
    resolved_tags = last_tags - cur_tags

    if not new_tags and not resolved_tags:
        state.last_alerts_check_iso = now_utc.isoformat()
        return True

    for tag in new_tags:
        msg = (
            f"⚠️ *Supertrend Alert (NEW)*\n"
            f"`{current_by_tag[tag]}`\n"
            f"_{now_utc.strftime('%Y-%m-%d %H:%M UTC')}_\n"
            f"_See /api/supertrend/operations for full snapshot_"
        )
        _send_telegram(msg, dry_run=dry_run)
        logger.info("posted NEW alert to telegram: %s", tag)

    for tag in resolved_tags:
        msg = (
            f"✅ *Supertrend Alert (RESOLVED)*\n"
            f"`{tag}`\n"
            f"_{now_utc.strftime('%Y-%m-%d %H:%M UTC')}_"
        )
        _send_telegram(msg, dry_run=dry_run)
        logger.info("posted RESOLVED alert to telegram: %s", tag)

    # Store TAGS, not full messages (smaller state, stable dedup)
    state.last_alerts_seen = sorted(cur_tags)
    state.last_alerts_check_iso = now_utc.isoformat()
    return True


# =================================================================== #
# R73: poll /api/smart-money/signal-health alerts + diff-broadcast
# =================================================================== #
def _fetch_shadow_alerts() -> list[str] | None:
    """GET /signal-health from compose-internal API. Returns alerts list
    or None on failure. Mirrors _fetch_operations_alerts for SHADOW pipeline."""
    import json as _json
    import urllib.error
    import urllib.request

    api = os.environ.get(
        "SHADOW_OPERATIONS_API_URL",
        "http://api:8000/api/smart-money/signal-health",
    )
    try:
        req = urllib.request.Request(api)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        # Note: when supabase is unavailable the endpoint returns
        # {configured: false, ...} and no alerts field — treat as empty
        alerts = data.get("alerts", [])
        if not isinstance(alerts, list):
            return []
        return [str(a) for a in alerts]
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("shadow alerts probe failed: %s", e)
        return None
    except Exception as e:
        logger.debug("shadow alerts probe unexpected error: %s", e)
        return None


def check_shadow_alerts(state: CronState, *, dry_run: bool,
                        now_utc: datetime) -> bool:
    """Diff current SHADOW alerts against last-seen, broadcast deltas.

    Same pattern as check_operations_alerts but for SHADOW pipeline.
    R83: tag-based dedup (not full message) to avoid spam from
    changing counter numbers between polls.
    """
    if os.environ.get("SHADOW_ALERT_BROADCAST", "1") != "1":
        return False
    current = _fetch_shadow_alerts()
    if current is None:
        return False

    current_by_tag: dict[str, str] = {}
    for a in current:
        tag = _alert_tag(a)
        if tag:
            current_by_tag[tag] = a

    last_tags = set(state.last_shadow_alerts_seen or [])
    cur_tags = set(current_by_tag.keys())

    new_tags = cur_tags - last_tags
    resolved_tags = last_tags - cur_tags

    if not new_tags and not resolved_tags:
        state.last_shadow_alerts_check_iso = now_utc.isoformat()
        return True

    for tag in new_tags:
        msg = (
            f"⚠️ *Shadow Alert (NEW)*\n"
            f"`{current_by_tag[tag]}`\n"
            f"_{now_utc.strftime('%Y-%m-%d %H:%M UTC')}_\n"
            f"_See /api/smart-money/signal-health for full snapshot_"
        )
        _send_telegram(msg, dry_run=dry_run)
        logger.info("posted NEW shadow alert to telegram: %s", tag)

    for tag in resolved_tags:
        msg = (
            f"✅ *Shadow Alert (RESOLVED)*\n"
            f"`{tag}`\n"
            f"_{now_utc.strftime('%Y-%m-%d %H:%M UTC')}_"
        )
        _send_telegram(msg, dry_run=dry_run)
        logger.info("posted RESOLVED shadow alert: %s", tag)

    state.last_shadow_alerts_seen = sorted(cur_tags)
    state.last_shadow_alerts_check_iso = now_utc.isoformat()
    return True


def tick(now_utc: datetime, state: CronState, *, dry_run: bool) -> bool:
    """Run any due jobs. Returns True if state changed (caller persists)."""
    changed = False
    today = now_utc.strftime("%Y-%m-%d")
    hour = now_utc.hour
    minute = now_utc.minute
    weekday = now_utc.weekday()   # 0 = Mon

    # R60: every tick, ensure the freqtrade bot is in running state.
    # Cheap probe (one HTTP GET); only POSTs when actually stopped.
    _ensure_freqtrade_running(dry_run=dry_run)

    # R69: every 5 minutes, poll SUPERTREND /operations alerts.
    # R73: same cadence, poll SHADOW /signal-health alerts.
    if minute % 5 == 0:
        if check_operations_alerts(state, dry_run=dry_run, now_utc=now_utc):
            changed = True
        if check_shadow_alerts(state, dry_run=dry_run, now_utc=now_utc):
            changed = True

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
