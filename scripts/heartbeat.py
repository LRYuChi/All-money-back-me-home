#!/usr/bin/env python3
"""系統脈搏檢查 — 每 4 小時執行一次的健康檢查腳本.

收集各子系統的運行狀態，發送「系統脈搏」通知至 Telegram。

檢查項目:
- Freqtrade API 狀態（配置、持倉、損益、餘額）
- 信號管線（K 線指標是否正常計算）
- 數據源健康度
- 機器人狀態存儲

Usage:
    python scripts/heartbeat.py
    # 排程: 0 */4 * * * python scripts/heartbeat.py
"""

import json
import sys
import urllib.request
import base64
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FT_API = "http://localhost:8080/api/v1"
_ft_creds = f"{os.environ.get('FT_USER', 'freqtrade')}:{os.environ.get('FT_PASS', 'freqtrade')}"
FT_AUTH = base64.b64encode(_ft_creds.encode()).decode()

# 初始資金（用於估算回撤）
INITIAL_CAPITAL = 1000.0


def ft_get(endpoint: str):
    """呼叫 Freqtrade REST API，回傳 JSON 或 None."""
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
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] 系統脈搏檢查開始...")

    # ── 1. Freqtrade API 狀態 ──
    config = ft_get("show_config")
    status = ft_get("status")
    profit = ft_get("profit")
    balance = ft_get("balance")

    # 判斷機器人整體狀態
    bot_status = "RUNNING"
    is_dry_run = True

    if not config:
        bot_status = "ERROR"
        print("  ❌ Freqtrade API 無回應")
    else:
        state = config.get("state", "")
        is_dry_run = config.get("dry_run", True)
        strategy = config.get("strategy", "未知")
        pairs = config.get("exchange", {}).get("pair_whitelist", [])
        print(f"  機器人: {state} ({'模擬' if is_dry_run else '實盤'})")
        print(f"  策略: {strategy}")
        print(f"  交易對: {len(pairs)} ({', '.join(p.split('/')[0] for p in pairs)})")
        if state != "running":
            bot_status = "STOPPED"

    # ── 2. 持倉資訊 ──
    open_positions = []
    if status is not None:
        print(f"  持倉中: {len(status)} 筆")
        for t in status:
            pair = t.get("pair", "?")
            direction = t.get("trade_direction", "?")
            pnl_pct = t.get("profit_pct", 0)
            pnl_usdt = t.get("profit_abs", 0)
            print(f"    {pair} {direction} {pnl_pct:+.2f}%")
            open_positions.append({
                "pair": pair,
                "side": direction,
                "profit_pct": pnl_pct,
                "profit_usdt": pnl_usdt,
            })
    else:
        print("  ⚠️ 無法取得持倉資訊")

    # ── 3. 損益統計 ──
    winning = 0
    losing = 0
    win_rate = 0.0

    if profit:
        total_pnl = profit.get("profit_all_coin", 0)
        winning = profit.get("winning_trades", 0)
        losing = profit.get("losing_trades", 0)
        closed = profit.get("closed_trade_count", 0)
        print(f"  總損益: {total_pnl:+.2f} USDT ({closed} 筆已關閉)")
        if winning + losing > 0:
            win_rate = winning / (winning + losing) * 100
            print(f"  勝率: {win_rate:.1f}% (勝 {winning} / 敗 {losing})")
    else:
        print("  ⚠️ 無法取得損益資訊")

    # ── 4. 帳戶餘額與回撤估算 ──
    equity = 0.0
    drawdown_pct = 0.0

    if balance:
        for c in balance.get("currencies", []):
            if c.get("currency") == "USDT":
                equity = c.get("balance", 0)
                print(f"  餘額: {equity:,.2f} USDT")
                if equity > 0 and INITIAL_CAPITAL > 0:
                    drawdown_pct = max(0.0, (1 - equity / INITIAL_CAPITAL) * 100)
                    if drawdown_pct > 0:
                        print(f"  估算回撤: {drawdown_pct:.1f}%")
                    else:
                        print(f"  累積報酬: {(equity / INITIAL_CAPITAL - 1) * 100:.1f}%")
                break
    else:
        print("  ⚠️ 無法取得餘額資訊")

    # ── 5. 信號管線檢查 ──
    candles = ft_get("pair_candles?pair=BTC/USDT:USDT&timeframe=1h&limit=5")
    if candles:
        data = candles.get("data", [])
        if len(data) > 0:
            columns = candles.get("columns", [])
            indicator_count = len([c for c in columns if c not in
                                   ("date", "open", "high", "low", "close", "volume")])
            print(f"  ✅ 信號管線正常（{len(data)} 根 K 線, {indicator_count} 個指標）")
        else:
            print("  ⚠️ K 線數據為空")
            if bot_status == "RUNNING":
                bot_status = "DEGRADED"
    else:
        print("  ❌ 無法取得 K 線數據")
        if bot_status == "RUNNING":
            bot_status = "DEGRADED"

    # ── 6. 數據源健康 ──
    data_health = None
    try:
        from market_monitor.health_check import DataFreshnessChecker
        checker = DataFreshnessChecker()
        report = checker.get_health_report()
        data_health = report.get("sources", {})
        summary = report.get("summary", {})
        severity = report.get("severity", "?")
        print(f"  數據源: {severity} ({summary.get('healthy', 0)}/{summary.get('total', 0)} 正常)")
        for src in report.get("unhealthy_sources", []):
            print(f"    ❌ {src}")
    except Exception as e:
        print(f"  ⚠️ 健康檢查失敗: {e}")

    # ── 7. 機器人狀態存儲 ──
    confidence = 0.0
    regime = "HIBERNATE"
    signal_summary = None
    guard_rejections = 0
    circuit_breaker = 0
    crypto_env = None

    try:
        from market_monitor.state_store import BotStateStore
        state = BotStateStore.read()
        confidence = state.get("last_confidence_score", 0.0)
        regime = state.get("last_confidence_regime", "HIBERNATE")
        signals_gen = state.get("signals_generated_today", 0)
        signals_flt = state.get("signals_filtered_today", 0)
        filter_reasons = state.get("filter_reasons", {})
        guard_rejections = state.get("guard_rejections_today", 0)
        circuit_breaker = state.get("circuit_breaker_activations", 0)
        crypto_env = state.get("crypto_env_cache", None)

        signal_summary = {
            "generated": signals_gen,
            "filtered": signals_flt,
            "reasons": filter_reasons,
        }

        regime_zh = {
            "AGGRESSIVE": "積極",
            "NORMAL": "正常",
            "CAUTIOUS": "謹慎",
            "DEFENSIVE": "防禦",
            "HIBERNATE": "休眠",
        }.get(regime, regime)

        print(f"  信心: {confidence:.2f} ({regime_zh})")
        print(f"  今日信號: 產生 {signals_gen} / 過濾 {signals_flt}")
        print(f"  Guard 攔截: {guard_rejections} / 熔斷: {circuit_breaker}")
    except Exception as e:
        print(f"  ⚠️ 狀態讀取失敗: {e}")

    # ── 8. 綜合健康評估 ──
    issues = []
    if not config:
        issues.append("Freqtrade API 離線")
    if not candles or not candles.get("data"):
        issues.append("信號管線異常")
    if drawdown_pct > 10:
        issues.append(f"回撤 {drawdown_pct:.1f}% 超過警戒")
    if winning + losing > 5 and win_rate < 40:
        issues.append(f"勝率偏低 {win_rate:.1f}%")

    if len(issues) == 0:
        print("\n✅ 系統運行正常")
    else:
        for issue in issues:
            print(f"  ⚠️ {issue}")
        if len(issues) >= 3 and bot_status == "RUNNING":
            bot_status = "DEGRADED"

    # ── 發送 Telegram 通知 ──
    try:
        from market_monitor.telegram_zh import notify_system_pulse
        notify_system_pulse(
            bot_status=bot_status,
            confidence=confidence,
            regime=regime,
            crypto_env=crypto_env,
            open_positions=open_positions if open_positions else None,
            equity=equity,
            drawdown_pct=drawdown_pct,
            max_drawdown_pct=drawdown_pct,  # 無歷史最大回撤，以當前值代替
            data_health=data_health,
            signal_summary=signal_summary,
            guard_rejections=guard_rejections,
            circuit_breaker_activations=circuit_breaker,
            is_dry_run=is_dry_run,
        )
        print("\nTelegram: 已發送")
    except Exception as e:
        print(f"\nTelegram: {e}")

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 脈搏檢查完成")


if __name__ == "__main__":
    main()
