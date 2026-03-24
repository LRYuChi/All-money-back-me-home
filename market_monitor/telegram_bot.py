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
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("telegram_bot")

BOT_TOKEN = os.environ.get("TG_AI_BOT_TOKEN", "")
_raw_ids = os.environ.get("TELEGRAM_CHAT_ID", "")
if not _raw_ids:
    logger.error("TELEGRAM_CHAT_ID 未設定，Telegram Bot 無法啟動")
    AUTHORIZED_CHAT_IDS = []
else:
    AUTHORIZED_CHAT_IDS = [int(x.strip()) for x in _raw_ids.split(",") if x.strip()]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_last_ai_call: float = 0
AI_COOLDOWN = 30

# =============================================
# 固定底部選單 (ReplyKeyboardMarkup)
# =============================================

PERSISTENT_MENU = {
    "keyboard": [
        ["📊 全覽", "📋 持倉", "📋 分析"],
        ["🎯 信心", "🔗 加密環境", "📈 機制"],
        ["💰 交易", "📊 統計", "📓 日誌"],
        ["🛡 風控", "🌍 宏觀", "🧠 決策"],
        ["🤖 AI回顧", "🔮 AI預測", "⚠️ AI風控"],
        ["🇹🇼 台股預測", "🏦 台股籌碼", "📉 台股技術"],
        ["🧠 台股ML", "🧠 BTC ML"],
        ["📰 投顧報告"],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# 按鈕文字 → handler 映射
BUTTON_MAP: dict[str, str] = {
    "📊 全覽": "overview",
    "📋 持倉": "positions",
    "📋 分析": "analysis",
    "🎯 信心": "confidence",
    "🔗 加密環境": "crypto",
    "📈 機制": "regime",
    "💰 交易": "trades",
    "📊 統計": "trade_stats",
    "📓 日誌": "journal",
    "🛡 風控": "guards",
    "🌍 宏觀": "macro",
    "🧠 決策": "decisions",
    "🤖 AI回顧": "ai_review",
    "🔮 AI預測": "ai_forecast",
    "⚠️ AI風控": "ai_risk",
    "📰 投顧報告": "advisor_report",
    "🇹🇼 台股預測": "tw_predict",
    "🏦 台股籌碼": "tw_chips",
    "📉 台股技術": "tw_tech",
    "🧠 台股ML": "tw_ml",
    "🧠 BTC ML": "btc_ml",
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
# 數據讀取 (自動刷新)
# =============================================

SNAPSHOT_MAX_AGE = 600  # 10 minutes — auto-refresh if older
_last_refresh: float = 0
REFRESH_COOLDOWN = 120  # Don't refresh more than once per 2 min


def _auto_refresh_snapshot() -> None:
    """If snapshot is stale (>10min), trigger data collection."""
    global _last_refresh
    now = time.time()
    if now - _last_refresh < REFRESH_COOLDOWN:
        return

    snap_file = DATA_DIR / "market_snapshot.json"
    try:
        if snap_file.exists():
            mtime = snap_file.stat().st_mtime
            if now - mtime < SNAPSHOT_MAX_AGE:
                return  # Fresh enough
    except Exception:
        pass

    # Stale — refresh
    _last_refresh = now
    logger.info("Snapshot stale, refreshing...")
    try:
        from agent.data_collector import collect_all
        from agent.summarizer import run as run_summarizer
        collect_all()
        run_summarizer()
        logger.info("Snapshot refreshed")
    except Exception as e:
        logger.warning("Auto-refresh failed: %s", e)


def read_snapshot() -> dict:
    _auto_refresh_snapshot()
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
            _ft_creds = f"{os.environ.get('FT_USER', 'freqtrade')}:{os.environ.get('FT_PASS', 'freqtrade')}"
            auth = base64.b64encode(_ft_creds.encode()).decode()
            req = urllib.request.Request(
                f"http://{host}/api/v1/{endpoint}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.debug("Freqtrade API %s/%s failed: %s", host, endpoint, e)
            continue
    logger.warning("Freqtrade API unreachable for /%s (tried all hosts)", endpoint)
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

def _next_analysis_countdown() -> str:
    """計算下次分析的倒計時。"""
    now = datetime.now(timezone.utc)
    hours = [0, 8, 16]
    for h in hours:
        target = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if target > now:
            delta = target - now
            hrs = int(delta.total_seconds() // 3600)
            mins = int((delta.total_seconds() % 3600) // 60)
            return f"{hrs}h{mins}m"
    # Next day 00:00
    tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    delta = tomorrow - now
    hrs = int(delta.total_seconds() // 3600)
    return f"{hrs}h"


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
    macro = snap.get("macro", {})
    fg = macro.get("fear_greed", "?")
    regime = snap.get("regime", {}).get("regime", "?")
    guards = read_guard_state()
    streak = guards.get("consecutive_losses", guards.get("consec_streak", 0))
    guard_status = "🟢 正常" if streak < 3 else f"🟠 連虧{streak}"

    lines = [
        "🤖 系統狀態",
        "━━━━━━━━━━━━━━━━",
        f"● {state} | SMCTrend | {mode}",
        f"⏰ 數據: {snapshot_age()} | 下次分析: {_next_analysis_countdown()}",
    ]

    # BTC price from macro
    btc_data = next((c for c in snap.get("freqtrade", {}).get("positions", []) if "BTC" in c.get("pair", "")), None)
    if not btc_data:
        # Try regime factors
        btc_chg = snap.get("regime", {}).get("factors", {}).get("btc", {}).get("change_pct", 0)
        lines.append(f"\n💲 BTC ({btc_chg:+.1f}%)")

    lines.append("━━━━━━━━━━━━━━━━")

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
        wr = (wins / trades * 100) if trades > 0 else 0
        lines.append("💰 績效")
        lines.append(f"├ 交易: {trades} 筆 | 勝率: {wr:.0f}%")
        lines.append(f"└ 損益: ${pnl:.2f}")

    # Quick dashboard
    lines.append("")
    lines.append("📊 快速總覽")
    lines.append(f"├ 🎯 信心  {conf_score:.2f} {bar(conf_score)} {regime_emoji(conf_regime)} {conf_regime}")
    lines.append(f"├ 🔗 BTC   {btc_env:.2f} {bar(btc_env)}")
    lines.append(f"├ 📈 機制  {regime} {regime_emoji(regime)}")
    if isinstance(fg, (int, float)):
        lines.append(f"├ 😨 F&G   {fg} {bar(fg, 100)} {fg_label(fg)}")
    lines.append(f"└ 🛡 Guard {guard_status}")

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

    def sb_status(val):
        return "✅" if val >= 0.4 else "⚠"

    lines = [
        "🎯 信心引擎",
        "━━━━━━━━━━━━━━━━",
        f"分數: {score:.4f} / 1.00  {regime_emoji(regime)} {regime}",
        f"{bar(score)} {score*100:.0f}%",
        "",
        "📊 沙盒分數",
        f"├ 宏觀  {sb.get('macro', 0):.2f} {bar(sb.get('macro', 0))} {sb_status(sb.get('macro', 0))}",
        f"├ 情緒  {sb.get('sentiment', 0):.2f} {bar(sb.get('sentiment', 0))} {sb_status(sb.get('sentiment', 0))}",
        f"├ 資本  {sb.get('capital', 0):.2f} {bar(sb.get('capital', 0))} {sb_status(sb.get('capital', 0))}",
        f"└ 避險  {sb.get('haven', 0):.2f} {bar(sb.get('haven', 0))} {sb_status(sb.get('haven', 0))}",
    ]

    if event < 1.0:
        lines.append(f"\n⚠ 事件乘數: x{event} (FOMC/CPI)")

    # Regime thresholds
    thresholds = [
        ("🟢", "AGGRESSIVE", 0.80),
        ("🔵", "NORMAL", 0.60),
        ("🟡", "CAUTIOUS", 0.40),
        ("🟠", "DEFENSIVE", 0.20),
        ("🔴", "HIBERNATE", 0.00),
    ]
    lines.append("")
    lines.append("📈 制度閾值")
    for emoji, name, thresh in thresholds:
        pointer = " ← 目前" if name == regime else ""
        op = ">" if thresh > 0 else "<"
        lines.append(f"├ {emoji} {name:11s} {op} {thresh:.2f}{pointer}")

    # Guidance
    guidance = {
        "AGGRESSIVE": ("100%", "3.0x", "全力出擊"),
        "NORMAL": ("75%", "2.0x", "正常操作"),
        "CAUTIOUS": ("50%", "1.5x", "謹慎交易"),
        "DEFENSIVE": ("25%", "1.0x", "減少曝險"),
        "HIBERNATE": ("0%", "0x", "全面暫停"),
    }.get(regime, ("?", "?", ""))

    lines.append("")
    lines.append("💡 建議")
    lines.append(f"├ 倉位: {guidance[0]} | 槓桿: {guidance[1]}")
    lines.append(f"└ {guidance[2]}")

    # What needs to improve
    weak = []
    if sb.get("macro", 1) < 0.4:
        weak.append("宏觀")
    if sb.get("sentiment", 1) < 0.4:
        weak.append("情緒")
    if weak:
        lines.append(f"\n需要 {'、'.join(weak)} 改善才能升級制度")

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
    """結構化分析儀表板 — 整合所有關鍵資訊。"""
    snap = read_snapshot()
    if not snap:
        return "📋 分析數據尚未生成。請等待 Pipeline 執行。"

    conf = snap.get("confidence", {})
    score = conf.get("score", 0)
    regime = conf.get("regime", "?")
    event = conf.get("event_multiplier", 1.0)
    sb = conf.get("sandboxes", {})
    reg = snap.get("regime", {})
    macro = snap.get("macro", {})
    crypto = snap.get("crypto_env", {})
    ft = snap.get("freqtrade", {})
    ts = snap.get("timestamp", "?")[:19]

    lines = [
        "📋 市場分析儀表板",
        "━━━━━━━━━━━━━━━━",
        f"⏰ {ts} UTC",
        "",
    ]

    # === Confidence ===
    lines.append(f"🎯 信心引擎: {score:.2f} {regime_emoji(regime)} {regime}")
    lines.append(f"{bar(score)} {score*100:.0f}%")
    lines.append(f"├ 宏觀 {sb.get('macro', 0):.2f} {bar(sb.get('macro', 0))}")
    lines.append(f"├ 情緒 {sb.get('sentiment', 0):.2f} {bar(sb.get('sentiment', 0))}")
    lines.append(f"├ 資本 {sb.get('capital', 0):.2f} {bar(sb.get('capital', 0))}")
    lines.append(f"└ 避險 {sb.get('haven', 0):.2f} {bar(sb.get('haven', 0))}")
    if event < 1.0:
        lines.append(f"⚠ 事件乘數 x{event} (FOMC/CPI)")

    # === Regime ===
    reg_name = reg.get("regime", "?")
    reg_guidance = reg.get("guidance", {})
    lines.append("")
    lines.append(f"📈 機制: {reg_name} {regime_emoji(reg_name)} ({reg.get('confidence', 0):.0%})")
    lines.append(f"建議: {reg_guidance.get('description', '?')}")

    # === Macro ===
    lines.append("")
    lines.append("🌍 宏觀環境")
    for key, label in [("VIX", "VIX"), ("10Y", "10Y"), ("Gold", "Gold"), ("Oil", "Oil"), ("DXY", "DXY")]:
        m = macro.get(key, {})
        if m:
            chg = m.get("change_pct", 0)
            a = ""
            if key == "VIX" and m.get("price", 0) > 25:
                a = " ⚠" if m["price"] < 30 else " 🚨"
            elif abs(chg) > 3:
                a = " 🚨"
            elif abs(chg) > 2:
                a = " ⚠"
            lines.append(f"├ {label:4s} {m.get('price', 0):>8.1f} ({chg:+.1f}%) {arrow(chg)}{a}")

    fg = macro.get("fear_greed")
    if fg is not None:
        lines.append(f"├ F&G  {fg}/100 {fg_label(fg)}")
    btc_d = macro.get("btc_dominance")
    if btc_d:
        lines.append(f"└ BTC.D {btc_d}%")

    # === Crypto Env ===
    lines.append("")
    lines.append("🔗 加密環境")
    main_coins = ["BTC", "ETH", "SOL"]
    alt_coins = ["BNB", "XRP", "DOGE"]
    for sym in main_coins:
        env = crypto.get(sym, {})
        if env and not env.get("error"):
            s = env.get("score", 0)
            r = env.get("regime", "?")
            sbs = env.get("sandboxes", {})
            signals = env.get("signals", [])
            lines.append(f"├ {sym:4s} {s:.2f} {bar(s)} {r}")
            if sbs:
                lines.append(f"│  D:{sbs.get('derivatives',0):.2f} C:{sbs.get('onchain',0):.2f} S:{sbs.get('sentiment',0):.2f}")
            for sig in signals[:1]:
                if sig not in ("neutral", "stable"):
                    lines.append(f"│  ⚠ {sig}")

    # Alt coins compact
    alt_scores = []
    for sym in alt_coins:
        env = crypto.get(sym, {})
        if env and not env.get("error"):
            alt_scores.append(f"{sym} {env.get('score', 0):.2f}")
    if alt_scores:
        lines.append(f"└ {' | '.join(alt_scores)}")

    # === Anomalies ===
    anomalies = []
    gold = macro.get("Gold", {})
    if gold and abs(gold.get("change_pct", 0)) > 3:
        anomalies.append(f"🚨 黃金 {gold['change_pct']:+.1f}%")
    vix = macro.get("VIX", {})
    if vix and vix.get("price", 0) > 28:
        anomalies.append(f"🚨 VIX {vix['price']:.1f} 恐慌")
    elif vix and vix.get("price", 0) > 25:
        anomalies.append(f"⚠ VIX {vix['price']:.1f} 偏高")
    if isinstance(fg, (int, float)) and fg <= 20:
        anomalies.append(f"⚠ F&G {fg} 極度恐懼")
    if event < 1.0:
        anomalies.append("⚠ FOMC/CPI 事件期")
    for sym in ["BTC", "ETH", "SOL"]:
        env = crypto.get(sym, {})
        if env.get("score", 1) < 0.3:
            anomalies.append(f"🚨 {sym} Env HOSTILE")

    if anomalies:
        lines.append("")
        lines.append("⚠ 異常警示")
        for a in anomalies:
            lines.append(f"├ {a}")

    # === Action ===
    guidance_map = {
        "AGGRESSIVE": ("100%", "3.0x", "全力出擊，跟隨趨勢"),
        "NORMAL": ("75%", "2.0x", "正常操作"),
        "CAUTIOUS": ("50%", "1.5x", "謹慎，減小倉位"),
        "DEFENSIVE": ("25%", "1.0x", "防禦，只做高品質信號"),
        "HIBERNATE": ("0%", "0x", "全面暫停，等待環境改善"),
    }
    g = guidance_map.get(regime, ("?", "?", "觀望"))
    lines.append("")
    lines.append("💡 操作建議")
    lines.append(f"├ 倉位: {g[0]} | 槓桿: {g[1]}")
    lines.append(f"└ {g[2]}")

    # Positions
    pos = ft.get("positions", [])
    if pos:
        lines.append(f"\n📈 當前持倉: {len(pos)} 個")

    return "\n".join(lines)


def cmd_trades() -> str:
    """完整交易紀錄 — 最近 20 筆含統計."""
    result = query_ft("trades?limit=20")
    if not result or not result.get("trades"):
        return "💰 交易紀錄\n━━━━━━━━━━━━━━━━\n尚無交易記錄。"

    trades = result["trades"]
    wins = sum(1 for t in trades if (t.get("profit_pct") or 0) > 0 and not t.get("is_open"))
    losses = sum(1 for t in trades if (t.get("profit_pct") or 0) <= 0 and not t.get("is_open"))
    closed = wins + losses
    wr = (wins / closed * 100) if closed > 0 else 0
    total_pnl = sum(t.get("profit_abs", 0) or 0 for t in trades if not t.get("is_open"))

    lines = [f"💰 交易紀錄 ({closed}筆 {wins}W {losses}L {wr:.0f}%)", "━━━━━━━━━━━━━━━━"]
    if total_pnl != 0:
        lines.append(f"累計損益: ${total_pnl:+.2f}")

    for t in trades[:20]:
        pair = t.get("pair", "?").replace("/USDT:USDT", "")
        pnl = t.get("profit_pct", 0) or 0
        pnl_abs = t.get("profit_abs", 0) or 0
        reason = t.get("exit_reason") or ("持倉中" if t.get("is_open") else "?")
        side = "Short" if t.get("is_short") else "Long"
        lev = t.get("leverage", 1) or 1
        icon = "⚪" if t.get("is_open") else ("🟢" if pnl > 0 else "🔴")
        tid = t.get("trade_id", "?")

        # Duration
        dur = t.get("trade_duration", 0) or 0
        if dur >= 60:
            dur_str = f"{dur // 60}h{dur % 60}m"
        elif dur > 0:
            dur_str = f"{dur}m"
        else:
            dur_str = "持倉中"

        # Entry/exit prices
        open_r = t.get("open_rate", 0)
        close_r = t.get("close_rate") or t.get("current_rate", 0)
        if open_r > 1000:
            price_str = f"${open_r:,.0f}->${close_r:,.0f}"
        elif open_r > 1:
            price_str = f"${open_r:.2f}->${close_r:.2f}"
        else:
            price_str = f"${open_r:.4f}->${close_r:.4f}"

        lines.append(f"\n{icon} #{tid} {pair} {side} {lev:.1f}x")
        lines.append(f"  {price_str}")
        lines.append(f"  {pnl:+.2f}% ${pnl_abs:+.2f} | {dur_str} | {reason}")

    return "\n".join(lines)


def cmd_positions() -> str:
    """當前持倉詳情."""
    result = query_ft("status")
    if not result or len(result) == 0:
        return "📋 持倉\n━━━━━━━━━━━━━━━━\n目前無持倉。"

    bal_data = query_ft("balance")
    total_bal = bal_data.get("total", 1000) if bal_data else 1000

    unrealized = sum((t.get("profit_abs") or 0) for t in result)
    lines = [f"📋 持倉 ({len(result)}筆 未實現${unrealized:+.2f})", "━━━━━━━━━━━━━━━━"]

    total_used = 0
    for t in result:
        pair = t.get("pair", "?").replace("/USDT:USDT", "")
        side = "Short" if t.get("is_short") else "Long"
        lev = t.get("leverage", 1) or 1
        pnl_pct = t.get("profit_pct", 0) or 0
        stake = t.get("stake_amount", 0) or 0
        open_r = t.get("open_rate", 0)
        cur_r = t.get("current_rate", 0)
        sl = t.get("stop_loss", 0)

        pos_val = stake * lev
        total_used += stake

        # Duration
        open_date = t.get("open_date", "")
        if open_date:
            try:
                from datetime import datetime, timezone
                opened = datetime.fromisoformat(open_date.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - opened
                hours = delta.total_seconds() / 3600
                dur_str = f"{int(hours)}h{int((hours % 1) * 60)}m"
            except Exception:
                dur_str = "?"
        else:
            dur_str = "?"

        icon = "🟢" if pnl_pct >= 0 else "🔴"
        if open_r > 1000:
            price_str = f"進${open_r:,.0f} 現${cur_r:,.0f}"
        elif open_r > 1:
            price_str = f"進${open_r:.2f} 現${cur_r:.2f}"
        else:
            price_str = f"進${open_r:.4f} 現${cur_r:.4f}"

        sl_str = f"SL ${sl:,.0f}" if sl and sl > 1000 else (f"SL ${sl:.2f}" if sl else "SL 未設定")

        lines.append(f"\n{icon} {pair} {side} {lev:.1f}x")
        lines.append(f"  {price_str} {pnl_pct:+.2f}%")
        lines.append(f"  Stake ${stake:.0f} 倉值${pos_val:.0f}")
        lines.append(f"  {sl_str} | {dur_str}")

    used_pct = (total_used / total_bal * 100) if total_bal > 0 else 0
    lines.append(f"\n帳戶${total_bal:.0f} 使用${total_used:.0f} ({used_pct:.1f}%)")

    return "\n".join(lines)


def cmd_trade_stats() -> str:
    """交易統計總覽."""
    result = query_ft("trades?limit=100")

    if not result or not result.get("trades"):
        return "📊 統計\n━━━━━━━━━━━━━━━━\n尚無交易資料。"

    trades = [t for t in result["trades"] if not t.get("is_open")]
    if not trades:
        return "📊 統計\n━━━━━━━━━━━━━━━━\n尚無已關閉交易。"

    wins = [t for t in trades if (t.get("profit_pct") or 0) > 0]
    losses = [t for t in trades if (t.get("profit_pct") or 0) <= 0]
    total_gain = sum(t.get("profit_abs", 0) or 0 for t in wins)
    total_loss = abs(sum(t.get("profit_abs", 0) or 0 for t in losses))
    net = total_gain - total_loss
    pf = total_gain / total_loss if total_loss > 0 else float("inf")
    wr = len(wins) / len(trades) * 100 if trades else 0

    avg_dur = sum(t.get("trade_duration", 0) or 0 for t in trades) / len(trades)
    avg_dur_str = f"{int(avg_dur // 60)}h{int(avg_dur % 60)}m"

    # Best/worst pair
    pair_stats: dict[str, dict] = {}
    for t in trades:
        p = t.get("pair", "?").replace("/USDT:USDT", "")
        if p not in pair_stats:
            pair_stats[p] = {"wins": 0, "losses": 0, "pnl": 0}
        if (t.get("profit_pct") or 0) > 0:
            pair_stats[p]["wins"] += 1
        else:
            pair_stats[p]["losses"] += 1
        pair_stats[p]["pnl"] += t.get("profit_abs", 0) or 0

    best = max(pair_stats.items(), key=lambda x: x[1]["pnl"]) if pair_stats else ("?", {"wins": 0, "losses": 0, "pnl": 0})
    worst = min(pair_stats.items(), key=lambda x: x[1]["pnl"]) if pair_stats else ("?", {"wins": 0, "losses": 0, "pnl": 0})

    # Max single win/loss
    max_win = max((t.get("profit_abs", 0) or 0 for t in trades), default=0)
    max_loss = min((t.get("profit_abs", 0) or 0 for t in trades), default=0)

    lines = ["📊 交易統計", "━━━━━━━━━━━━━━━━"]
    lines.append(f"交易: {len(trades)}筆 | 勝率: {wr:.0f}%")
    lines.append(f"盈: +${total_gain:.2f} | 虧: -${total_loss:.2f}")
    lines.append(f"淨利: ${net:+.2f}")
    lines.append(f"Profit Factor: {pf:.2f}")
    lines.append(f"平均持倉: {avg_dur_str}")
    lines.append(f"最大單盈: +${max_win:.2f}")
    lines.append(f"最大單虧: ${max_loss:.2f}")
    lines.append(f"最佳: {best[0]} ({best[1]['wins']}W{best[1]['losses']}L)")
    lines.append(f"最差: {worst[0]} ({worst[1]['wins']}W{worst[1]['losses']}L)")

    return "\n".join(lines)


def cmd_guards() -> str:
    state = read_guard_state()
    lines = ["🛡 風控狀態", "━━━━━━━━━━━━━━━━"]

    daily = state.get("daily_loss", 0)
    streak = state.get("consecutive_losses", state.get("consec_streak", 0))
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


def cmd_journal() -> str:
    """交易日誌 — 結構化交易紀錄含進場條件."""
    try:
        from strategies.smc_trend import read_journal
        entries = read_journal(limit=20)
    except Exception:
        return "📓 交易日誌\n━━━━━━━━━━━━━━━━\n無法讀取日誌。"

    if not entries:
        return "📓 交易日誌\n━━━━━━━━━━━━━━━━\n尚無記錄。"

    # Pair ENTRY and EXIT by trade pair+ts proximity
    exits = [e for e in entries if e.get("event") == "EXIT"]
    entry_map = {}
    for e in entries:
        if e.get("event") == "ENTRY":
            entry_map[e.get("pair", "")] = e

    lines = ["📓 交易日誌", "━━━━━━━━━━━━━━━━"]

    # Grade statistics
    grade_stats = {"A": {"w": 0, "l": 0}, "B+": {"w": 0, "l": 0}, "B": {"w": 0, "l": 0}}

    for ex in exits[:10]:
        pair = ex.get("pair", "?").replace("/USDT:USDT", "")
        pnl = ex.get("pnl_pct", 0)
        pnl_usd = ex.get("pnl_usd", 0)
        r_mult = ex.get("r_multiple", 0)
        reason = ex.get("exit_reason", "?")
        dur = ex.get("duration_min", 0)
        icon = "🟢" if pnl > 0 else "🔴"

        # Find matching entry
        en = entry_map.get(ex.get("pair", ""))
        grade = en.get("grade", "?") if en else "?"
        conf_entry = en.get("confidence", 0) if en else 0
        conds = en.get("conditions", {}) if en else {}

        # Grade stats
        if grade in grade_stats:
            if pnl > 0:
                grade_stats[grade]["w"] += 1
            else:
                grade_stats[grade]["l"] += 1

        # Conditions summary
        cond_parts = []
        if conds.get("htf_trend"):
            cond_parts.append(f"htf={'+'  if conds['htf_trend'] > 0 else '-'}")
        if conds.get("in_ob"):
            cond_parts.append("OB")
        if conds.get("in_fvg"):
            cond_parts.append("FVG")
        if conds.get("confluence"):
            cond_parts.append("Confluence")
        if conds.get("in_ote"):
            cond_parts.append("OTE")
        if conds.get("strong_structure"):
            cond_parts.append("Strong")
        cond_str = " ".join(cond_parts) if cond_parts else "?"

        dur_str = f"{int(dur // 60)}h{int(dur % 60)}m" if dur >= 60 else f"{int(dur)}m"

        lines.append(f"\n{icon} {pair} Grade {grade} conf={conf_entry:.2f}")
        lines.append(f"  {pnl:+.2f}% ${pnl_usd:+.2f} | {r_mult:+.1f}R | {dur_str}")
        lines.append(f"  觸發: {cond_str}")
        lines.append(f"  出場: {reason}")

    # Grade summary
    lines.append("\n━━━━━━━━━━━━━━━━")
    for g, s in grade_stats.items():
        total = s["w"] + s["l"]
        if total > 0:
            wr = s["w"] / total * 100
            lines.append(f"Grade {g}: {total}筆 {wr:.0f}%W")

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
        "📋 分析 — 最新AI分析\n"
        "📰 摘要 — 機構報告精華\n\n"
        "💬 也可直接打字問問題\n"
        "例如: 現在適合做多嗎？"
    )


def cmd_overview() -> str:
    """一鍵全覽 — 最常用的單一訊息摘要。"""
    snap = read_snapshot()
    conf = snap.get("confidence", {})
    reg = snap.get("regime", {})
    macro = snap.get("macro", {})
    crypto = snap.get("crypto_env", {})
    ft = snap.get("freqtrade", {})
    guards = read_guard_state()

    score = conf.get("score", 0)
    regime = conf.get("regime", "?")
    reg_name = reg.get("regime", "?")
    fg = macro.get("fear_greed", "?")
    btc_env = crypto.get("BTC", {}).get("score", 0) if crypto else 0
    pos = ft.get("positions", [])
    profit = ft.get("profit", {})
    streak = guards.get("consecutive_losses", guards.get("consec_streak", 0))

    lines = [
        f"📊 全覽 | {snapshot_age()}",
        "━━━━━━━━━━━━━━━━",
        f"🎯 {score:.2f} {regime_emoji(regime)} {regime} | 📈 {reg_name} {regime_emoji(reg_name)}",
        f"🔗 BTC {btc_env:.2f} {bar(btc_env)}",
    ]

    if isinstance(fg, (int, float)):
        lines.append(f"😨 F&G {fg} {bar(fg, 100)} {fg_label(fg)}")

    # Macro highlights
    vix = macro.get("VIX", {})
    gold = macro.get("Gold", {})
    highlights = []
    if vix:
        a = " ⚠" if vix.get("price", 0) > 25 else ""
        highlights.append(f"VIX {vix.get('price', 0):.0f}{a}")
    if gold:
        a = " 🚨" if abs(gold.get("change_pct", 0)) > 3 else ""
        highlights.append(f"Gold {gold.get('change_pct', 0):+.1f}%{a}")
    if highlights:
        lines.append(f"🌍 {' | '.join(highlights)}")

    # Positions
    lines.append(f"📈 持倉: {len(pos)} | 🛡 Guard: {'🟢' if streak < 3 else '🟠'}")

    if profit:
        pnl = profit.get("profit_all_coin", 0)
        trades = profit.get("trade_count", 0)
        if trades > 0:
            lines.append(f"💰 {trades}筆 ${pnl:.2f}")

    # Event warning
    if conf.get("event_multiplier", 1) < 1:
        lines.append(f"⚠ 事件期 x{conf['event_multiplier']}")

    return "\n".join(lines)


def cmd_digest() -> str:
    """機構報告精華摘要."""
    try:
        from market_monitor.report_collector import get_latest_digest, run_collection_and_digest, SUMMARY_FILE
        # If digest is older than 8 hours, refresh
        if SUMMARY_FILE.exists():
            age_hours = (time.time() - SUMMARY_FILE.stat().st_mtime) / 3600
            if age_hours < 6:
                return get_latest_digest()
        # Collect fresh
        _, digest = run_collection_and_digest()
        return digest
    except Exception as e:
        logger.error("Digest generation failed: %s", e)
        return f"機構報告摘要生成失敗: {e}"


def _ai_call(system_prompt: str, user_content: str, max_tokens: int = 1000) -> str:
    """Shared Claude AI call with cooldown."""
    global _last_ai_call
    if not ANTHROPIC_API_KEY:
        return "AI 功能未啟用 (缺少 ANTHROPIC_API_KEY)"
    elapsed = time.time() - _last_ai_call
    if elapsed < AI_COOLDOWN:
        return f"AI 冷卻中，請等 {int(AI_COOLDOWN - elapsed)} 秒。"
    _last_ai_call = time.time()
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text = response.content[0].text if response.content else "無法生成回答"
        tokens = f"({response.usage.input_tokens}in/{response.usage.output_tokens}out)"
        return f"🤖 AI 分析\n━━━━━━━━━━━━━━━━\n{text}\n\n{tokens}"
    except Exception as e:
        logger.error("AI call failed: %s", e)
        return f"AI 查詢失敗: {str(e)[:100]}"


def cmd_ai_review() -> str:
    """AI 回顧最近交易，分析贏虧原因。"""
    trades_data = _ft_api("/api/v1/trades?limit=10") or []
    if not trades_data:
        return "無最近交易數據"
    trades_summary = "\n".join(
        f"- {t.get('pair','')} {'SHORT' if t.get('is_short') else 'LONG'} "
        f"PnL:{t.get('profit_pct',0):.2f}% 持倉:{t.get('trade_duration_s',0)//3600:.0f}h "
        f"出場:{t.get('exit_reason','')}"
        for t in trades_data[:10]
    )
    return _ai_call(
        "你是加密貨幣合約交易策略分析師。用繁體中文回答。"
        "分析最近交易的贏虧原因，識別模式，給出具體可執行的改善建議。"
        "策略是 Supertrend 4 層 MTF（1D→4H→1H→15m），趨勢跟蹤型。",
        f"最近 10 筆交易:\n{trades_summary}\n\n"
        "請分析：1. 贏家共同特徵 2. 虧損原因 3. 改善建議（具體可執行）"
    )


def cmd_ai_forecast() -> str:
    """AI 基於當前市場狀態預測交易機會。"""
    summary = read_summary()
    snapshot_path = DATA_DIR / "market_snapshot.json"
    snapshot = {}
    if snapshot_path.exists():
        try:
            with open(snapshot_path) as f:
                snapshot = json.load(f)
        except Exception:
            pass
    return _ai_call(
        "你是加密貨幣合約交易 AI。用繁體中文回答。"
        "基於 Supertrend 4 層 MTF 策略（1D/4H/1H/15m 方向一致性 + ADX>25 + 成交量確認）。"
        "預測未來 4-24 小時內各幣種（BTC/ETH/AVAX/NEAR/ATOM/ADA）的交易機會。"
        "標注方向、信心度、關鍵價位。如果沒有明確機會就說「觀望」。",
        f"市場數據:\n{summary}\n\n快照:\n{json.dumps(snapshot, default=str)[:2000]}\n\n"
        "請預測各幣種未來 4-24h 的交易機會。"
    )


def cmd_ai_risk() -> str:
    """AI 評估當前持倉風險。"""
    positions = _ft_api("/api/v1/status") or []
    balance = _ft_api("/api/v1/balance") or {}
    if not positions:
        return "目前無持倉，無需風險評估。"
    pos_summary = "\n".join(
        f"- {p.get('pair','')} {'SHORT' if p.get('is_short') else 'LONG'} "
        f"利潤:{p.get('profit_pct',0):.2f}% 槓桿:{p.get('leverage',1):.1f}x "
        f"倉位:{p.get('stake_amount',0):.0f}$"
        for p in positions
    )
    total_bal = balance.get("total", 0)
    return _ai_call(
        "你是加密貨幣合約交易風控專家。用繁體中文回答。"
        "評估當前持倉的風險水平，考慮：槓桿、方向集中度、市場波動率、相關性。"
        "給出明確的風險等級（低/中/高/極高）和具體建議（持有/減倉/對沖/平倉）。",
        f"帳戶餘額: {total_bal:.0f} USDT\n\n當前持倉:\n{pos_summary}\n\n"
        "請評估風險並給出具體建議。"
    )


def cmd_advisor_report() -> str:
    """投顧報告上傳入口。"""
    return (
        "📰 *投顧報告分析*\n\n"
        "請傳送投顧報告內容：\n"
        "• 直接貼上報告文字\n"
        "• 傳送報告截圖/照片\n"
        "• 傳送 PDF 檔案\n\n"
        "系統將自動：\n"
        "1. 解析報告重點\n"
        "2. 與系統信號交叉比對\n"
        "3. 生成結構化摘要"
    )


def cmd_tw_predict() -> str:
    """台股完整大盤預測。"""
    try:
        from market_monitor.tw_predictor import predict, format_predict_report
        result = predict()
        return format_predict_report(result)
    except Exception as e:
        logger.error("TW predict failed: %s", e)
        return f"台股預測失敗: {e}"


def cmd_tw_chips() -> str:
    """台股籌碼快報。"""
    try:
        from market_monitor.tw_predictor import calc_institutional_score, format_chips_report
        result = calc_institutional_score()
        return format_chips_report(result)
    except Exception as e:
        logger.error("TW chips failed: %s", e)
        return f"台股籌碼查詢失敗: {e}"


def cmd_tw_tech() -> str:
    """台股技術分析掃描。"""
    try:
        from market_monitor.tw_predictor import format_tech_report
        return format_tech_report()
    except Exception as e:
        logger.error("TW tech failed: %s", e)
        return f"台股技術分析失敗: {e}"


def cmd_tw_ml() -> str:
    """ML 模型預測台股方向。"""
    try:
        from market_monitor.ml.predict import predict_direction, format_ml_report
        result = predict_direction("^TWII", horizons=[5, 20])
        return format_ml_report(result)
    except Exception as e:
        logger.error("TW ML predict failed: %s", e)
        return f"ML 預測失敗: {e}"


def cmd_btc_ml() -> str:
    """ML 模型預測 BTC 方向。"""
    try:
        from market_monitor.ml.predict import predict_direction, format_ml_report
        result = predict_direction("BTC-USD", horizons=[5, 20])
        return format_ml_report(result)
    except Exception as e:
        logger.error("BTC ML predict failed: %s", e)
        return f"ML 預測失敗: {e}"


COMMANDS = {
    "status": cmd_status, "start": cmd_help, "help": cmd_help,
    "confidence": cmd_confidence, "crypto": cmd_crypto,
    "regime": cmd_regime, "analysis": cmd_analysis,
    "trades": cmd_trades, "positions": cmd_positions,
    "trade_stats": cmd_trade_stats, "journal": cmd_journal,
    "guards": cmd_guards,
    "decisions": cmd_decisions, "macro": cmd_macro,
    "menu": cmd_help, "overview": cmd_overview,
    "digest": cmd_digest,
    "ai_review": cmd_ai_review,
    "ai_forecast": cmd_ai_forecast,
    "ai_risk": cmd_ai_risk,
    "advisor_report": cmd_advisor_report,
    "tw_predict": cmd_tw_predict,
    "tw_chips": cmd_tw_chips,
    "tw_tech": cmd_tw_tech,
    "tw_ml": cmd_tw_ml,
    "btc_ml": cmd_btc_ml,
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

    # Add data freshness info
    snapshot_path = DATA_DIR / "market_snapshot.json"
    data_age = "未知"
    if snapshot_path.exists():
        age_sec = time.time() - snapshot_path.stat().st_mtime
        data_age = f"{age_sec/60:.0f} 分鐘前"
    data_warning = f"⚠️ 以下數據更新於 {data_age}。超過 30 分鐘的數據請標注「可能過期」。\n\n"

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system="你是加密貨幣合約交易AI助手。策略是 Supertrend 4 層 MTF（1D→4H→1H→15m 方向一致+ADX>25+成交量確認）。用繁體中文簡潔回答，300字以內。基於數據客觀分析。",
            messages=[{"role": "user", "content": f"{data_warning}市場數據:\n{summary}\n\n問題: {question}"}],
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
        {"command": "overview", "description": "一鍵全覽"},
        {"command": "positions", "description": "當前持倉詳情"},
        {"command": "trades", "description": "交易紀錄 (20筆)"},
        {"command": "trade_stats", "description": "統計 (勝率/PF)"},
        {"command": "analysis", "description": "完整分析儀表板"},
        {"command": "confidence", "description": "信心引擎分數"},
        {"command": "crypto", "description": "加密環境 (6幣種)"},
        {"command": "regime", "description": "市場機制 + 建議"},
        {"command": "macro", "description": "宏觀指標"},
        {"command": "guards", "description": "風控狀態"},
        {"command": "ai_review", "description": "AI 交易回顧"},
        {"command": "ai_forecast", "description": "AI 市場預測"},
        {"command": "ai_risk", "description": "AI 風險評估"},
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

    # Detect advisor report text (long message with TW stock keywords)
    ADVISOR_KEYWORDS = ["投顧", "買進", "目標價", "建議", "看好", "看多", "看空", "持有", "個股", "產業", "營收", "獲利", "EPS"]
    if len(text) > 100 and any(kw in text for kw in ADVISOR_KEYWORDS):
        try:
            from market_monitor.tw_advisor import TWAdvisorProcessor
            processor = TWAdvisorProcessor()
            result = processor.process_text(text)
            return result["telegram_message"]
        except Exception as e:
            logger.warning("投顧報告處理失敗: %s", e)
            return f"報告處理失敗: {e}"

    # 自由文字 → AI
    return handle_ai_query(text)


def _download_telegram_file(file_id: str) -> bytes | None:
    """下載 Telegram 檔案。"""
    try:
        token = os.environ.get("TG_AI_BOT_TOKEN", "")
        # Get file path
        url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            file_path = data["result"]["file_path"]
        # Download file
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        logger.warning("Telegram 檔案下載失敗: %s", e)
        return None


def handle_photo(photo_data: bytes, chat_id: int) -> str:
    """處理圖片上傳（投顧報告截圖）。"""
    try:
        from market_monitor.tw_advisor import TWAdvisorProcessor
        processor = TWAdvisorProcessor()
        result = processor.process_image(photo_data)
        return result["telegram_message"]
    except Exception as e:
        logger.warning("圖片報告處理失敗: %s", e)
        return f"圖片處理失敗: {e}"


def run_polling():
    if not BOT_TOKEN:
        logger.error("TG_AI_BOT_TOKEN not set!")
        return

    logger.info("Telegram AI Bot starting...")

    # Clear any stale webhook/polling connections to prevent 409 Conflict
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=false"
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("Cleared stale webhook/polling connections")
    except Exception as e:
        logger.warning("Failed to clear webhook: %s", e)

    setup_bot_commands()

    # Push updated keyboard to user on startup
    try:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if chat_id:
            send_reply(int(chat_id), "Bot 已啟動 — 按鈕選單已更新", with_menu=True)
    except Exception:
        pass

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

                if not chat_id:
                    continue
                if chat_id not in AUTHORIZED_CHAT_IDS:
                    continue

                # Check for photo messages
                if "photo" in msg:
                    photos = msg["photo"]
                    # Get largest photo
                    file_id = photos[-1]["file_id"]
                    # Download photo
                    photo_data = _download_telegram_file(file_id)
                    if photo_data:
                        reply = handle_photo(photo_data, chat_id)
                        send_reply(chat_id, reply)
                        continue

                text = msg.get("text", "")
                if not text:
                    continue

                logger.info("Msg from %s: %s", chat_id, text[:40])
                response = process_message(chat_id, text)
                send_reply(chat_id, response)

        except Exception as e:
            logger.error("Polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
