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
                 leverage: float, confidence: float, reason: str = "",
                 details: dict | None = None):
    """進場通知 — 包含完整進場原因分析."""
    direction = "📈 做多" if side == "long" else "📉 做空"
    conf_emoji = "🔥" if confidence >= 0.8 else "✅" if confidence >= 0.6 else "⚠️"

    msg = (
        f"{direction} *進場通知*\n\n"
        f"💱 交易對: `{pair}`\n"
        f"💲 進場價: `{rate:,.2f}`\n"
        f"💰 倉位: `{stake:,.2f} USDT`\n"
        f"⚡ 槓桿: `{leverage:.1f}x`\n"
    )

    # 詳細進場原因
    if details:
        msg += "\n📊 *進場原因:*\n"
        if details.get("htf_trend"):
            trend_zh = "多頭 BOS" if details["htf_trend"] > 0 else "空頭 BOS"
            htf_label = details.get("htf_label", "4H")
            msg += f"  ✅ {htf_label} 趨勢: {trend_zh}\n"
        if details.get("in_ob"):
            msg += f"  ✅ Order Block: {details.get('ob_range', '活躍區間')}\n"
        if details.get("in_fvg"):
            msg += f"  ✅ Fair Value Gap: {details.get('fvg_range', '活躍區間')}\n"
        if details.get("confluence"):
            msg += "  🔥 OB+FVG 匯合（Grade A）\n"
        if details.get("in_ote"):
            msg += "  ✅ OTE 折扣/溢價區\n"
        if details.get("adam_bullish") is not None:
            adam_dir = "向上" if details["adam_bullish"] else "向下"
            slope = details.get("adam_slope", 0)
            msg += f"  ✅ 亞當投影: {adam_dir} (slope {slope:+.3f})\n"
        if details.get("in_killzone"):
            hour = details.get("utc_hour", 0)
            if 7 <= hour <= 10:
                kz = "倫敦開盤"
            elif 12 <= hour <= 14:
                kz = "紐約開盤"
            elif 15 <= hour <= 17:
                kz = "倫敦收盤"
            else:
                kz = f"UTC {hour}:00"
            msg += f"  ✅ Killzone: {kz}\n"
        if details.get("htf_zone_aligned"):
            htf_label = details.get("htf_label", "4H")
            msg += f"  ✅ {htf_label} OB/FVG 區域對齊\n"

    # 信心引擎分解
    msg += f"\n{conf_emoji} *信心引擎:* `{confidence:.2f}`\n"
    if details and details.get("confidence_factors"):
        cf = details["confidence_factors"]
        msg += (
            f"  動量: {cf.get('momentum', 0):.2f} | "
            f"趨勢: {cf.get('trend', 0):.2f} | "
            f"量能: {cf.get('volume', 0):.2f}\n"
            f"  波動: {cf.get('volatility', 0):.2f} | "
            f"健康: {cf.get('health', 0):.2f}\n"
        )

    # 缺失數據源警告
    if details and details.get("missing_sources"):
        msg += "\n⚠️ *缺失數據源:*\n"
        for src in details["missing_sources"]:
            msg += f"  ❌ {src}\n"

    msg += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    send_message(msg)


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
