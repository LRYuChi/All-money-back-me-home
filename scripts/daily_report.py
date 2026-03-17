#!/usr/bin/env python3
"""Daily trading report — sends summary to Telegram.

Fetches status from Freqtrade API and confidence engine,
then sends a formatted report.

Usage:
    python scripts/daily_report.py
    # Or schedule via cron: 0 8 * * * python scripts/daily_report.py
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from market_monitor.telegram_zh import notify_daily_report, send_message

API_URL = "http://127.0.0.1:8080/api/v1"
API_AUTH = "freqtrade:freqtrade"


def api_get(endpoint: str) -> dict | None:
    """Fetch from Freqtrade API."""
    try:
        import base64
        auth = base64.b64encode(API_AUTH.encode()).decode()
        req = urllib.request.Request(
            f"{API_URL}/{endpoint}",
            headers={"Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def main():
    # Get profit data
    profit = api_get("profit")
    status = api_get("status")

    # Get confidence
    conf_path = PROJECT_ROOT / "data" / "reports" / "confidence.json"
    confidence = 0.5
    regime = "UNKNOWN"
    if conf_path.exists():
        with open(conf_path) as f:
            conf_data = json.load(f)
            confidence = conf_data.get("score", 0.5)
            regime = conf_data.get("regime", "UNKNOWN")

    if profit:
        total_profit = profit.get("profit_all_coin", 0)
        closed = profit.get("closed_trade_count", 0)
        winning = profit.get("winning_trades", 0)
        losing = profit.get("losing_trades", 0)
        open_trades = len(status) if status else 0

        notify_daily_report(
            total_profit=total_profit,
            win_count=winning,
            loss_count=losing,
            open_trades=open_trades,
            confidence=confidence,
            regime=regime,
        )
        print(f"Daily report sent: profit={total_profit:.2f}, "
              f"W/L={winning}/{losing}, open={open_trades}, "
              f"confidence={confidence:.2f} ({regime})")
    else:
        send_message(
            "📊 *每日報告*\n\n"
            "⚠️ 無法連接交易系統\n"
            f"🎯 信心指數: `{confidence:.2f}` ({regime})"
        )
        print("Bot not reachable, sent minimal report")


if __name__ == "__main__":
    main()
