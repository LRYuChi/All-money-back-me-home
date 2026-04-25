"""Hyperliquid 鯨魚錢包策略逆向工程 — R65.

對應 Polymarket 那支 polymarket_analyzer.py，這是 HL 永續鯨魚的版本。
從公開 Info API 拉 user fills，反推交易行為的策略特徵：

  ① 執行模式 (Bot / 半自動 / 人工)
  ② 進場風格 (HFT / 短線 / 波段 / 大趨勢)
  ③ 風控結構 (是否雙向對沖、勝率、止損紀律)
  ④ 資金管理 (倉位集中度、平均規模、平均槓桿)
  ⑤ 時間模式 (峰值時段、台灣對應時間)

用法:
    # 單一錢包，預設拉最近 30 天 (上限 5000 筆 fill)
    python -m smart_money.cli.whale_analyzer 0xabc123...

    # 自訂窗口與輸出
    python -m smart_money.cli.whale_analyzer 0xabc... --days 90 --limit 10000 \
        --out reports/whale_xyz.html

    # 只要終端報告
    python -m smart_money.cli.whale_analyzer 0xabc... --no-html

不寫 DB、不依賴 supabase — 純 stdlib + hyperliquid SDK。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("whale_analyzer")

HL_API = "https://api.hyperliquid.xyz"
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 5000
DEFAULT_OUT = "whale_report.html"


# =================================================================== #
# Fetch
# =================================================================== #
def fetch_fills(address: str, days: int, limit: int) -> list[dict]:
    """Pull user fills from HL Info API. Paginates by time-cursor.

    Uses the official `hyperliquid` SDK if installed (matches the rest of
    the smart_money/ codebase), else falls back to plain urllib POST.
    """
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    print(f"[1/3] 拉取 fills: {address[:10]}… 窗口 {days} 天 (上限 {limit})")

    fills: list[dict] = []
    cursor = start_ms

    try:
        from hyperliquid.info import Info
        info = Info(HL_API, skip_ws=True)
        use_sdk = True
    except ImportError:
        info = None
        use_sdk = False

    page = 0
    while len(fills) < limit:
        page += 1
        if use_sdk:
            try:
                batch = info.user_fills_by_time(
                    address=address,
                    start_time=cursor,
                    end_time=end_ms,
                    aggregate_by_time=False,
                )
            except Exception as e:
                print(f"  ⚠ SDK call failed page {page}: {e}")
                break
        else:
            batch = _post_info({
                "type": "userFillsByTime",
                "user": address,
                "startTime": cursor,
                "endTime": end_ms,
            })
        if not batch:
            break
        fills.extend(batch)
        print(f"  ... 已載入 {len(fills)} 筆 (page {page})", end="\r")
        # Cursor on next-page = last fill ts + 1ms
        last_ts = max(int(f.get("time", 0)) for f in batch)
        if last_ts <= cursor or len(batch) < 100:
            break
        cursor = last_ts + 1
    print(f"  ✓ 共載入 {len(fills)} 筆 fill")
    return fills[:limit]


def _post_info(body: dict) -> list[dict]:
    """Fallback when hyperliquid SDK is missing — direct REST POST."""
    import urllib.request
    req = urllib.request.Request(
        f"{HL_API}/info",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def fetch_clearinghouse_state(address: str) -> dict | None:
    """Pull current open positions + equity. Best-effort — returns None on failure."""
    try:
        from hyperliquid.info import Info
        info = Info(HL_API, skip_ws=True)
        return info.user_state(address)
    except Exception as e:
        logger.debug("clearinghouse_state fetch failed: %s", e)
        try:
            return _post_info({"type": "clearinghouseState", "user": address})
        except Exception:
            return None


# =================================================================== #
# Analysis
# =================================================================== #
@dataclass
class Analysis:
    n_fills: int = 0
    n_open: int = 0
    n_close: int = 0
    n_long_open: int = 0
    n_short_open: int = 0
    total_volume_usd: float = 0.0
    total_pnl_usd: float = 0.0
    total_fee_usd: float = 0.0
    n_winners: int = 0
    n_losers: int = 0
    avg_gap_sec: float = 0.0
    asset_count: Counter = field(default_factory=Counter)
    asset_volume: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    asset_pnl: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    direction_count: Counter = field(default_factory=Counter)
    hour_count: Counter = field(default_factory=Counter)
    size_buckets: list[int] = field(default_factory=lambda: [0] * 9)
    hold_durations_h: list[float] = field(default_factory=list)
    hedged_assets: dict[str, dict] = field(
        default_factory=lambda: defaultdict(lambda: {"long": 0, "short": 0}),
    )
    sorted_fills: list[dict] = field(default_factory=list)
    peak_hour: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_usd: float = 0.0


# Buckets in USD notional (size × price)
_SIZE_EDGES = [0, 100, 500, 1000, 5000, 10_000, 50_000, 100_000, 500_000]
_SIZE_LABELS = [
    "<100", "100-500", "500-1k", "1k-5k", "5k-10k",
    "10k-50k", "50k-100k", "100k-500k", "500k+",
]


def analyze(fills: list[dict]) -> Analysis:
    print("[2/3] 分析中…")
    a = Analysis(n_fills=len(fills))
    if not fills:
        return a

    # Sort ascending by time so we can compute gaps + hold durations
    a.sorted_fills = sorted(fills, key=lambda f: int(f.get("time", 0)))

    # Inter-trade gaps (cap to 24h to avoid sleep periods skewing)
    gaps = []
    for i in range(1, min(len(a.sorted_fills), 1000)):
        prev = int(a.sorted_fills[i - 1].get("time", 0))
        cur = int(a.sorted_fills[i].get("time", 0))
        g = (cur - prev) / 1000.0
        if 0 < g < 86400:
            gaps.append(g)
    a.avg_gap_sec = sum(gaps) / len(gaps) if gaps else 0.0

    # Track open positions per (coin, side) for hold-duration calc
    open_positions: dict[tuple[str, str], list[float]] = defaultdict(list)
    win_pnl: list[float] = []
    loss_pnl: list[float] = []

    for f in a.sorted_fills:
        coin = str(f.get("coin", "?"))
        direction = str(f.get("dir", ""))
        sz = float(f.get("sz", 0) or 0)
        px = float(f.get("px", 0) or 0)
        notional = sz * px
        ts_ms = int(f.get("time", 0))
        fee = float(f.get("fee", 0) or 0)
        closed_pnl = f.get("closedPnl")

        a.asset_count[coin] += 1
        a.asset_volume[coin] += notional
        a.total_volume_usd += notional
        a.total_fee_usd += fee

        # Direction parsing — match smart_money/scanner/hl_client conventions
        d_low = direction.strip()
        side = None
        action = None
        if "Long" in d_low and "Short" not in d_low:
            side = "long"
            action = "close" if d_low.startswith("Close") else "open"
        elif "Short" in d_low and "Long" not in d_low:
            side = "short"
            action = "close" if d_low.startswith("Close") else "open"
        elif "Long" in d_low and "Short" in d_low:
            # Reversal — both sides; count as both close + open of opposite
            side = "short" if d_low.startswith("Long") else "long"
            action = "open"
        else:
            a.direction_count["other"] += 1
            continue

        a.direction_count[side] += 1
        if action == "open":
            a.n_open += 1
            if side == "long":
                a.n_long_open += 1
            else:
                a.n_short_open += 1
            open_positions[(coin, side)].append(ts_ms)
            a.hedged_assets[coin][side] += 1
        else:
            a.n_close += 1
            queue = open_positions.get((coin, side))
            if queue:
                opened_at = queue.pop(0)
                hold_h = (ts_ms - opened_at) / 3_600_000.0
                if 0 < hold_h < 24 * 365:
                    a.hold_durations_h.append(hold_h)
            if closed_pnl is not None:
                pnl = float(closed_pnl)
                a.total_pnl_usd += pnl
                a.asset_pnl[coin] += pnl
                if pnl > 0:
                    a.n_winners += 1
                    win_pnl.append(pnl)
                elif pnl < 0:
                    a.n_losers += 1
                    loss_pnl.append(pnl)

        # Hour of day (UTC)
        if ts_ms:
            h = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour
            a.hour_count[h] += 1

        # Size bucket (USD notional)
        for j, edge in enumerate(_SIZE_EDGES):
            upper = _SIZE_EDGES[j + 1] if j + 1 < len(_SIZE_EDGES) else float("inf")
            if edge <= notional < upper:
                a.size_buckets[j] += 1
                break

    a.peak_hour = max(a.hour_count, key=a.hour_count.get) if a.hour_count else 0
    decided = a.n_winners + a.n_losers
    a.win_rate = a.n_winners / decided if decided else 0.0
    a.avg_win_usd = sum(win_pnl) / len(win_pnl) if win_pnl else 0.0
    a.avg_loss_usd = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0.0
    if loss_pnl:
        a.profit_factor = abs(sum(win_pnl) / sum(loss_pnl)) if sum(loss_pnl) else 0.0
    else:
        a.profit_factor = float("inf") if win_pnl else 0.0

    return a


def hedged_market_pct(a: Analysis) -> float:
    if not a.hedged_assets:
        return 0.0
    both = sum(
        1 for v in a.hedged_assets.values()
        if v["long"] > 0 and v["short"] > 0
    )
    return both / len(a.hedged_assets) * 100


def classify_strategy(a: Analysis) -> tuple[str, str]:
    """Return (label, description) — the 'verdict'."""
    is_bot = a.avg_gap_sec < 90 or a.n_fills > 1000
    long_short = a.n_long_open / max(a.n_short_open, 1)
    hedge_pct = hedged_market_pct(a)

    parts = []
    if is_bot:
        parts.append("自動化 BOT")
    if long_short > 2.0:
        parts.append("偏多")
    elif long_short < 0.5:
        parts.append("偏空")
    else:
        parts.append("方向中性")
    if hedge_pct > 25:
        parts.append("Delta 對沖")

    avg_hold = (
        sum(a.hold_durations_h) / len(a.hold_durations_h)
        if a.hold_durations_h else 0.0
    )
    if avg_hold < 1:
        parts.append("HFT/scalp")
    elif avg_hold < 12:
        parts.append("intra-day")
    elif avg_hold < 72:
        parts.append("swing")
    else:
        parts.append("position")

    label = " + ".join(parts)

    if a.win_rate >= 0.55 and a.profit_factor >= 1.5:
        verdict = "高勝率 + 高 PF — 真鯨魚行為，值得追蹤"
    elif a.win_rate >= 0.5 and a.profit_factor >= 1.2:
        verdict = "穩健獲利，邊際 edge 確定存在"
    elif a.profit_factor >= 1.0:
        verdict = "盈虧平衡附近，可能尚在試水或依賴大單"
    else:
        verdict = "虧損中，或樣本期不利 — 暫不建議跟單"

    return label, verdict


# =================================================================== #
# Terminal report
# =================================================================== #
def _bar(val: float, max_val: float, width: int = 25, char: str = "█") -> str:
    if max_val <= 0:
        return "░" * width
    filled = round(val / max_val * width)
    return char * filled + "░" * (width - filled)


def print_report(address: str, a: Analysis, state: dict | None) -> None:
    print("\n" + "═" * 65)
    print(f"  HYPERLIQUID WHALE ANALYSIS  //  {address[:10]}…{address[-6:]}")
    print("═" * 65)
    print(f"  Wallet      : {address}")
    if state:
        equity = (state.get("marginSummary") or {}).get("accountValue", "?")
        print(f"  Equity      : ${equity}")
    print(f"  Fills       : {a.n_fills:,}")
    print(f"  Volume      : ${a.total_volume_usd:,.0f}")
    print(f"  Realized PnL: ${a.total_pnl_usd:+,.2f}")
    print(f"  Fees paid   : ${a.total_fee_usd:,.2f}")
    print(f"  Avg gap     : {a.avg_gap_sec:.1f} sec/trade")
    print(f"  Open / Close: {a.n_open} / {a.n_close}")
    print("─" * 65)

    # Asset concentration
    print("\n  [資產集中度 — top 10 by fill count]")
    top_assets = a.asset_count.most_common(10)
    if top_assets:
        top_max = top_assets[0][1]
        for coin, cnt in top_assets:
            pct = cnt / a.n_fills * 100
            vol = a.asset_volume.get(coin, 0)
            pnl = a.asset_pnl.get(coin, 0)
            print(f"  {coin:<8} {_bar(cnt, top_max, 18)} "
                  f"{pct:5.1f}% ({cnt}) vol=${vol:>10,.0f} pnl=${pnl:+8,.0f}")

    # Direction
    print("\n  [方向偏好 (open events only)]")
    tot = a.n_long_open + a.n_short_open or 1
    print(f"  LONG  {_bar(a.n_long_open, tot, 25)} "
          f"{a.n_long_open / tot * 100:5.1f}%  ({a.n_long_open})")
    print(f"  SHORT {_bar(a.n_short_open, tot, 25)} "
          f"{a.n_short_open / tot * 100:5.1f}%  ({a.n_short_open})")
    if tot > 0:
        ls_ratio = a.n_long_open / max(a.n_short_open, 1)
        print(f"  L/S ratio   : {ls_ratio:.2f}x")

    # Position size distribution
    print("\n  [倉位 USD 名目分布]")
    sz_max = max(a.size_buckets) if a.size_buckets else 1
    for i, label in enumerate(_SIZE_LABELS):
        cnt = a.size_buckets[i]
        print(f"  {label:<10} {_bar(cnt, sz_max, 20)} {cnt:>4}")

    # Hold time
    if a.hold_durations_h:
        avg_hold = sum(a.hold_durations_h) / len(a.hold_durations_h)
        med_hold = sorted(a.hold_durations_h)[len(a.hold_durations_h) // 2]
        print(f"\n  [持倉時長]")
        print(f"  平均持倉 : {avg_hold:.2f} h  | 中位數: {med_hold:.2f} h"
              f"  | 樣本: {len(a.hold_durations_h)} 對 open→close")

    # Hour heatmap
    print("\n  [交易時段 UTC 00–23]")
    max_h = max(a.hour_count.values()) if a.hour_count else 1
    shades = " ░▒▓█"
    row = "  "
    for h in range(24):
        v = a.hour_count.get(h, 0)
        idx = min(4, round(v / max_h * 4))
        row += shades[idx]
    row += f"  peak={a.peak_hour:02d}:00 UTC"
    print(row)
    print(f"  (台灣時間約 {(a.peak_hour + 8) % 24:02d}:00 最活躍)")

    # PnL stats
    print("\n  [績效]")
    decided = a.n_winners + a.n_losers
    if decided:
        print(f"  Win rate    : {a.win_rate * 100:.1f}%  ({a.n_winners}W / {a.n_losers}L)")
        print(f"  Avg win     : ${a.avg_win_usd:+,.2f}")
        print(f"  Avg loss    : ${a.avg_loss_usd:+,.2f}")
        pf_str = f"{a.profit_factor:.2f}" if a.profit_factor != float("inf") else "∞"
        print(f"  Profit factor: {pf_str}")
    else:
        print("  (無關閉倉位的 PnL 樣本)")

    # Hedging
    hedge_pct = hedged_market_pct(a)
    print(f"\n  [風控結構]")
    print(f"  雙向持倉資產佔比: {hedge_pct:.0f}%")
    if hedge_pct > 25:
        print("  → 明顯 Delta 對沖傾向")
    elif hedge_pct > 10:
        print("  → 輕度反向持倉")
    else:
        print("  → 純方向性策略")

    # Verdict
    print("\n" + "─" * 65)
    print("  [策略逆向工程結論]")
    print("─" * 65)
    label, verdict = classify_strategy(a)
    print(f"  >> {label}")
    print(f"  >> {verdict}")
    print()

    # Recent fills
    print("─" * 65)
    print("  [最近 15 筆 fill]")
    print(f"  {'時間(UTC)':<17} {'COIN':<6} {'DIR':<14} {'SIZE':>10} "
          f"{'PRICE':>10} {'PNL':>10}")
    print("  " + "-" * 60)
    for f in reversed(a.sorted_fills[-15:]):
        ts = datetime.fromtimestamp(int(f.get("time", 0)) / 1000,
                                    tz=timezone.utc).strftime("%m-%d %H:%M")
        coin = str(f.get("coin", "?"))[:6]
        d = str(f.get("dir", ""))[:14]
        sz = float(f.get("sz", 0) or 0)
        px = float(f.get("px", 0) or 0)
        pnl = f.get("closedPnl")
        pnl_str = f"{float(pnl):+.2f}" if pnl is not None else "-"
        print(f"  {ts:<17} {coin:<6} {d:<14} {sz:>10.4f} {px:>10.4f} {pnl_str:>10}")
    print("\n" + "═" * 65 + "\n")


# =================================================================== #
# HTML report
# =================================================================== #
def generate_html(address: str, a: Analysis, state: dict | None) -> str:
    label, verdict = classify_strategy(a)
    is_bot = a.avg_gap_sec < 90 or a.n_fills > 1000
    hedge_pct = hedged_market_pct(a)
    avg_hold = (
        sum(a.hold_durations_h) / len(a.hold_durations_h)
        if a.hold_durations_h else 0.0
    )
    decided = a.n_winners + a.n_losers
    long_short_ratio = a.n_long_open / max(a.n_short_open, 1)
    equity = "—"
    if state:
        equity = (state.get("marginSummary") or {}).get("accountValue", "—")

    fills_for_js = [
        {
            "ts": int(f.get("time", 0)) // 1000,
            "coin": str(f.get("coin", "")),
            "dir": str(f.get("dir", "")),
            "sz": float(f.get("sz", 0) or 0),
            "px": float(f.get("px", 0) or 0),
            "pnl": float(f.get("closedPnl")) if f.get("closedPnl") is not None else None,
        }
        for f in a.sorted_fills[-300:]
    ]

    stats = {
        "n_fills": a.n_fills,
        "total_vol": round(a.total_volume_usd, 0),
        "total_pnl": round(a.total_pnl_usd, 2),
        "total_fee": round(a.total_fee_usd, 2),
        "avg_gap": round(a.avg_gap_sec, 1),
        "win_rate": round(a.win_rate * 100, 1),
        "profit_factor": (
            round(a.profit_factor, 2) if a.profit_factor != float("inf") else "∞"
        ),
        "n_open": a.n_open, "n_close": a.n_close,
        "n_long": a.n_long_open, "n_short": a.n_short_open,
        "long_short_ratio": round(long_short_ratio, 2),
        "hedge_pct": round(hedge_pct, 1),
        "avg_hold_h": round(avg_hold, 2),
        "peak_hour": a.peak_hour,
        "top_coin": a.asset_count.most_common(1)[0][0] if a.asset_count else "—",
        "top_coin_pct": (
            round(a.asset_count.most_common(1)[0][1] / a.n_fills * 100, 1)
            if a.asset_count else 0
        ),
        "label": label,
        "verdict": verdict,
        "is_bot": is_bot,
        "equity": str(equity),
    }
    asset_data = json.dumps(a.asset_count.most_common(10))
    pnl_by_coin = sorted(
        [(c, round(p, 2)) for c, p in a.asset_pnl.items()],
        key=lambda x: x[1], reverse=True,
    )[:10]
    return _HTML_TEMPLATE.format(
        address=address,
        addr_short=f"{address[:8]}…{address[-6:]}",
        stats_json=json.dumps(stats),
        asset_data=asset_data,
        pnl_by_coin=json.dumps(pnl_by_coin),
        size_data=json.dumps(a.size_buckets),
        size_labels=json.dumps(_SIZE_LABELS),
        hour_data=json.dumps([a.hour_count.get(h, 0) for h in range(24)]),
        hold_data=json.dumps(a.hold_durations_h[:1000]),
        fills_json=json.dumps(fills_for_js),
    )


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>{addr_short} — Hyperliquid Whale Strategy</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');
:root{{--bg:#060608;--s:#0d0d12;--b:#1e1e2a;--a:#00ff9d;--r:#ff3366;--u:#4466ff;--t:#e8e8f0;--m:#5a5a72;--mono:'Space Mono',monospace;--sans:'Syne',sans-serif;}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--t);font-family:var(--mono);padding:24px 20px;max-width:1100px;margin:0 auto}}
body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,255,157,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,157,.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}}
h1{{font-family:var(--sans);font-size:26px;font-weight:800;position:relative;z-index:1}}
h1 span{{color:var(--a)}}
.sub{{font-size:10px;color:var(--m);letter-spacing:.2em;margin-top:4px;text-transform:uppercase}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:24px 0;position:relative;z-index:1}}
.card{{background:var(--s);border:1px solid var(--b);padding:18px;position:relative;overflow:hidden}}
.card::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;background:var(--a)}}
.clabel{{font-size:9px;color:var(--m);letter-spacing:.2em;text-transform:uppercase;margin-bottom:8px}}
.cval{{font-family:var(--sans);font-size:24px;font-weight:800;line-height:1}}
.cval.g{{color:var(--a)}}.cval.r{{color:var(--r)}}.cval.u{{color:var(--u)}}
.csub{{font-size:10px;color:var(--m);margin-top:5px}}
.section{{background:var(--s);border:1px solid var(--b);margin-bottom:18px;position:relative;z-index:1}}
.sh{{padding:12px 18px;border-bottom:1px solid var(--b);font-size:10px;letter-spacing:.25em;text-transform:uppercase;color:var(--a);font-weight:700;display:flex;justify-content:space-between;align-items:center}}
.sb{{padding:18px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px;position:relative;z-index:1}}
@media(max-width:640px){{.two{{grid-template-columns:1fr}}}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:9px;font-size:10px}}
.bl{{width:110px;flex-shrink:0;font-size:9px;color:var(--t);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bt{{flex:1;height:5px;background:var(--b)}}
.bf{{height:100%;background:var(--a);transition:width .6s}}
.bf.r{{background:var(--r)}}.bf.u{{background:var(--u)}}
.bv{{width:80px;text-align:right;color:var(--m);font-size:9px;flex-shrink:0}}
canvas{{display:block;width:100%;height:140px}}
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{padding:7px 10px;color:var(--m);font-size:8px;letter-spacing:.2em;text-transform:uppercase;border-bottom:1px solid var(--b);font-weight:400;text-align:left}}
td{{padding:7px 10px;border-bottom:1px solid rgba(30,30,42,.4);vertical-align:middle}}
tr:hover td{{background:rgba(0,255,157,.02)}}
.tag{{display:inline-block;padding:1px 7px;font-size:8px;font-weight:700;letter-spacing:.1em}}
.tag.long{{background:rgba(0,255,157,.1);color:var(--a)}}.tag.short{{background:rgba(255,51,102,.1);color:var(--r)}}
.tag.open{{background:rgba(68,102,255,.1);color:var(--u)}}.tag.close{{background:rgba(232,232,240,.05);color:var(--t)}}
.finding{{border:1px solid var(--b);padding:15px;margin-bottom:12px;position:relative}}
.finding::before{{content:attr(data-n);position:absolute;top:-1px;right:14px;background:var(--bg);padding:0 5px;font-size:8px;color:var(--m);letter-spacing:.2em}}
.ft{{font-family:var(--sans);font-size:13px;font-weight:700;color:var(--a);margin-bottom:7px}}
.fb{{font-size:10px;color:var(--m);line-height:1.75}}
.fb strong{{color:var(--t)}}
header{{margin-bottom:28px;padding-bottom:18px;border-bottom:1px solid var(--b);position:relative;z-index:1}}
footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--b);font-size:9px;color:var(--m);letter-spacing:.15em;display:flex;justify-content:space-between;position:relative;z-index:1}}
.wrap{{overflow-x:auto;max-height:420px;overflow-y:auto}}
.wrap::-webkit-scrollbar{{width:3px;height:3px}}
.wrap::-webkit-scrollbar-thumb{{background:var(--b)}}
.pnl-pos{{color:var(--a)}}.pnl-neg{{color:var(--r)}}
</style>
</head>
<body>
<header>
  <div class="sub">Hyperliquid // Whale Strategy Dissector</div>
  <h1>{addr_short} <span>WHALE</span></h1>
  <div class="sub" style="margin-top:8px">{address}</div>
</header>

<div class="grid" id="statsGrid"></div>

<div class="two">
  <div class="section">
    <div class="sh">// 資產集中度</div>
    <div class="sb" id="assetDist"></div>
  </div>
  <div class="section">
    <div class="sh">// PnL by 幣種 (top 10)</div>
    <div class="sb" id="pnlByCoin"></div>
  </div>
</div>

<div class="two">
  <div class="section">
    <div class="sh">// 倉位 USD 名目分布</div>
    <div class="sb"><canvas id="sizeChart"></canvas></div>
  </div>
  <div class="section">
    <div class="sh">// 交易時段 UTC</div>
    <div class="sb"><canvas id="hourChart"></canvas><div id="hourNote" style="margin-top:8px;font-size:10px;color:var(--m)"></div></div>
  </div>
</div>

<div class="section">
  <div class="sh">// 持倉時長分布 (小時，capped 24h)</div>
  <div class="sb"><canvas id="holdChart"></canvas><div id="holdNote" style="margin-top:8px;font-size:10px;color:var(--m)"></div></div>
</div>

<div class="section">
  <div class="sh">// 策略逆向工程結論</div>
  <div class="sb" id="findings"></div>
</div>

<div class="section">
  <div class="sh">// 最近 fills <span style="color:var(--m);font-weight:400">最新 300 筆</span></div>
  <div class="wrap">
    <table>
      <thead><tr><th>時間 UTC</th><th>幣種</th><th>方向</th><th>動作</th><th>SIZE</th><th>價格</th><th>PnL</th></tr></thead>
      <tbody id="fillBody"></tbody>
    </table>
  </div>
</div>

<footer>
  <span>HL WHALE STRATEGY DISSECTOR // PUBLIC INFO API</span>
  <span id="ft"></span>
</footer>

<script>
const STATS = {stats_json};
const ASSETS = {asset_data};
const PNL_BY_COIN = {pnl_by_coin};
const SIZE_DATA = {size_data};
const SIZE_LABELS = {size_labels};
const HOURS = {hour_data};
const HOLDS = {hold_data};
const FILLS = {fills_json};

function drawBar(id, labels, data, colorFn) {{
  const canvas = document.getElementById(id);
  if (!canvas) return;
  const W = canvas.offsetWidth || 500, H = 140;
  canvas.setAttribute('width', W); canvas.setAttribute('height', H);
  const ctx = canvas.getContext('2d');
  const pL=34,pR=6,pT=8,pB=22;
  const cW=W-pL-pR, cH=H-pT-pB;
  ctx.clearRect(0,0,W,H);
  const max = Math.max(...data, 1), n = data.length;
  const gap = cW/n, bw = gap*0.7;
  ctx.strokeStyle='rgba(30,30,42,.8)'; ctx.lineWidth=1;
  for(let i=0;i<=4;i++) {{
    const y=pT+cH-(i/4)*cH;
    ctx.beginPath(); ctx.moveTo(pL,y); ctx.lineTo(W-pR,y); ctx.stroke();
    ctx.fillStyle='rgba(90,90,114,.7)'; ctx.font='7px Space Mono';
    ctx.textAlign='right'; ctx.fillText(Math.round(max*i/4),pL-2,y+3);
  }}
  data.forEach((v,i) => {{
    const x=pL+gap*i+(gap-bw)/2, h=(v/max)*cH, y=pT+cH-h;
    ctx.fillStyle = colorFn ? colorFn(v,i,max) : `rgba(0,255,157,${{.3+.7*(v/max)}})`;
    ctx.fillRect(x,y,bw,h);
    if(labels && labels[i]) {{
      ctx.fillStyle='rgba(90,90,114,.8)'; ctx.font='7px Space Mono';
      ctx.textAlign='center';
      ctx.fillText(labels[i], pL+gap*i+gap/2, H-pB+12);
    }}
  }});
}}

function drawHist(id, data, bins) {{
  if (!data.length) return;
  const sorted = [...data].sort((a,b)=>a-b);
  const min = sorted[0], max = Math.min(24, sorted[sorted.length-1]);
  const range = max - min || 1;
  const bw = range / bins;
  const counts = new Array(bins).fill(0);
  data.forEach(v => {{
    const idx = Math.min(bins-1, Math.floor((v-min)/bw));
    counts[idx]++;
  }});
  const labels = counts.map((_,i) => i % 4 === 0 ? `${{(min+i*bw).toFixed(1)}}h` : '');
  drawBar(id, labels, counts, (v,i,max) => `rgba(68,102,255,${{.2+.8*(v/max)}})`);
}}

function barRow(label, val, max, suffix='', cls='') {{
  const pct = (val/max*100).toFixed(1);
  return `<div class="bar-row"><div class="bl">${{label}}</div><div class="bt"><div class="bf ${{cls}}" style="width:${{pct}}%"></div></div><div class="bv">${{suffix||pct+'%'}}</div></div>`;
}}

window.addEventListener('load', () => {{
  document.getElementById('ft').textContent = new Date().toISOString().slice(0,19)+' UTC';

  // Stats grid
  const s = STATS;
  const pnlClass = s.total_pnl >= 0 ? 'g' : 'r';
  document.getElementById('statsGrid').innerHTML = `
    <div class="card"><div class="clabel">Fills</div><div class="cval u">${{s.n_fills.toLocaleString()}}</div><div class="csub">${{s.n_open}} open / ${{s.n_close}} close</div></div>
    <div class="card"><div class="clabel">Volume</div><div class="cval">$${{s.total_vol.toLocaleString()}}</div><div class="csub">total notional</div></div>
    <div class="card"><div class="clabel">Realized PnL</div><div class="cval ${{pnlClass}}">$${{s.total_pnl.toLocaleString()}}</div><div class="csub">fee paid $${{s.total_fee.toFixed(2)}}</div></div>
    <div class="card"><div class="clabel">Win rate</div><div class="cval ${{s.win_rate>=50?'g':'r'}}">${{s.win_rate}}%</div><div class="csub">PF ${{s.profit_factor}}</div></div>
    <div class="card"><div class="clabel">Mode</div><div class="cval" style="font-size:16px;color:${{s.is_bot?'var(--r)':'var(--a)'}}">${{s.is_bot?'BOT':'人工'}}</div><div class="csub">avg gap ${{s.avg_gap}}s</div></div>
    <div class="card"><div class="clabel">L/S ratio</div><div class="cval ${{s.long_short_ratio>1?'g':'r'}}">${{s.long_short_ratio}}x</div><div class="csub">${{s.n_long}}L / ${{s.n_short}}S</div></div>
    <div class="card"><div class="clabel">Hedge</div><div class="cval ${{s.hedge_pct>20?'r':'g'}}">${{s.hedge_pct}}%</div><div class="csub">雙向持倉</div></div>
    <div class="card"><div class="clabel">Avg hold</div><div class="cval">${{s.avg_hold_h}}h</div><div class="csub">open→close mean</div></div>
    <div class="card"><div class="clabel">Top coin</div><div class="cval" style="font-size:18px">${{s.top_coin}}</div><div class="csub">${{s.top_coin_pct}}% fills</div></div>
    <div class="card"><div class="clabel">Equity</div><div class="cval" style="font-size:14px">$${{s.equity}}</div><div class="csub">current snapshot</div></div>
  `;

  // Asset distribution
  const topMax = ASSETS[0]?.[1] || 1;
  document.getElementById('assetDist').innerHTML = ASSETS.map(([n, v]) =>
    barRow(n, v, topMax, `${{v}}`)).join('');

  // PnL by coin
  if (PNL_BY_COIN.length) {{
    const maxAbs = Math.max(...PNL_BY_COIN.map(([,p])=>Math.abs(p)), 1);
    document.getElementById('pnlByCoin').innerHTML = PNL_BY_COIN.map(([c,p]) => {{
      const pct = (Math.abs(p)/maxAbs*100).toFixed(1);
      const cls = p>=0 ? '' : 'r';
      const sign = p>=0 ? '+' : '';
      return `<div class="bar-row"><div class="bl">${{c}}</div><div class="bt"><div class="bf ${{cls}}" style="width:${{pct}}%"></div></div><div class="bv">${{sign}}$${{p.toFixed(0)}}</div></div>`;
    }}).join('');
  }} else {{
    document.getElementById('pnlByCoin').innerHTML = '<div style="color:var(--m);font-size:10px">尚無已實現 PnL</div>';
  }}

  // Size chart
  drawBar('sizeChart', SIZE_LABELS, SIZE_DATA);

  // Hour chart
  drawBar('hourChart', Array.from({{length:24}},(_,i)=>i%6===0?`${{i}}h`:''), HOURS,
    (v,i,max) => `rgba(68,102,255,${{.15+.85*(v/max)}})`);
  const tw = (s.peak_hour+8)%24;
  document.getElementById('hourNote').innerHTML =
    `Peak: UTC <strong style="color:var(--u)">${{String(s.peak_hour).padStart(2,'0')}}:00</strong> (台灣時間 ${{String(tw).padStart(2,'0')}}:00)`;

  // Hold histogram
  drawHist('holdChart', HOLDS, 20);
  document.getElementById('holdNote').innerHTML =
    `${{HOLDS.length}} 筆 open→close 配對，平均 <strong style="color:var(--t)">${{s.avg_hold_h}}h</strong>`;

  // Findings
  const findings = [
    {{n:'01', t:s.is_bot?'🤖 自動化 BOT':'👤 人工/半自動',
      b:`平均 fill 間隔 <strong>${{s.avg_gap}}s</strong>，共 <strong>${{s.n_fills.toLocaleString()}}</strong> 筆。
      ${{s.avg_gap<30?'< 30s = 確定全自動腳本，人類不可能。':s.avg_gap<120?'高頻，極可能是信號觸發的 algo。':'節奏偏人工，可能 swing 操盤手。'}}`}},
    {{n:'02', t:`📊 倉位風格 — 平均持倉 ${{s.avg_hold_h}}h`,
      b:`${{s.avg_hold_h<1?'<1h = HFT/scalp，吃手續費 rebate 或 flow 套利':
         s.avg_hold_h<12?'<12h = intra-day，捕捉日內波動':
         s.avg_hold_h<72?'12-72h = swing，跟趨勢或新聞':
         '>72h = position trader，長期布局或波段抓底'}}。
      L/S 比 <strong>${{s.long_short_ratio}}x</strong>，
      ${{s.long_short_ratio>2?'明顯偏多':s.long_short_ratio<0.5?'明顯偏空':'方向中性'}}。`}},
    {{n:'03', t:`🔄 風控 (Hedge ${{s.hedge_pct}}%)`,
      b:s.hedge_pct>25?
      `<strong>${{s.hedge_pct}}%</strong> 資產同時持有 long + short，明確 Delta 對沖機制。
      推測：開倉後若不利，反向補倉降低淨敞口，控制 drawdown。`:
      `雙向持倉 ${{s.hedge_pct}}%，方向性策略為主。${{s.hedge_pct>10?'偶爾反手或加碼相反方向':'純單邊'}}。`}},
    {{n:'04', t:`💰 績效 — Win rate ${{s.win_rate}}% / PF ${{s.profit_factor}}`,
      b:s.win_rate>=55 && s.profit_factor!='∞' && s.profit_factor>=1.5?
      '<strong style="color:var(--a)">真鯨魚行為</strong> — 高勝率 + 高 PF，值得追蹤跟單。':
      s.win_rate>=50 && s.profit_factor!='∞' && s.profit_factor>=1.2?
      '穩健獲利，邊際 edge 確定存在。可納入 follow-list 觀察。':
      s.profit_factor!='∞' && s.profit_factor>=1.0?
      '盈虧平衡附近，可能尚在試水期或仰賴大單。':
      '<strong style="color:var(--r)">虧損中</strong>，或樣本期不利市場。暫不建議跟單。'}},
    {{n:'05', t:`🎯 主力資產 — ${{s.top_coin}} ${{s.top_coin_pct}}%`,
      b:s.top_coin_pct>50?
      `高度集中 <strong>${{s.top_coin}}</strong>，深度客製化策略。可能對該幣有獨家 alpha (Twitter sentiment / 鏈上、orderflow)。`:
      `跨資產分散，多策略並行或被動套利驅動。`}},
    {{n:'99', t:`📋 綜合假說`,
      b:`<strong>${{s.label}}</strong><br>${{s.verdict}}`}},
  ];
  document.getElementById('findings').innerHTML = findings.map(f =>
    `<div class="finding" data-n="${{f.n}}"><div class="ft">${{f.t}}</div><div class="fb">${{f.b}}</div></div>`
  ).join('');

  // Fills table
  const rows = [...FILLS].reverse().map(t => {{
    const d = new Date(t.ts*1000);
    const time = d.toISOString().replace('T',' ').slice(0,16);
    const isLong = t.dir.toLowerCase().includes('long') && !t.dir.toLowerCase().includes('short');
    const isShort = t.dir.toLowerCase().includes('short') && !t.dir.toLowerCase().includes('long');
    const isOpen = t.dir.toLowerCase().includes('open');
    const sideTag = isLong?'<span class="tag long">LONG</span>':isShort?'<span class="tag short">SHORT</span>':t.dir;
    const actTag = isOpen?'<span class="tag open">OPEN</span>':'<span class="tag close">CLOSE</span>';
    const pnlCell = t.pnl !== null ?
      `<span class="${{t.pnl>=0?'pnl-pos':'pnl-neg'}}">${{t.pnl>=0?'+':''}}${{t.pnl.toFixed(2)}}</span>` : '—';
    return `<tr>
      <td style="color:var(--m);font-size:9px">${{time}}</td>
      <td><strong>${{t.coin}}</strong></td>
      <td>${{sideTag}}</td>
      <td>${{actTag}}</td>
      <td>${{t.sz.toFixed(4)}}</td>
      <td>$${{t.px.toFixed(4)}}</td>
      <td>${{pnlCell}}</td>
    </tr>`;
  }}).join('');
  document.getElementById('fillBody').innerHTML = rows;
}});
</script>
</body>
</html>"""


# =================================================================== #
# Entry
# =================================================================== #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smart_money.cli.whale_analyzer",
        description="Hyperliquid 鯨魚錢包策略逆向工程。",
    )
    p.add_argument("address", help="HL wallet address (0x…)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"窗口天數 (預設 {DEFAULT_DAYS})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"最多 fill 筆數 (預設 {DEFAULT_LIMIT})")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help=f"HTML 輸出路徑 (預設 {DEFAULT_OUT})")
    p.add_argument("--no-html", action="store_true",
                   help="只輸出終端報告，不產生 HTML")
    p.add_argument("--log-level", default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.address.startswith("0x") or len(args.address) != 42:
        print(f"  ✗ 不像合法 EVM 地址: {args.address}")
        return 2

    print(f"\n  HL WHALE STRATEGY DISSECTOR")
    print(f"  Address: {args.address}  Days: {args.days}  Limit: {args.limit}\n")

    fills = fetch_fills(args.address, args.days, args.limit)
    if not fills:
        print("  ✗ 找不到 fills (可能地址沒交易紀錄、SDK 沒裝、或網路問題)")
        return 1

    state = fetch_clearinghouse_state(args.address)
    a = analyze(fills)

    print("[3/3] 輸出報告…")
    print_report(args.address, a, state)

    if not args.no_html:
        html = generate_html(args.address, a, state)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"  ✓ HTML 報告: {args.out}")
        print("  → 用瀏覽器打開檢視完整視覺化\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
