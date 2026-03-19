#!/usr/bin/env python3
"""模擬交易驗證 — 驗證信號生成管線是否正常運作.

在機器人處於 HIBERNATE 模式時，模擬評估進場條件（不含信心門檻），
證明信號生成管線仍在正常運作。

檢查 SMC 進場條件:
- htf_trend != 0（高時間框架趨勢存在）
- in_bullish_ob / in_bearish_ob（在 Order Block 中）
- in_bullish_fvg / in_bearish_fvg（在 FVG 中）
- in_killzone == 1（在活躍交易時段）

Usage:
    python scripts/dry_run_verify.py
    # 排程: 0 */6 * * * python scripts/dry_run_verify.py
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
FT_AUTH = base64.b64encode(b"freqtrade:freqtrade").decode()


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


def _find_column_index(columns: list, name: str) -> int:
    """在 columns 列表中查找欄位索引，找不到回傳 -1."""
    try:
        return columns.index(name)
    except ValueError:
        return -1


def _get_val(row: list, columns: list, name: str, default=0):
    """從 K 線資料行中取得指定欄位的值."""
    idx = _find_column_index(columns, name)
    if idx < 0 or idx >= len(row):
        return default
    val = row[idx]
    if val is None:
        return default
    return val


def analyze_pair(pair: str) -> dict:
    """分析單一交易對過去 24 小時的潛在進場信號.

    Returns:
        dict with keys: pair, candles_analyzed, long_signals, short_signals,
                        total_signals, indicator_ok
    """
    result = {
        "pair": pair,
        "candles_analyzed": 0,
        "long_signals": 0,
        "short_signals": 0,
        "total_signals": 0,
        "indicator_ok": False,
    }

    # 編碼交易對名稱（處理 / 和 : 字元）
    encoded_pair = urllib.request.quote(pair, safe="")
    candles = ft_get(f"pair_candles?pair={encoded_pair}&timeframe=1h&limit=24")

    if not candles:
        return result

    data = candles.get("data", [])
    columns = candles.get("columns", [])

    if not data or not columns:
        return result

    result["candles_analyzed"] = len(data)
    result["indicator_ok"] = True

    long_count = 0
    short_count = 0

    for row in data:
        htf_trend = _get_val(row, columns, "htf_trend", 0)
        in_bullish_ob = _get_val(row, columns, "in_bullish_ob", 0)
        in_bearish_ob = _get_val(row, columns, "in_bearish_ob", 0)
        in_bullish_fvg = _get_val(row, columns, "in_bullish_fvg", 0)
        in_bearish_fvg = _get_val(row, columns, "in_bearish_fvg", 0)
        in_killzone = _get_val(row, columns, "in_killzone", 0)

        # 無趨勢方向則跳過
        if htf_trend == 0:
            continue

        # 不在 Killzone 則跳過
        if in_killzone != 1:
            continue

        # 檢查做多條件：趨勢向上 + (在看多 OB 或看多 FVG)
        if htf_trend > 0 and (in_bullish_ob == 1 or in_bullish_fvg == 1):
            long_count += 1

        # 檢查做空條件：趨勢向下 + (在看空 OB 或看空 FVG)
        if htf_trend < 0 and (in_bearish_ob == 1 or in_bearish_fvg == 1):
            short_count += 1

    result["long_signals"] = long_count
    result["short_signals"] = short_count
    result["total_signals"] = long_count + short_count

    return result


def main():
    now = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] 模擬交易驗證開始...\n")

    # ── 1. 取得交易對列表 ──
    config = ft_get("show_config")
    if not config:
        print("❌ 無法連接 Freqtrade API，驗證中止")
        return

    pairs = config.get("exchange", {}).get("pair_whitelist", [])
    if not pairs:
        print("⚠️ 交易對列表為空")
        return

    print(f"交易對: {len(pairs)} ({', '.join(p.split('/')[0] for p in pairs)})\n")

    # ── 2. 取得當前信心分數 ──
    confidence = 0.0
    regime = "HIBERNATE"
    try:
        from market_monitor.state_store import BotStateStore
        state = BotStateStore.read()
        confidence = state.get("last_confidence_score", 0.0)
        regime = state.get("last_confidence_regime", "HIBERNATE")
    except Exception as e:
        print(f"⚠️ 無法讀取信心分數: {e}")

    regime_zh = {
        "AGGRESSIVE": "積極",
        "NORMAL": "正常",
        "CAUTIOUS": "謹慎",
        "DEFENSIVE": "防禦",
        "HIBERNATE": "休眠",
    }.get(regime, regime)

    # ── 3. 驗證信號管線（用 BTC 確認指標存在）──
    test_pair = pairs[0] if pairs else "BTC/USDT:USDT"
    encoded_test = urllib.request.quote(test_pair, safe="")
    test_candles = ft_get(f"pair_candles?pair={encoded_test}&timeframe=1h&limit=5")
    indicator_ok = False
    candles_count = 0

    if test_candles:
        test_data = test_candles.get("data", [])
        candles_count = len(test_data)
        if candles_count > 0:
            indicator_ok = True

    # ── 4. 分析各交易對 ──
    pair_results = []
    total_signals = 0

    for pair in pairs:
        try:
            result = analyze_pair(pair)
            pair_results.append(result)
            total_signals += result["total_signals"]

            # stdout 輸出
            if result["total_signals"] > 0:
                parts = []
                if result["long_signals"] > 0:
                    parts.append(f"{result['long_signals']} 做多")
                if result["short_signals"] > 0:
                    parts.append(f"{result['short_signals']} 做空")
                print(f"  {pair}: {', '.join(parts)}")
            else:
                print(f"  {pair}: 無信號")
        except Exception as e:
            print(f"  {pair}: 分析失敗 ({e})")
            pair_results.append({
                "pair": pair,
                "candles_analyzed": 0,
                "long_signals": 0,
                "short_signals": 0,
                "total_signals": 0,
                "indicator_ok": False,
            })

    # ── 5. 組裝報告 ──
    lines = []
    lines.append("🔍 *模擬交易驗證報告*")
    lines.append(f"⏰ {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # 信號管線狀態
    lines.append("【信號管線狀態】")
    if indicator_ok:
        lines.append(f"  ✅ 指標計算: 正常（{candles_count} 根 K 線已分析）")
    else:
        lines.append("  ❌ 指標計算: 異常（無法取得 K 線數據）")
    lines.append(f"  🎯 當前信心: {confidence:.2f} ({regime_zh})")
    lines.append("")

    # 各交易對潛在信號
    lines.append("【假設信心足夠的潛在信號 (24h)】")
    for r in pair_results:
        pair_short = r["pair"]
        if r["total_signals"] > 0:
            parts = []
            if r["long_signals"] > 0:
                parts.append(f"{r['long_signals']} 個做多信號")
            if r["short_signals"] > 0:
                parts.append(f"{r['short_signals']} 個做空信號")
            lines.append(f"  {pair_short}: {', '.join(parts)}")
        else:
            lines.append(f"  {pair_short}: 0 個信號")

    lines.append("")
    lines.append(f"  總計: {total_signals} 個潛在進場信號被信心門檻過濾")
    lines.append("")

    # 結論
    lines.append("【結論】")
    if indicator_ok and any(r["indicator_ok"] for r in pair_results):
        lines.append("  ✅ 信號生成管線正常運作")
    else:
        lines.append("  ❌ 信號生成管線異常，需要檢查")

    if regime in ("HIBERNATE", "DEFENSIVE") and total_signals > 0:
        lines.append(f"  ⚠️ 因信心分數 {confidence:.2f} ({regime_zh}) 而未執行交易")
    elif total_signals == 0:
        lines.append("  ℹ️ 過去 24 小時無符合 SMC 條件的進場信號")
    else:
        lines.append(f"  ✅ 信心 {confidence:.2f} ({regime_zh})，交易正常執行中")

    report_text = "\n".join(lines)

    # 輸出至 stdout
    print(f"\n{'=' * 50}")
    print(report_text)
    print("=" * 50)

    # 發送至 Telegram
    try:
        from market_monitor.telegram_zh import send_message
        send_message(report_text)
        print("\nTelegram: 已發送")
    except Exception as e:
        print(f"\nTelegram: {e}")

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 驗證完成")


if __name__ == "__main__":
    main()
