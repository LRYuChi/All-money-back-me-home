"""Telegram 互動式 AI 交易助手 — 手機端查詢系統狀態。

結構化指令 (0 token): /status, /confidence, /crypto, /regime, /trades, /guards...
自由文字 (~2500 tokens): Claude Sonnet 分析回答

需要獨立的 Bot Token (不與 Freqtrade 共用)。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("telegram_bot")

# Config
BOT_TOKEN = os.environ.get("TG_AI_BOT_TOKEN", "")
AUTHORIZED_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_CHAT_ID", "1481081110").split(",")]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")

# Rate limit: max 1 AI query per 30 seconds
_last_ai_call: float = 0
AI_COOLDOWN = 30


# =============================================
# Authorization
# =============================================

def is_authorized(chat_id: int) -> bool:
    return chat_id in AUTHORIZED_CHAT_IDS


# =============================================
# Data Readers (0 token — pure Python)
# =============================================

def read_snapshot() -> dict:
    """Read latest market snapshot."""
    try:
        with open(DATA_DIR / "market_snapshot.json") as f:
            return json.load(f)
    except Exception:
        return {}


def read_summary() -> str:
    """Read latest analysis summary."""
    try:
        return (DATA_DIR / "analysis_input.txt").read_text()
    except Exception:
        return "摘要尚未生成。請等待下次 Pipeline 執行。"


def read_guard_state() -> dict:
    """Read guard pipeline state."""
    try:
        with open(DATA_DIR / "guard_state.json") as f:
            return json.load(f)
    except Exception:
        return {}


def query_freqtrade(endpoint: str) -> dict | list | None:
    """Query Freqtrade REST API."""
    hosts = ["freqtrade:8080", "localhost:8080"]
    for host in hosts:
        try:
            auth = base64.b64encode(b"freqtrade:freqtrade").decode()
            req = urllib.request.Request(
                f"http://{host}/api/v1/{endpoint}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            continue
    return None


def query_decisions(limit: int = 5) -> list[dict]:
    """Query agent decision memory."""
    try:
        from agent.memory import AgentMemory
        memory = AgentMemory()
        return memory.get_decisions(limit=limit)
    except Exception:
        return []


# =============================================
# Command Handlers (0 token)
# =============================================

def cmd_status() -> str:
    """持倉 + 損益 + bot 狀態"""
    config = query_freqtrade("show_config")
    positions = query_freqtrade("status")
    profit = query_freqtrade("profit")
    snapshot = read_snapshot()

    state = config.get("state", "?") if config else "offline"
    dry = config.get("dry_run", True) if config else True
    pos_list = positions if isinstance(positions, list) else []
    conf = snapshot.get("confidence", {})

    lines = [
        "🤖 系統狀態",
        "━━━━━━━━━━",
        f"● {state.upper()} | SMCTrend | {'Dry Run' if dry else 'LIVE'}",
        f"持倉: {len(pos_list)} 個",
    ]

    if profit:
        pnl = profit.get("profit_all_coin", 0)
        trades = profit.get("trade_count", 0)
        lines.append(f"損益: ${pnl:.2f} | 交易: {trades} 筆")

    if conf:
        lines.append(f"信心: {conf.get('score', '?')} ({conf.get('regime', '?')})")

    # Open positions detail
    for p in pos_list[:3]:
        pair = p.get("pair", "?")
        pnl_pct = p.get("profit_pct", 0)
        lines.append(f"  {pair}: {pnl_pct:+.2f}%")

    return "\n".join(lines)


def cmd_confidence() -> str:
    """信心引擎詳細分數"""
    snapshot = read_snapshot()
    conf = snapshot.get("confidence", {})
    if not conf:
        return "信心引擎數據尚未取得。"

    sb = conf.get("sandboxes", {})
    event = conf.get("event_multiplier", 1.0)

    lines = [
        f"🎯 信心引擎 {conf.get('score', '?')} ({conf.get('regime', '?')})",
        "━━━━━━━━━━",
        f"宏觀: {sb.get('macro', '?')} | 情緒: {sb.get('sentiment', '?')}",
        f"資本: {sb.get('capital', '?')} | 避險: {sb.get('haven', '?')}",
    ]
    if event < 1.0:
        lines.append(f"事件乘數: x{event} (FOMC/CPI)")

    return "\n".join(lines)


def cmd_crypto() -> str:
    """6 幣種 Crypto 環境分數"""
    snapshot = read_snapshot()
    crypto = snapshot.get("crypto_env", {})
    if not crypto:
        return "Crypto 環境數據尚未取得。"

    lines = ["🔗 加密環境引擎", "━━━━━━━━━━"]
    for sym in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]:
        env = crypto.get(sym, {})
        if env:
            score = env.get("score", "?")
            regime = env.get("regime", "?")
            signals = env.get("signals", [])
            sig = f" | {', '.join(signals)}" if signals else ""
            lines.append(f"  {sym}: {score} ({regime}){sig}")

    return "\n".join(lines)


def cmd_regime() -> str:
    """當前市場機制"""
    snapshot = read_snapshot()
    reg = snapshot.get("regime", {})
    if not reg:
        return "市場機制數據尚未取得。"

    guidance = reg.get("guidance", {})
    lines = [
        f"📊 市場機制: {reg.get('regime', '?')}",
        f"信心度: {reg.get('confidence', 0):.0%}",
        "━━━━━━━━━━",
        f"策略: {guidance.get('strategy', '?')}",
        f"風險: {guidance.get('risk_level', '?')}",
        f"槓桿上限: {guidance.get('leverage_cap', '?')}x",
        f"{guidance.get('description', '')}",
    ]
    return "\n".join(lines)


def cmd_analysis() -> str:
    """最新 AI 分析報告"""
    summary = read_summary()
    if len(summary) > 3500:
        summary = summary[:3500] + "\n...(截斷)"
    return f"📋 最新分析\n━━━━━━━━━━\n{summary}"


def cmd_trades() -> str:
    """最近交易"""
    trades = query_freqtrade("trades?limit=5")
    if not trades or not trades.get("trades"):
        return "💰 最近交易\n━━━━━━━━━━\n尚無交易記錄。"

    lines = ["💰 最近交易", "━━━━━━━━━━"]
    for t in trades["trades"][:5]:
        pair = t.get("pair", "?")
        pnl = t.get("profit_pct", 0)
        reason = t.get("exit_reason", "?")
        lines.append(f"  {pair}: {pnl:+.2f}% ({reason})")
    return "\n".join(lines)


def cmd_guards() -> str:
    """Guard 風控狀態"""
    state = read_guard_state()
    if not state:
        return "🛡 Guard 狀態: 無數據 (等待首筆交易)"

    lines = [
        "🛡 Guard 風控狀態",
        "━━━━━━━━━━",
        f"日損: ${state.get('daily_loss', 0):.2f}",
        f"連虧: {state.get('consec_streak', 0)} 筆",
    ]
    paused = state.get("consec_paused_until", 0)
    if paused > time.time():
        remaining = (paused - time.time()) / 3600
        lines.append(f"暫停中: 剩餘 {remaining:.1f}h")
    else:
        lines.append("狀態: 正常運行")

    return "\n".join(lines)


def cmd_decisions() -> str:
    """最近 Agent 決策"""
    decisions = query_decisions(5)
    if not decisions:
        return "🧠 決策記錄: 無數據"

    lines = ["🧠 最近決策", "━━━━━━━━━━"]
    for d in decisions:
        ts = datetime.fromtimestamp(d["timestamp"]).strftime("%m/%d %H:%M")
        action = d["action"][:30]
        conf = d["confidence"]
        lines.append(f"  [{ts}] {action} ({conf:.2f})")

    return "\n".join(lines)


def cmd_macro() -> str:
    """宏觀指標"""
    snapshot = read_snapshot()
    macro = snapshot.get("macro", {})
    if not macro:
        return "🌍 宏觀指標: 無數據"

    lines = ["🌍 宏觀指標", "━━━━━━━━━━"]
    for key, label in [("VIX", "VIX"), ("10Y", "10Y殖利率"), ("Gold", "黃金"), ("Oil", "原油"), ("DXY", "美元指數")]:
        m = macro.get(key, {})
        if m:
            lines.append(f"  {label}: {m.get('price', '?')} ({m.get('change_pct', 0):+.2f}%)")

    fg = macro.get("fear_greed")
    if fg is not None:
        lines.append(f"  F&G: {fg}/100")

    btc_d = macro.get("btc_dominance")
    if btc_d:
        lines.append(f"  BTC.D: {btc_d}%")

    return "\n".join(lines)


def cmd_help() -> str:
    """指令清單 (純文字版，按鈕版用 cmd_menu)"""
    return (
        "📱 交易 AI 助手\n"
        "━━━━━━━━━━━━━━━━\n"
        "點擊下方按鈕或輸入指令：\n\n"
        "💬 也可直接打字問問題\n"
        "例如: 黃金暴跌對 BTC 有影響嗎？"
    )


# Command registry
COMMANDS = {
    "status": cmd_status,
    "start": cmd_help,
    "help": cmd_help,
    "confidence": cmd_confidence,
    "crypto": cmd_crypto,
    "regime": cmd_regime,
    "analysis": cmd_analysis,
    "trades": cmd_trades,
    "guards": cmd_guards,
    "decisions": cmd_decisions,
    "macro": cmd_macro,
}


# =============================================
# AI Free Text Handler (~2500 tokens)
# =============================================

def handle_ai_query(question: str) -> str:
    """Use Claude to answer a free-form question with market context."""
    global _last_ai_call

    if not ANTHROPIC_API_KEY:
        return "AI 功能未啟用 (缺少 API Key)"

    # Rate limit
    elapsed = time.time() - _last_ai_call
    if elapsed < AI_COOLDOWN:
        remaining = int(AI_COOLDOWN - elapsed)
        return f"AI 查詢冷卻中，請等 {remaining} 秒後再試。"

    _last_ai_call = time.time()

    # Read context
    summary = read_summary()

    try:
        import anthropic
        client = anthropic.Anthropic()

        system = (
            "你是加密貨幣合約交易 AI 助手。用繁體中文簡潔回答。"
            "你有完整的市場數據，基於數據做客觀分析。"
            "回答控制在 300 字以內。"
        )
        user_msg = f"市場數據:\n{summary}\n\n用戶問題: {question}"

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )

        text = response.content[0].text if response.content else "無法生成回答"
        tokens = f"({response.usage.input_tokens}in/{response.usage.output_tokens}out)"
        return f"🤖 AI 分析\n━━━━━━━━━━\n{text}\n\n{tokens}"

    except Exception as e:
        logger.error("AI query failed: %s", e)
        return f"AI 查詢失敗: {str(e)[:100]}"


# =============================================
# Telegram Bot (polling mode)
# =============================================

def send_reply(chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> None:
    """Send a reply with optional inline keyboard buttons."""
    if len(text) > 4000:
        text = text[:4000] + "\n...(截斷)"

    payload: dict = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("Reply failed: %s", e)


# Main menu button layout
MAIN_MENU_BUTTONS = [
    [
        {"text": "📊 狀態", "callback_data": "cmd_status"},
        {"text": "🎯 信心", "callback_data": "cmd_confidence"},
        {"text": "🔗 加密環境", "callback_data": "cmd_crypto"},
    ],
    [
        {"text": "📈 機制", "callback_data": "cmd_regime"},
        {"text": "🌍 宏觀", "callback_data": "cmd_macro"},
        {"text": "💰 交易", "callback_data": "cmd_trades"},
    ],
    [
        {"text": "🛡 風控", "callback_data": "cmd_guards"},
        {"text": "🧠 決策", "callback_data": "cmd_decisions"},
        {"text": "📋 分析", "callback_data": "cmd_analysis"},
    ],
    [
        {"text": "🔄 刷新主選單", "callback_data": "cmd_menu"},
    ],
]

# Callback → command mapping
CALLBACK_MAP = {
    "cmd_status": cmd_status,
    "cmd_confidence": cmd_confidence,
    "cmd_crypto": cmd_crypto,
    "cmd_regime": cmd_regime,
    "cmd_macro": cmd_macro,
    "cmd_trades": cmd_trades,
    "cmd_guards": cmd_guards,
    "cmd_decisions": cmd_decisions,
    "cmd_analysis": cmd_analysis,
}


def answer_callback(callback_query_id: str) -> None:
    """Answer callback query to remove loading indicator."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        data = json.dumps({"callback_query_id": callback_query_id}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def process_message(chat_id: int, text: str) -> tuple[str, list | None]:
    """Process an incoming message. Returns (response_text, buttons_or_None)."""
    text = text.strip()

    # Command
    if text.startswith("/"):
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()

        # /start and /help → show button menu
        if cmd in ("start", "help", "menu"):
            return cmd_help(), MAIN_MENU_BUTTONS

        handler = COMMANDS.get(cmd)
        if handler:
            try:
                return handler(), None
            except Exception as e:
                logger.error("Command %s failed: %s", cmd, e)
                return f"指令執行失敗: {e}", None
        return f"未知指令: /{cmd}\n輸入 /help 查看可用指令", None

    # Free text → AI
    return handle_ai_query(text), None


def run_polling():
    """Run Telegram bot in long-polling mode."""
    if not BOT_TOKEN:
        logger.error("TG_AI_BOT_TOKEN not set!")
        return

    logger.info("Telegram AI Bot starting (polling mode)...")
    logger.info("Authorized chat IDs: %s", AUTHORIZED_CHAT_IDS)

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())

            if not data.get("ok"):
                logger.warning("getUpdates failed: %s", data)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                # --- Handle callback queries (button presses) ---
                callback = update.get("callback_query")
                if callback:
                    cb_chat_id = callback.get("message", {}).get("chat", {}).get("id")
                    cb_data = callback.get("data", "")
                    cb_id = callback.get("id")

                    if cb_chat_id and is_authorized(cb_chat_id):
                        logger.info("Button press from %s: %s", cb_chat_id, cb_data)
                        answer_callback(cb_id)

                        if cb_data == "cmd_menu":
                            send_reply(cb_chat_id, cmd_help(), MAIN_MENU_BUTTONS)
                        elif cb_data in CALLBACK_MAP:
                            try:
                                result = CALLBACK_MAP[cb_data]()
                                send_reply(cb_chat_id, result)
                            except Exception as e:
                                send_reply(cb_chat_id, f"執行失敗: {e}")
                    continue

                # --- Handle text messages ---
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")

                if not chat_id or not text:
                    continue

                if not is_authorized(chat_id):
                    logger.warning("Unauthorized: chat_id=%s", chat_id)
                    continue

                logger.info("Message from %s: %s", chat_id, text[:50])
                response, buttons = process_message(chat_id, text)
                send_reply(chat_id, response, buttons)

        except Exception as e:
            logger.error("Polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
