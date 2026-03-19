"""Telegram 互動式 AI 交易助手 — 豐富視覺化 + 固定底部選單。

特色:
- 固定底部按鈕 (ReplyKeyboardMarkup) — 不用輸入指令
- 進度條 + 趨勢箭頭 + 異常標記 — 資訊一目了然
- 結構化指令 (0 token) + 自由問答 (Claude AI)
"""

from __future__ import annotations

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

BOT_TOKEN = os.environ.get("TG_AI_BOT_TOKEN", "")
AUTHORIZED_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_CHAT_ID", "1481081110").split(",")]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_last_ai_call: float = 0
AI_COOLDOWN = 30

# =============================================
# 固定底部選單 (ReplyKeyboardMarkup)
# =============================================

PERSISTENT_MENU = {
    "keyboard": [
        ["📊 狀態", "🎯 信心", "🔗 加密環境"],
        ["📈 機制", "🌍 宏觀", "💰 交易"],
        ["🛡 風控", "🧠 決策", "📋 分析"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# 按鈕文字 → handler 映射
BUTTON_MAP: dict[str, str] = {
    "📊 狀態": "status",
    "🎯 信心": "confidence",
    "🔗 加密環境": "crypto",
    "📈 機制": "regime",
    "🌍 宏觀": "macro",
    "💰 交易": "trades",
    "🛡 風控": "guards",
    "🧠 決策": "decisions",
    "📋 分析": "analysis",
}

# =============================================
# 視覺化工具
# =============================================

def bar(value: float, max_val: float = 1.0, length: int = 10) -> str:
    filled = max(0, min(length, int(value / max_val * length)))
    return "▰" * filled + "▱" * (length - filled)


def arrow(change: float, threshold: float = 1.0) -> str:
    if change > threshold:
        return "↑"
    if change < -threshold:
        return "↓"
    return "→"


def alert(value: float, warn: float = 2.0, danger: float = 4.0) -> str:
    if abs(value) >= danger:
        return " 🚨"
    if abs(value) >= warn:
        return " ⚠"
    return ""


def regime_emoji(regime: str) -> str:
    return {
        "AGGRESSIVE": "🟢", "NORMAL": "🔵", "CAUTIOUS": "🟡",
        "DEFENSIVE": "🟠", "HIBERNATE": "🔴",
        "FAVORABLE": "🟢", "NEUTRAL": "🔵", "HOSTILE": "🔴",
        "TRENDING_BULL": "🟢", "TRENDING_BEAR": "🔴",
        "HIGH_VOLATILITY": "🟠", "ACCUMULATION": "🟡", "RANGING": "🔵",
    }.get(regime, "⚪")


def fg_label(value: int) -> str:
    if value <= 20:
        return "極度恐懼 😱"
    if value <= 40:
        return "恐懼 😨"
    if value <= 60:
        return "中性 😐"
    if value <= 80:
        return "貪婪 🤑"
    return "極度貪婪 🤯"


# =============================================
# 數據讀取
# =============================================

def read_snapshot() -> dict:
    try:
        with open(DATA_DIR / "market_snapshot.json") as f:
            return json.load(f)
    except Exception:
        return {}


def read_summary() -> str:
    try:
        return (DATA_DIR / "analysis_input.txt").read_text()
    except Exception:
        return "摘要尚未生成。"


def read_guard_state() -> dict:
    try:
        with open(DATA_DIR / "guard_state.json") as f:
            return json.load(f)
    except Exception:
        return {}


def query_ft(endpoint: str):
    for host in ["freqtrade:8080", "localhost:8080"]:
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


def snapshot_age() -> str:
    snap = read_snapshot()
    ts = snap.get("timestamp", "")
    if not ts:
        return "未知"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            return f"{mins}分鐘前"
        return f"{mins // 60}小時{mins % 60}分前"
    except Exception:
        return "未知"


# =============================================
# 指令回應 (豐富版)
# =============================================

def cmd_status() -> str:
    config = query_ft("show_config")
    positions = query_ft("status")
    profit = query_ft("profit")
    snap = read_snapshot()

    state = config.get("state", "offline").upper() if config else "OFFLINE"
    dry = config.get("dry_run", True) if config else True
    mode = "Dry Run" if dry else "LIVE 🔴"
    pos_list = positions if isinstance(positions, list) else []
    conf = snap.get("confidence", {})
    conf_score = conf.get("score", 0)
    conf_regime = conf.get("regime", "?")
    crypto = snap.get("crypto_env", {})
    btc_env = crypto.get("BTC", {}).get("score", 0) if crypto else 0

    lines = [
        "🤖 系統狀態",
        "━━━━━━━━━━━━━━━━",
        f"● {state} | SMCTrend | {mode}",
        f"⏰ 數據更新: {snapshot_age()}",
        "",
    ]

    # Positions
    if pos_list:
        lines.append(f"📈 持倉: {len(pos_list)} 個")
        for p in pos_list[:5]:
            pair = p.get("pair", "?").replace("/USDT:USDT", "")
            side = "Long" if not p.get("is_short") else "Short"
            lev = p.get("leverage", 1)
            entry = p.get("open_rate", 0)
            current = p.get("current_rate", entry)
            pnl_pct = p.get("profit_pct", 0)
            pnl_abs = p.get("profit_abs", 0)
            sl = p.get("stop_loss_abs", 0)
            icon = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(f"┌ {pair} {side} {lev:.1f}x")
            lines.append(f"│ 進場: ${entry:,.0f} → 現價: ${current:,.0f}")
            lines.append(f"│ {icon} 損益: {pnl_pct:+.2f}% (${pnl_abs:+.2f})")
            lines.append(f"└ 止損: ${sl:,.0f}")
    else:
        lines.append("📈 持倉: 0 個")

    # Performance
    lines.append("")
    if profit:
        trades = profit.get("trade_count", 0)
        pnl = profit.get("profit_all_coin", 0)
        wins = profit.get("winning_trades", 0)
        losses = profit.get("losing_trades", 0)
        wr = (wins / trades * 100) if trades > 0 else 0
        lines.append("💰 績效總覽")
        lines.append(f"├ 總交易: {trades} 筆 | 勝率: {wr:.0f}%")
        lines.append(f"├ 勝 {wins} / 敗 {losses}")
        lines.append(f"└ 總損益: ${pnl:.2f}")
    else:
        lines.append("💰 績效: 無數據")

    # Quick indicators
    lines.append("")
    lines.append(f"🎯 信心: {conf_score:.2f} ({conf_regime}) {bar(conf_score)}")
    lines.append(f"🔗 BTC: {btc_env:.2f} {bar(btc_env)}")

    return "\n".join(lines)


def cmd_confidence() -> str:
    snap = read_snapshot()
    conf = snap.get("confidence", {})
    if not conf:
        return "🎯 信心引擎數據尚未取得。"

    score = conf.get("score", 0)
    regime = conf.get("regime", "?")
    event = conf.get("event_multiplier", 1.0)
    sb = conf.get("sandboxes", {})

    lines = [
        "🎯 信心引擎",
        "━━━━━━━━━━━━━━━━",
        f"分數: {score:.4f} / 1.00  {regime_emoji(regime)} {regime}",
        f"{bar(score)} {score*100:.0f}%",
        "",
        "📊 沙盒分數",
        f"├ 宏觀  {sb.get('macro', 0):.2f} {bar(sb.get('macro', 0))}",
        f"├ 情緒  {sb.get('sentiment', 0):.2f} {bar(sb.get('sentiment', 0))}",
        f"├ 資本  {sb.get('capital', 0):.2f} {bar(sb.get('capital', 0))}",
        f"└ 避險  {sb.get('haven', 0):.2f} {bar(sb.get('haven', 0))}",
    ]

    if event < 1.0:
        lines.append(f"\n⚠ 事件乘數: x{event} (FOMC/CPI)")

    # Guidance
    guidance = {
        "AGGRESSIVE": ("100%", "3.0x"),
        "NORMAL": ("75%", "2.0x"),
        "CAUTIOUS": ("50%", "1.5x"),
        "DEFENSIVE": ("25%", "1.0x"),
        "HIBERNATE": ("0%", "0x"),
    }.get(regime, ("?", "?"))

    lines.append("")
    lines.append("💡 建議")
    lines.append(f"├ 倉位: {guidance[0]}")
    lines.append(f"└ 槓桿: {guidance[1]}")

    return "\n".join(lines)


def cmd_crypto() -> str:
    snap = read_snapshot()
    crypto = snap.get("crypto_env", {})
    if not crypto:
        return "🔗 加密環境數據尚未取得。"

    lines = ["🔗 加密環境引擎", "━━━━━━━━━━━━━━━━"]
    for sym in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]:
        env = crypto.get(sym, {})
        if not env or env.get("error"):
            continue
        score = env.get("score", 0)
        regime = env.get("regime", "?")
        sb = env.get("sandboxes", {})
        signals = env.get("signals", [])

        lines.append(f"\n{regime_emoji(regime)} {sym}  {score:.2f} {bar(score)} {regime}")
        if sb:
            lines.append(f"├ 衍生品 {sb.get('derivatives', 0):.2f} | 鏈上 {sb.get('onchain', 0):.2f} | 情緒 {sb.get('sentiment', 0):.2f}")
        if signals:
            for sig in signals[:2]:
                lines.append(f"└ ⚠ {sig}")

    return "\n".join(lines)


def cmd_regime() -> str:
    snap = read_snapshot()
    reg = snap.get("regime", {})
    if not reg:
        return "📈 市場機制數據尚未取得。"

    regime = reg.get("regime", "?")
    confidence = reg.get("confidence", 0)
    guidance = reg.get("guidance", {})
    factors = reg.get("factors", {})

    lines = [
        "📈 市場機制",
        "━━━━━━━━━━━━━━━━",
        f"{regime_emoji(regime)} {regime} ({confidence:.0%} 信心度)",
        f"{bar(confidence)} ",
        "",
        f"策略: {guidance.get('strategy', '?')}",
        f"風險: {guidance.get('risk_level', '?')}",
        f"槓桿上限: {guidance.get('leverage_cap', '?')}x",
        f"📝 {guidance.get('description', '')}",
    ]

    if factors:
        lines.append("")
        lines.append("📊 判斷依據")
        conf_f = factors.get("confidence", {})
        if isinstance(conf_f, dict):
            lines.append(f"├ 信心分數: {conf_f.get('score', '?')}")
        lines.append(f"├ VIX: {factors.get('vix', '?')}")
        lines.append(f"├ F&G: {factors.get('fear_greed', '?')}")
        lines.append(f"└ BTC Env: {factors.get('btc_env', '?')}")

    return "\n".join(lines)


def cmd_macro() -> str:
    snap = read_snapshot()
    macro = snap.get("macro", {})
    if not macro:
        return "🌍 宏觀指標: 無數據"

    lines = ["🌍 宏觀指標", "━━━━━━━━━━━━━━━━"]

    items = [
        ("VIX", "VIX", "", 25, 30),
        ("10Y", "10Y殖利率", "%", 0, 0),
        ("Gold", "黃金", "", 2, 4),
        ("Oil", "原油", "", 3, 5),
        ("DXY", "美元指數", "", 0, 0),
    ]
    for key, label, suffix, warn, danger in items:
        m = macro.get(key, {})
        if not m:
            continue
        price = m.get("price", 0)
        chg = m.get("change_pct", 0)
        a = ""
        if key == "VIX" and price > 25:
            a = " ⚠ 偏高" if price < 30 else " 🚨 恐慌"
        elif warn > 0:
            a = alert(chg, warn, danger)
        lines.append(f"{label:　<5} {price:>8.2f}{suffix}  ({chg:+.1f}%) {arrow(chg)}{a}")

    # Fear & Greed
    fg = macro.get("fear_greed")
    if fg is not None:
        lines.append("")
        lines.append(f"😨 Fear & Greed: {fg}/100 ({fg_label(fg)})")
        lines.append(f"{bar(fg, 100)}")

    # BTC Dominance
    btc_d = macro.get("btc_dominance")
    if btc_d:
        lines.append(f"\n₿ BTC.D: {btc_d}%")

    return "\n".join(lines)


def cmd_analysis() -> str:
    summary = read_summary()
    age = snapshot_age()
    if len(summary) > 3500:
        summary = summary[:3500] + "\n...(截斷)"
    return f"📋 最新分析 (數據: {age})\n━━━━━━━━━━━━━━━━\n{summary}"


def cmd_trades() -> str:
    result = query_ft("trades?limit=5")
    if not result or not result.get("trades"):
        return "💰 最近交易\n━━━━━━━━━━━━━━━━\n尚無交易記錄。"

    lines = ["💰 最近交易", "━━━━━━━━━━━━━━━━"]
    for t in result["trades"][:5]:
        pair = t.get("pair", "?").replace("/USDT:USDT", "")
        pnl = t.get("profit_pct", 0) or 0
        pnl_abs = t.get("profit_abs", 0) or 0
        reason = t.get("exit_reason", "open")
        side = "Short" if t.get("is_short") else "Long"
        lev = t.get("leverage", 1) or 1
        icon = "🟢" if pnl >= 0 else "🔴"
        dur = t.get("trade_duration", 0) or 0
        dur_str = f"{dur // 60}h{dur % 60}m" if dur else "?"

        lines.append(f"\n{icon} {pair} {side} {lev:.1f}x")
        lines.append(f"├ 損益: {pnl:+.2f}% (${pnl_abs:+.2f})")
        lines.append(f"├ 原因: {reason}")
        lines.append(f"└ 持倉: {dur_str}")

    return "\n".join(lines)


def cmd_guards() -> str:
    state = read_guard_state()
    lines = ["🛡 風控狀態", "━━━━━━━━━━━━━━━━"]

    daily = state.get("daily_loss", 0)
    streak = state.get("consec_streak", 0)
    paused = state.get("consec_paused_until", 0)

    lines.append(f"日損: ${daily:.2f} / 限額5%")
    lines.append(f"連虧: {streak} 筆 / 上限5筆")

    if paused > time.time():
        remaining = (paused - time.time()) / 3600
        lines.append(f"🔴 暫停中: 剩餘 {remaining:.1f}h")
    else:
        lines.append("🟢 狀態: 正常運行")

    cooldowns = state.get("cooldown_last_trade", {})
    if cooldowns:
        lines.append("")
        lines.append("⏱ 冷卻中")
        for pair, ts in cooldowns.items():
            elapsed = time.time() - ts
            if elapsed < 900:  # 15min cooldown
                lines.append(f"└ {pair.replace('/USDT:USDT', '')}: {int(900 - elapsed)}s")

    return "\n".join(lines)


def cmd_decisions() -> str:
    try:
        from agent.memory import AgentMemory
        memory = AgentMemory()
        decisions = memory.get_decisions(limit=5)
    except Exception:
        decisions = []

    if not decisions:
        return "🧠 決策記錄: 無數據"

    lines = ["🧠 最近決策", "━━━━━━━━━━━━━━━━"]
    for d in decisions:
        ts = datetime.fromtimestamp(d["timestamp"]).strftime("%m/%d %H:%M")
        action = d["action"][:35]
        conf = d["confidence"]
        regime = d.get("regime", "")
        reason = d.get("reason", "")[:60]
        lines.append(f"\n[{ts}] {action}")
        lines.append(f"├ 信心: {conf:.2f} {bar(conf, length=5)}")
        if regime:
            lines.append(f"├ 機制: {regime}")
        if reason:
            lines.append(f"└ {reason}")

    stats = memory.get_stats()
    lines.append(f"\n📊 統計: {stats['total_decisions']} 決策 | {stats['knowledge_entries']} 知識")

    return "\n".join(lines)


def cmd_help() -> str:
    return (
        "📱 chiMoney AI 交易助手\n"
        "━━━━━━━━━━━━━━━━\n"
        "點擊下方按鈕快速查詢\n\n"
        "📊 狀態 — 持倉+損益+系統\n"
        "🎯 信心 — 信心引擎分數\n"
        "🔗 加密環境 — 6幣種評估\n"
        "📈 機制 — 市場趨勢判斷\n"
        "🌍 宏觀 — VIX/黃金/原油\n"
        "💰 交易 — 最近交易記錄\n"
        "🛡 風控 — Guard狀態\n"
        "🧠 決策 — Agent決策歷史\n"
        "📋 分析 — 最新AI分析\n\n"
        "💬 也可直接打字問問題\n"
        "例如: 現在適合做多嗎？"
    )


COMMANDS = {
    "status": cmd_status, "start": cmd_help, "help": cmd_help,
    "confidence": cmd_confidence, "crypto": cmd_crypto,
    "regime": cmd_regime, "analysis": cmd_analysis,
    "trades": cmd_trades, "guards": cmd_guards,
    "decisions": cmd_decisions, "macro": cmd_macro, "menu": cmd_help,
}


# =============================================
# AI 自由問答
# =============================================

def handle_ai_query(question: str) -> str:
    global _last_ai_call

    if not ANTHROPIC_API_KEY:
        return "AI 功能未啟用 (缺少 API Key)"

    elapsed = time.time() - _last_ai_call
    if elapsed < AI_COOLDOWN:
        return f"AI 冷卻中，請等 {int(AI_COOLDOWN - elapsed)} 秒。"

    _last_ai_call = time.time()
    summary = read_summary()

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system="你是加密貨幣合約交易AI助手。用繁體中文簡潔回答，300字以內。基於數據客觀分析。",
            messages=[{"role": "user", "content": f"市場數據:\n{summary}\n\n問題: {question}"}],
        )
        text = response.content[0].text if response.content else "無法生成回答"
        tokens = f"({response.usage.input_tokens}in/{response.usage.output_tokens}out)"
        return f"🤖 AI 分析\n━━━━━━━━━━━━━━━━\n{text}\n\n{tokens}"
    except Exception as e:
        logger.error("AI query failed: %s", e)
        return f"AI 查詢失敗: {str(e)[:100]}"


# =============================================
# Telegram 通信
# =============================================

def send_reply(chat_id: int, text: str, with_menu: bool = True) -> None:
    if len(text) > 4000:
        text = text[:4000] + "\n...(截斷)"

    payload: dict = {"chat_id": chat_id, "text": text}
    if with_menu:
        payload["reply_markup"] = PERSISTENT_MENU

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("Reply failed: %s", e)


def setup_bot_commands() -> None:
    """設置 Telegram 底部命令選單 (輸入 / 時顯示)。"""
    commands = [
        {"command": "status", "description": "持倉 + 損益 + 狀態"},
        {"command": "confidence", "description": "信心引擎分數"},
        {"command": "crypto", "description": "加密環境 (6幣種)"},
        {"command": "regime", "description": "市場機制 + 建議"},
        {"command": "macro", "description": "宏觀指標"},
        {"command": "analysis", "description": "最新 AI 分析"},
        {"command": "trades", "description": "最近交易"},
        {"command": "guards", "description": "風控狀態"},
        {"command": "decisions", "description": "Agent 決策"},
        {"command": "help", "description": "指令清單"},
    ]
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands"
        data = json.dumps({"commands": commands}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        logger.info("Bot commands registered")
    except Exception as e:
        logger.warning("setMyCommands failed: %s", e)


def process_message(chat_id: int, text: str) -> str:
    text = text.strip()

    # 按鈕文字匹配
    if text in BUTTON_MAP:
        cmd = BUTTON_MAP[text]
        handler = COMMANDS.get(cmd)
        if handler:
            try:
                return handler()
            except Exception as e:
                return f"執行失敗: {e}"

    # /指令
    if text.startswith("/"):
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        handler = COMMANDS.get(cmd)
        if handler:
            try:
                return handler()
            except Exception as e:
                return f"指令失敗: {e}"
        return f"未知指令: /{cmd}"

    # 自由文字 → AI
    return handle_ai_query(text)


def run_polling():
    if not BOT_TOKEN:
        logger.error("TG_AI_BOT_TOKEN not set!")
        return

    logger.info("Telegram AI Bot starting...")
    setup_bot_commands()

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
            with urllib.request.urlopen(urllib.request.Request(url), timeout=35) as resp:
                data = json.loads(resp.read())

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")

                if not chat_id or not text:
                    continue
                if chat_id not in AUTHORIZED_CHAT_IDS:
                    continue

                logger.info("Msg from %s: %s", chat_id, text[:40])
                response = process_message(chat_id, text)
                send_reply(chat_id, response)

        except Exception as e:
            logger.error("Polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
