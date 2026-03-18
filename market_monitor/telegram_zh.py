"""繁體中文 Telegram 通知模組.

透過 Telegram Bot API 發送交易通知，補充 Freqtrade 內建的英文訊息。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

# 從環境變數或直接設定
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not BOT_TOKEN or not CHAT_ID:
    logger.warning("TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID 未設定，Telegram 通知已停用")


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """發送 Telegram 訊息."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Telegram 發送失敗: %s", e)
        return False


def notify_startup(strategy: str, pairs: list[str], wallet: float = 1000):
    """系統啟動通知."""
    send_message(
        f"🟢 *交易系統已啟動*\n\n"
        f"📋 策略: `{strategy}`\n"
        f"💱 交易對: {', '.join(pairs)}\n"
        f"💰 模擬資金: {wallet} USDT\n"
        f"⏰ 時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"📊 模式: 模擬交易"
    )


def notify_entry(pair: str, side: str, rate: float, stake: float,
                 leverage: float, confidence: float, reason: str = ""):
    """進場通知."""
    direction = "📈 做多" if side == "long" else "📉 做空"
    conf_emoji = "🔥" if confidence >= 0.8 else "✅" if confidence >= 0.6 else "⚠️"

    send_message(
        f"{direction} *進場通知*\n\n"
        f"💱 交易對: `{pair}`\n"
        f"💲 進場價: `{rate:.2f}`\n"
        f"💰 倉位: `{stake:.2f} USDT`\n"
        f"⚡ 槓桿: `{leverage:.1f}x`\n"
        f"{conf_emoji} 信心指數: `{confidence:.2f}`\n"
        f"📝 原因: {reason}\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )


def notify_exit(pair: str, side: str, profit_pct: float, profit_usdt: float,
                exit_reason: str, duration: str, confidence: float):
    """出場通知."""
    if profit_pct >= 0:
        emoji = "💰" if profit_pct >= 5 else "✅"
        result = "獲利"
    else:
        emoji = "🔴"
        result = "虧損"

    direction = "做多" if side == "long" else "做空"

    send_message(
        f"{emoji} *{result}出場*\n\n"
        f"💱 交易對: `{pair}` ({direction})\n"
        f"📊 損益: `{profit_pct:+.2f}%` (`{profit_usdt:+.2f} USDT`)\n"
        f"📝 出場原因: `{exit_reason}`\n"
        f"⏱ 持倉時間: {duration}\n"
        f"🎯 信心指數: `{confidence:.2f}`\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )


def notify_stoploss(pair: str, side: str, loss_pct: float, loss_usdt: float):
    """止損通知."""
    direction = "做多" if side == "long" else "做空"
    send_message(
        f"🛑 *觸發止損*\n\n"
        f"💱 `{pair}` ({direction})\n"
        f"📉 虧損: `{loss_pct:.2f}%` (`{loss_usdt:.2f} USDT`)\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )


def notify_pyramid(pair: str, addon_num: int, addon_stake: float,
                   total_profit_pct: float, confidence: float):
    """金字塔加碼通知."""
    send_message(
        f"🔺 *金字塔加碼 #{addon_num}*\n\n"
        f"💱 交易對: `{pair}`\n"
        f"💰 加碼金額: `{addon_stake:.2f} USDT`\n"
        f"📊 當前利潤: `{total_profit_pct:+.2f}%`\n"
        f"🎯 信心指數: `{confidence:.2f}`\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )


def notify_daily_report(total_profit: float, win_count: int, loss_count: int,
                        open_trades: int, confidence: float, regime: str):
    """每日報告."""
    regime_zh = {
        "AGGRESSIVE": "🔥 積極",
        "NORMAL": "✅ 正常",
        "CAUTIOUS": "⚠️ 謹慎",
        "DEFENSIVE": "🛡️ 防禦",
        "HIBERNATE": "❄️ 休眠",
    }.get(regime, regime)

    send_message(
        f"📊 *每日交易報告*\n\n"
        f"💰 今日損益: `{total_profit:+.2f} USDT`\n"
        f"✅ 獲利: {win_count} 筆 | 🔴 虧損: {loss_count} 筆\n"
        f"📂 持倉中: {open_trades} 筆\n"
        f"🎯 信心指數: `{confidence:.2f}`\n"
        f"🌡️ 市場狀態: {regime_zh}\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d')}"
    )


def notify_confidence_change(old_regime: str, new_regime: str,
                             confidence: float, reason: str = ""):
    """信心狀態變化通知."""
    regime_zh = {
        "AGGRESSIVE": "🔥 積極",
        "NORMAL": "✅ 正常",
        "CAUTIOUS": "⚠️ 謹慎",
        "DEFENSIVE": "🛡️ 防禦",
        "HIBERNATE": "❄️ 休眠",
    }
    old_zh = regime_zh.get(old_regime, old_regime)
    new_zh = regime_zh.get(new_regime, new_regime)

    send_message(
        f"🔄 *市場狀態變化*\n\n"
        f"📊 {old_zh} → {new_zh}\n"
        f"🎯 信心指數: `{confidence:.2f}`\n"
        f"📝 {reason}\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )
