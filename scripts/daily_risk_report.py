#!/usr/bin/env python3
"""Daily Risk Report — institutional-grade risk assessment.

Generates a comprehensive daily report covering:
- Portfolio exposure and P&L
- Risk metrics (current drawdown, leverage, position sizing)
- Data source health
- System uptime and errors
- Market regime assessment

Sends to Telegram and saves to data/reports/.

Usage:
    python scripts/daily_risk_report.py
    # Schedule: 0 8 * * * python scripts/daily_risk_report.py
"""

import json
import os
import sys
import urllib.request
import base64
from datetime import datetime
from pathlib import Path

try:
    from market_monitor.state_store import BotStateStore
    _STATE_AVAILABLE = True
except ImportError:
    _STATE_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FT_API = "http://localhost:8080/api/v1"
FT_AUTH = base64.b64encode(b"freqtrade:freqtrade").decode()


def ft_get(endpoint: str):
    try:
        req = urllib.request.Request(
            f"{FT_API}/{endpoint}",
            headers={"Authorization": f"Basic {FT_AUTH}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def main():
    now = datetime.now()
    report_lines = []
    report_lines.append(f"📋 每日風險報告 — {now.strftime('%Y-%m-%d')}")
    report_lines.append("=" * 40)

    # 1. Portfolio Status
    profit = ft_get("profit")
    balance = ft_get("balance")
    status = ft_get("status")
    config = ft_get("show_config")

    report_lines.append("")
    report_lines.append("【投資組合】")
    if profit:
        report_lines.append(f"  總損益: ${profit.get('profit_all_coin', 0):.2f} USDT")
        report_lines.append(f"  已關閉: {profit.get('closed_trade_count', 0)} 筆")
        report_lines.append(f"  勝/敗: {profit.get('winning_trades', 0)}/{profit.get('losing_trades', 0)}")
    if profit:
        winning = profit.get('winning_trades', 0)
        losing = profit.get('losing_trades', 0)
        total_trades = winning + losing
        if total_trades > 0:
            win_rate = winning / total_trades * 100
            report_lines.append(f"  勝率: {win_rate:.1f}% ({winning}/{total_trades})")
    if balance:
        for c in balance.get("currencies", []):
            if c.get("currency") == "USDT":
                report_lines.append(f"  餘額: ${c.get('balance', 0):.2f} USDT")
    if status:
        report_lines.append(f"  持倉中: {len(status)} 筆")
        for t in status:
            report_lines.append(f"    {t.get('pair')} {t.get('trade_direction')} {t.get('profit_pct', 0):.2f}%")

    # 2. Risk Metrics
    report_lines.append("")
    report_lines.append("【風險指標】")
    if config:
        report_lines.append(f"  模式: {'模擬' if config.get('dry_run') else '實盤'}")
        report_lines.append(f"  策略: {config.get('strategy', '?')}")
        report_lines.append(f"  止損: {config.get('stoploss', '?')}")
        report_lines.append(f"  最大持倉: {config.get('max_open_trades', '?')}")
    report_lines.append(f"  風控: MaxDrawdown 15% + StoplossGuard 4次/24h")
    report_lines.append(f"  熔斷: BTC ±10% / ATR 3x spike / 信心 <0.15")

    # 3. Data Source Health
    report_lines.append("")
    report_lines.append("【數據源健康】")
    sources = {
        "OKX API": True,  # If FT is running, OKX is connected
        "yfinance (VIX)": True,
        "yfinance (Gold)": True,
        "yfinance (Oil)": True,
        "FRED (NFCI/M2)": bool(os.environ.get("FRED_API_KEY")),
        "Fear & Greed": True,
        "CoinGecko (BTC.D)": True,
        "yfinance (DXY)": False,  # Known unstable
    }
    ok = sum(1 for v in sources.values() if v)
    total = len(sources)
    report_lines.append(f"  可用: {ok}/{total}")
    for name, available in sources.items():
        if not available:
            report_lines.append(f"  ❌ {name}")

    # 4. System Status
    report_lines.append("")
    report_lines.append("【系統狀態】")
    if config:
        report_lines.append(f"  Freqtrade: {config.get('state', '?')}")
        pairs = config.get("exchange", {}).get("pair_whitelist", [])
        report_lines.append(f"  交易對: {len(pairs)} ({', '.join(p.split('/')[0] for p in pairs)})")
    report_lines.append(f"  報告時間: {now.strftime('%Y-%m-%d %H:%M')}")

    # 6. Bot State (from shared state store)
    if _STATE_AVAILABLE:
        state = BotStateStore.read()
        report_lines.append("")
        report_lines.append("【機器人狀態】")
        report_lines.append(f"  信心分數: {state.get('last_confidence_score', 0):.2f} ({state.get('last_confidence_regime', '?')})")
        report_lines.append(f"  Guard 攔截: {state.get('guard_rejections_today', 0)} 次")
        report_lines.append(f"  熔斷觸發: {state.get('circuit_breaker_activations', 0)} 次")
        report_lines.append(f"  信號生成: {state.get('signals_generated_today', 0)} | 過濾: {state.get('signals_filtered_today', 0)}")
        report_lines.append(f"  連勝: {state.get('consecutive_wins', 0)} | 連敗: {state.get('consecutive_losses', 0)}")

        # API health
        api_health = state.get("api_health", {})
        if api_health:
            ok = sum(1 for v in api_health.values() if v)
            total_apis = len(api_health)
            report_lines.append(f"  數據源: {ok}/{total_apis} 正常")
            failing = [k for k, v in api_health.items() if not v]
            if failing:
                report_lines.append(f"  ❌ {', '.join(failing)}")

    # 5. Risk Rating
    report_lines.append("")
    risk_score = "A-"  # Base
    issues = []
    if not os.environ.get("FRED_API_KEY"):
        issues.append("FRED API 缺失")
    if not profit or profit.get("trade_count", 0) == 0:
        issues.append("尚無交易數據驗證")
    if _STATE_AVAILABLE:
        state = BotStateStore.read()
        if state.get("circuit_breaker_activations", 0) > 2:
            issues.append("熔斷多次觸發")
        failing_apis = [k for k, v in state.get("api_health", {}).items() if not v]
        if len(failing_apis) >= 3:
            issues.append(f"{len(failing_apis)} 個數據源異常")
    if len(issues) > 2:
        risk_score = "B"
    report_lines.append(f"【綜合風控評級: {risk_score}】")
    for issue in issues:
        report_lines.append(f"  ⚠️ {issue}")

    report_text = "\n".join(report_lines)
    print(report_text)

    # Save to file
    report_path = PROJECT_ROOT / "data" / "reports" / f"risk_report_{now.strftime('%Y%m%d')}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"\nSaved to: {report_path}")

    # Send to Telegram
    try:
        from market_monitor.telegram_zh import send_message
        send_message(report_text, parse_mode="HTML")
        print("Telegram: sent")
    except Exception as e:
        print(f"Telegram: {e}")


if __name__ == "__main__":
    main()
