"""Summarizer — 將 50KB JSON 壓縮為 ~800 token 精簡摘要。

只突出異常值和變化，正常數據用最精簡格式。
輸出為 analysis_input.txt，供 AI Agent 直接讀取分析。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
SNAPSHOT_PATH = DATA_DIR / "market_snapshot.json"
SUMMARY_PATH = DATA_DIR / "analysis_input.txt"
PREV_SNAPSHOT_PATH = DATA_DIR / "market_snapshot_prev.json"


def load_snapshot() -> dict:
    """載入最新快照。"""
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


def load_prev_snapshot() -> dict | None:
    """載入上次快照 (用於趨勢比較)。"""
    try:
        with open(PREV_SNAPSHOT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def summarize(snapshot: dict, prev: dict | None = None) -> str:
    """生成精簡摘要。"""
    ts = snapshot.get("timestamp", "?")

    lines = [f"=== 市場快照 {ts[:19]} UTC ==="]

    # --- 信心引擎 ---
    conf = snapshot.get("confidence", {})
    score = conf.get("score", "?")
    regime = conf.get("regime", "?")
    event = conf.get("event_multiplier", 1.0)
    sb = conf.get("sandboxes", {})
    conf_line = f"信心: {score} {regime}"
    if event < 1.0:
        conf_line += f" (event x{event})"
    conf_line += f" | M={sb.get('macro','?')} S={sb.get('sentiment','?')} C={sb.get('capital','?')} H={sb.get('haven','?')}"
    lines.append(conf_line)

    # --- 市場機制 ---
    reg = snapshot.get("regime", {})
    lines.append(f"機制: {reg.get('regime', '?')} ({reg.get('confidence', 0):.0%})")

    # --- 宏觀 ---
    macro = snapshot.get("macro", {})
    macro_parts = []
    for key, label in [("VIX", "VIX"), ("10Y", "10Y"), ("Gold", "Gold"), ("Oil", "Oil")]:
        m = macro.get(key, {})
        if m:
            chg = m.get("change_pct", 0)
            flag = " ⚠" if abs(chg) > 2 else ""
            macro_parts.append(f"{label}: {m.get('price', '?')} ({chg:+.1f}%){flag}")
    fg = macro.get("fear_greed", "?")
    macro_parts.append(f"F&G: {fg}")
    btc_d = macro.get("btc_dominance")
    if btc_d:
        macro_parts.append(f"BTC.D: {btc_d}%")
    lines.append(" | ".join(macro_parts))

    # --- Crypto 環境 ---
    crypto = snapshot.get("crypto_env", {})
    if crypto:
        lines.append("")
        lines.append("Crypto Env:")
        for sym in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]:
            env = crypto.get(sym, {})
            if not env or env.get("error"):
                continue
            score_val = env.get("score", "?")
            regime_val = env.get("regime", "?")
            signals = env.get("signals", [])
            sig_str = " | " + " | ".join(signals) if signals else ""
            lines.append(f"  {sym} {score_val} {regime_val}{sig_str}")

    # --- 持倉 ---
    ft = snapshot.get("freqtrade", {})
    positions = ft.get("positions", [])
    profit = ft.get("profit", {})
    pos_count = len(positions)
    total_pnl = profit.get("profit_all_coin", 0)
    trade_count = profit.get("trade_count", 0)
    lines.append("")
    lines.append(f"持倉: {pos_count} | 總損益: ${total_pnl:.2f} | 交易: {trade_count} 筆")
    if positions:
        for p in positions[:3]:
            pair = p.get("pair", "?")
            pnl = p.get("profit_pct", 0)
            lines.append(f"  {pair}: {pnl:+.2f}%")

    # --- Guard ---
    guards = snapshot.get("guards", {})
    if guards:
        lines.append(f"Guard: DailyLoss ${guards.get('daily_loss', 0):.0f} | Streak {guards.get('consecutive_losses', 0)}")

    # --- 異常值標記 ---
    anomalies = []
    # Gold big move
    gold = macro.get("Gold", {})
    if gold and abs(gold.get("change_pct", 0)) > 2:
        anomalies.append(f"Gold {gold['change_pct']:+.2f}%")
    # VIX high
    vix = macro.get("VIX", {})
    if vix and vix.get("price", 0) > 25:
        anomalies.append(f"VIX {vix['price']:.1f}")
    # F&G extreme
    if isinstance(fg, (int, float)):
        if fg <= 20:
            anomalies.append(f"F&G 極度恐懼 ({fg})")
        elif fg >= 80:
            anomalies.append(f"F&G 極度貪婪 ({fg})")
    # Crypto env hostile
    for sym, env in crypto.items():
        if env.get("score", 1) < 0.3:
            anomalies.append(f"{sym} env HOSTILE ({env['score']:.2f})")
    # Event multiplier
    if conf.get("event_multiplier", 1) < 1:
        anomalies.append(f"事件乘數 {conf['event_multiplier']} (FOMC/CPI)")

    if anomalies:
        lines.append("")
        lines.append("異常:")
        for a in anomalies:
            lines.append(f"  ⚠ {a}")

    # --- 趨勢 (vs 上次) ---
    if prev:
        lines.append("")
        lines.append("趨勢 (vs 上次):")
        prev_conf = prev.get("confidence", {}).get("score")
        curr_conf = conf.get("score")
        if prev_conf and curr_conf:
            diff = float(curr_conf) - float(prev_conf)
            arrow = "↑" if diff > 0.02 else "↓" if diff < -0.02 else "→"
            lines.append(f"  信心 {prev_conf}→{curr_conf} ({arrow})")

        prev_vix = prev.get("macro", {}).get("VIX", {}).get("price")
        curr_vix = macro.get("VIX", {}).get("price")
        if prev_vix and curr_vix:
            diff = float(curr_vix) - float(prev_vix)
            lines.append(f"  VIX {prev_vix}→{curr_vix} ({diff:+.1f})")

    # --- 最近決策 ---
    decisions = snapshot.get("recent_decisions", [])
    if decisions:
        lines.append("")
        lines.append("最近決策:")
        for d in decisions[:3]:
            lines.append(f"  [{d.get('time', '?')}] {d.get('action', '?')} (conf={d.get('confidence', 0):.2f})")

    return "\n".join(lines)


def run() -> str:
    """生成摘要並保存。"""
    snapshot = load_snapshot()
    prev = load_prev_snapshot()
    summary = summarize(snapshot, prev)

    # Save summary
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(summary)

    # Rotate: current → prev
    import shutil
    shutil.copy2(SNAPSHOT_PATH, PREV_SNAPSHOT_PATH)

    logger.info("Summary generated: %d chars → %s", len(summary), SUMMARY_PATH)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
