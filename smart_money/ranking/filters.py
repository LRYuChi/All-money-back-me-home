"""硬門檻過濾:不通過直接淘汰,不進入分數計算.

所有門檻參數來自 `smart_money.config.settings.ranking`,可透過 env var override.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from smart_money.store.schema import Trade


@dataclass(slots=True, frozen=True)
class FilterResult:
    """篩選結果.passed=False 時 reason 說明為什麼."""

    passed: bool
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class FilterThresholds:
    min_sample_size: int = 50             # 已平倉交易最少筆數
    min_active_days: int = 30
    max_symbol_concentration: float = 0.80
    min_avg_holding_seconds: int = 300


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #
def _closed_trades(trades: list[Trade]) -> list[Trade]:
    """只看 close/decrease,因為那些才有真正的 PnL."""
    return [t for t in trades if t.action in ("close", "decrease")]


def active_days(trades: list[Trade]) -> float:
    """錢包活躍天數(首筆到尾筆的時間跨距)."""
    if len(trades) < 2:
        return 0.0
    ts = sorted(t.ts for t in trades)
    delta = ts[-1] - ts[0]
    return delta.total_seconds() / 86400


def symbol_concentration(trades: list[Trade]) -> float:
    """交易量最大的幣種 占比(以筆數為基準).

    回傳 0~1;單一幣種占 80% 以上視為過度集中.
    """
    if not trades:
        return 0.0
    counter = Counter(t.symbol for t in trades)
    most_common_count = counter.most_common(1)[0][1]
    return most_common_count / len(trades)


def avg_holding_seconds(trades: list[Trade]) -> float:
    """平均持倉時間(open → close).

    實作:把同一 (wallet, symbol, side) 的 open 與後續 close 配對,取 ts 差值.
    因為 HL fills 可能有部分平倉,我們保守地用「每次 close 的 ts 減去最近一次 open 的 ts」.
    """
    if len(trades) < 2:
        return 0.0

    # 依 (symbol, side) 分組,再依 ts 排序
    groups: dict[tuple[str, str], list[Trade]] = {}
    for t in trades:
        groups.setdefault((t.symbol, t.side), []).append(t)

    deltas: list[float] = []
    for g in groups.values():
        g_sorted = sorted(g, key=lambda x: x.ts)
        open_stack: list[datetime] = []
        for t in g_sorted:
            if t.action in ("open", "increase"):
                open_stack.append(t.ts)
            elif t.action in ("close", "decrease") and open_stack:
                opened_at = open_stack.pop(0)  # FIFO
                deltas.append((t.ts - opened_at).total_seconds())

    return sum(deltas) / len(deltas) if deltas else 0.0


# ------------------------------------------------------------------ #
# Main entry
# ------------------------------------------------------------------ #
def apply_filters(
    trades: list[Trade],
    *,
    thresholds: FilterThresholds | None = None,
) -> FilterResult:
    """套用所有硬門檻,回第一個未過的原因.

    通過所有檢查才回 passed=True.
    """
    t = thresholds or FilterThresholds()

    closed = _closed_trades(trades)
    if len(closed) < t.min_sample_size:
        return FilterResult(False, f"sample_size={len(closed)} < {t.min_sample_size}")

    days = active_days(trades)
    if days < t.min_active_days:
        return FilterResult(False, f"active_days={days:.1f} < {t.min_active_days}")

    conc = symbol_concentration(trades)
    if conc > t.max_symbol_concentration:
        return FilterResult(
            False, f"symbol_concentration={conc:.2f} > {t.max_symbol_concentration}",
        )

    avg_hold = avg_holding_seconds(trades)
    if 0 < avg_hold < t.min_avg_holding_seconds:
        return FilterResult(
            False, f"avg_holding={avg_hold:.0f}s < {t.min_avg_holding_seconds}s (HFT-like)",
        )

    return FilterResult(True)


__all__ = [
    "FilterResult",
    "FilterThresholds",
    "active_days",
    "apply_filters",
    "avg_holding_seconds",
    "symbol_concentration",
]
