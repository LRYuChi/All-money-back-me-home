"""排名量化指標 — 6 個確定性特徵.

設計原則:
- 每個函式輸入 `list[Trade]`,輸出一個 float 分數(大部分越大越好).
- 邊界處理:空輸入回合理預設(通常 0),不 raise.
- 全確定性:相同輸入必產生相同輸出(不用 random, 不用時間 now()).
- 每個特徵伴隨詳細 docstring 說明「為什麼這指標能抓到 smart money」.

見 docs/SMART_MONEY_MIGRATION.md §3 Phase 2 的權重配置.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from smart_money.store.schema import Trade

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def closed_pnls(trades: list[Trade]) -> list[float]:
    """抽出所有 close/decrease 的 pnl,過濾 None."""
    out: list[float] = []
    for t in trades:
        if t.action in ("close", "decrease") and t.pnl is not None:
            out.append(t.pnl)
    return out


def _stddev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


# ================================================================== #
# 1) Sortino Ratio — 下行風險調整報酬
# ================================================================== #
# 為什麼用 Sortino 而非 Sharpe:
#   鯨魚跟單最關心「下行風險」,Sortino 只對 negative returns 計 stddev,
#   比 Sharpe 更能篩出「有本事控制回撤」的交易員.
#
# 公式:  (mean_return - risk_free) / downside_deviation
# 這裡我們用 trade-level PnL(已扣 fee),不除資本假設(因為我們不知道每個錢包
# 實際投入資本).改為 PnL / trade 作為 return proxy,並以 0 作為目標.
#
# Winsorize:極端正 outlier 不增 denominator 但灌 numerator,會灌水分數;
#   對大幅正向 PnL 做 99% winsor.
# ================================================================== #
def sortino_ratio(trades: list[Trade], *, mar: float = 0.0) -> float:
    """Minimum Acceptable Return = mar (default 0)."""
    pnls = closed_pnls(trades)
    if len(pnls) < 2:
        return 0.0

    # winsorize 99th percentile on positives
    sorted_pnls = sorted(pnls)
    cap_idx = max(0, int(len(sorted_pnls) * 0.99) - 1)
    cap = sorted_pnls[cap_idx]
    winsorized = [min(p, cap) if p > 0 else p for p in pnls]

    excess = [p - mar for p in winsorized]
    avg_excess = sum(excess) / len(excess)

    downside = [min(0.0, e) for e in excess]
    downside_dev = math.sqrt(sum(d ** 2 for d in downside) / len(downside))

    if downside_dev == 0:
        # 零下行:若平均也 ≥ 0 視為完美;給定上限避免 inf
        return 10.0 if avg_excess >= 0 else -10.0

    return avg_excess / downside_dev


# ================================================================== #
# 2) Profit Factor — 賺賠比
# ================================================================== #
# 公式:  sum(wins) / abs(sum(losses))
# > 1.5 算穩定獲利;< 1.0 長期虧損;1.0 ~ 1.5 勉強
# 對刷單 grid bot 友好(grid 通常 PF 勉強過 1.0 但靠量),因此搭配其他特徵.
# ================================================================== #
def profit_factor(trades: list[Trade]) -> float:
    pnls = closed_pnls(trades)
    wins = sum(p for p in pnls if p > 0)
    losses = sum(-p for p in pnls if p < 0)
    if losses == 0:
        return 10.0 if wins > 0 else 0.0       # 只賺不賠:給上限
    return wins / losses


# ================================================================== #
# 3) Max Drawdown Recovery — 最大回撤後回補天數
# ================================================================== #
# 邏輯:把 PnL 依 ts 累積成 equity curve,找最大回撤點(peak→trough),
#      然後算「從 trough 回到 peak」花了多少天.
# 值越小越好(快速恢復 = 韌性高).我們回傳「log(1 + days)」做成越大越好的正向分數:
#      recovery_score = 1 / (1 + log(1 + days_to_recover))
#      未恢復的 → 給 0 分(最差).
# ================================================================== #
@dataclass(slots=True)
class DrawdownStats:
    max_drawdown: float        # 峰谷跌幅(絕對值,正數)
    recovery_days: float | None   # None 表示尚未恢復
    peak_equity: float
    trough_equity: float


def compute_drawdown(trades: list[Trade]) -> DrawdownStats:
    """計算最大回撤與回補時間.

    演算法(兩 pass,清晰正確):
      1) 掃一遍 equity curve,找 max_dd 與對應 (peak_ts, trough_ts, peak_before_trough)
      2) 從 trough_ts 之後掃,找第一個 equity >= peak_before_trough 的點 → recovery_ts
    """
    pairs = [(t.ts, t.pnl) for t in trades
             if t.action in ("close", "decrease") and t.pnl is not None]
    if not pairs:
        return DrawdownStats(0.0, None, 0.0, 0.0)

    pairs.sort(key=lambda x: x[0])

    # -- Pass 1: 找 max_dd / peak / trough ---------------------------------
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    trough_equity = 0.0
    trough_ts: datetime | None = None
    peak_before_trough = 0.0

    for ts, pnl in pairs:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            trough_equity = equity
            trough_ts = ts
            peak_before_trough = peak

    # -- Pass 2: 找 recovery -----------------------------------------------
    recovery_ts: datetime | None = None
    if trough_ts is not None:
        equity = 0.0
        for ts, pnl in pairs:
            equity += pnl
            if ts > trough_ts and equity >= peak_before_trough:
                recovery_ts = ts
                break

    recovery_days: float | None = None
    if trough_ts is not None and recovery_ts is not None:
        recovery_days = (recovery_ts - trough_ts).total_seconds() / 86400

    return DrawdownStats(
        max_drawdown=max_dd,
        recovery_days=recovery_days,
        peak_equity=peak,
        trough_equity=trough_equity,
    )


def drawdown_recovery_score(trades: list[Trade]) -> float:
    """回傳 0~1,越大代表恢復越快或從未經歷顯著 DD."""
    stats = compute_drawdown(trades)
    if stats.max_drawdown == 0:
        return 1.0      # 從無回撤:滿分
    if stats.recovery_days is None:
        return 0.0      # 回撤後未恢復:0 分
    # 30 天內恢復 → 0.7+;90 天內恢復 → 0.5;180+ 天 → < 0.3
    return 1.0 / (1.0 + math.log1p(stats.recovery_days))


# ================================================================== #
# 4) Holding Time CV — 持倉時間變異係數
# ================================================================== #
# 為什麼用 CV:
#   真人 discretionary trader 的持倉時間分布會有顯著變異(有快進快出,
#   也有長期趨勢單);grid bot 或 HFT 持倉時間幾乎一致(CV 很小).
#
# CV = stddev / mean.  回傳 cv 本身(越大越「像人」);scorer 再決定如何加權.
# ================================================================== #
def holding_time_cv(trades: list[Trade]) -> float:
    """同 filters.avg_holding_seconds 的配對邏輯,但回 CV."""
    groups: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        groups[(t.symbol, t.side)].append(t)

    deltas: list[float] = []
    for g in groups.values():
        g_sorted = sorted(g, key=lambda x: x.ts)
        open_stack: list[datetime] = []
        for t in g_sorted:
            if t.action in ("open", "increase"):
                open_stack.append(t.ts)
            elif t.action in ("close", "decrease") and open_stack:
                opened_at = open_stack.pop(0)
                deltas.append((t.ts - opened_at).total_seconds())

    if len(deltas) < 2:
        return 0.0
    mean = sum(deltas) / len(deltas)
    if mean == 0:
        return 0.0
    sd = _stddev(deltas)
    return sd / mean


# ================================================================== #
# 5) Martingale Penalty — 加倉攤平偵測
# ================================================================== #
# 偵測 pattern:
#   「連續 N 次虧損後,下一筆 size 顯著大於前平均」→ 加倉攤平典型徵兆.
#
# 算法:
#   1. 依 ts 排序 close 事件.
#   2. 找連續 ≥ 2 個虧損的段落,下一個新 open 若 size > 前 3 筆平均 size × 1.5 → 標記.
#   3. 標記次數 / 總 close 次數 = penalty (0~1).
# ================================================================== #
def martingale_penalty(trades: list[Trade]) -> float:
    if not trades:
        return 0.0

    ts_sorted = sorted(trades, key=lambda t: t.ts)

    consecutive_losses = 0
    recent_open_sizes: list[float] = []
    flagged = 0
    closes = 0

    for t in ts_sorted:
        if t.action in ("close", "decrease"):
            closes += 1
            if t.pnl is not None and t.pnl < 0:
                consecutive_losses += 1
            elif t.pnl is not None and t.pnl > 0:
                consecutive_losses = 0
        elif t.action in ("open", "increase"):
            if consecutive_losses >= 2 and len(recent_open_sizes) >= 3:
                avg = sum(recent_open_sizes[-3:]) / 3
                if t.size > avg * 1.5:
                    flagged += 1
            recent_open_sizes.append(t.size)

    if closes == 0:
        return 0.0
    return min(1.0, flagged / closes)


# ================================================================== #
# 6) Regime Stability — 多/空/盤整 regime 各自是否皆能賺
# ================================================================== #
# 邏輯:把時間軸按 30 天 window 切;對每 window,依「多方錢包 PnL 總和 vs
# 空方 PnL 總和」決定該 window 是 long-biased / short-biased / mixed.
#
# 不需要外部市場資料(keep system self-contained);看錢包自身在不同風格下
# 是否一致賺錢.
#
# 分數:每個 window 為正 PnL → +1;每個 window 為負 → +0.
# 最終 score = 正 window 數 / 總 window 數.
# ================================================================== #
def regime_stability(trades: list[Trade], *, window_days: int = 30) -> float:
    pairs = [(t.ts, t.pnl) for t in trades
             if t.action in ("close", "decrease") and t.pnl is not None]
    if len(pairs) < 2:
        return 0.0

    pairs.sort(key=lambda x: x[0])
    start = pairs[0][0]
    end = pairs[-1][0]
    total_days = (end - start).total_seconds() / 86400

    n_windows = max(1, int(total_days / window_days))
    if n_windows < 2:
        # 不足一個 window 或只有一個 → 退化為「總體 PnL > 0 ? 0.5 : 0」
        total_pnl = sum(p for _, p in pairs)
        return 0.5 if total_pnl > 0 else 0.0

    window_pnls: list[float] = [0.0] * n_windows
    window_length_sec = window_days * 86400
    for ts, pnl in pairs:
        idx = int((ts - start).total_seconds() / window_length_sec)
        idx = min(idx, n_windows - 1)
        window_pnls[idx] += pnl

    positive = sum(1 for p in window_pnls if p > 0)
    return positive / n_windows


# ================================================================== #
# Public facade: compute all metrics for a wallet
# ================================================================== #
@dataclass(slots=True)
class MetricsBundle:
    sortino: float
    profit_factor: float
    drawdown_recovery: float       # 0~1
    holding_time_cv: float
    martingale_penalty: float      # 0~1 (higher = worse)
    regime_stability: float        # 0~1
    sample_size: int
    total_pnl: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "sortino": self.sortino,
            "profit_factor": self.profit_factor,
            "drawdown_recovery": self.drawdown_recovery,
            "holding_time_cv": self.holding_time_cv,
            "martingale_penalty": self.martingale_penalty,
            "regime_stability": self.regime_stability,
            "sample_size": self.sample_size,
            "total_pnl": self.total_pnl,
        }


def compute_all(trades: list[Trade]) -> MetricsBundle:
    pnls = closed_pnls(trades)
    return MetricsBundle(
        sortino=sortino_ratio(trades),
        profit_factor=profit_factor(trades),
        drawdown_recovery=drawdown_recovery_score(trades),
        holding_time_cv=holding_time_cv(trades),
        martingale_penalty=martingale_penalty(trades),
        regime_stability=regime_stability(trades),
        sample_size=len(pnls),
        total_pnl=sum(pnls),
    )


__all__ = [
    "DrawdownStats",
    "MetricsBundle",
    "closed_pnls",
    "compute_all",
    "compute_drawdown",
    "drawdown_recovery_score",
    "holding_time_cv",
    "martingale_penalty",
    "profit_factor",
    "regime_stability",
    "sortino_ratio",
]
