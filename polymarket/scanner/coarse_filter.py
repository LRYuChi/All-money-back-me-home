"""Coarse Filter — 第二階段：快速淘汰明顯無分析價值的錢包.

這一層不是要找出「好錢包」，而是要剔除「絕對不可能是好錢包的錢包」，大幅
減少下一階段（特徵計算）的計算量。

被淘汰的錢包不會永久排除——它們每天都會重新進入第一階段的候選池。一個錢包
今天被淘汰，不代表 30 天後不會因為累積更多交易而通過粗篩。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from polymarket.models import Position, Trade

logger = logging.getLogger(__name__)


@dataclass
class CoarseFilterResult:
    """粗篩結果。reasons 為空代表通過."""

    passed: bool
    reasons: list[str] = field(default_factory=list)


def apply_coarse_filter(
    wallet_address: str,
    trades: list[Trade],
    positions: list[Position],
    pre_reg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> CoarseFilterResult:
    """對單一錢包施加粗篩。回傳 (通過?, 失敗理由列表)."""
    now = now or datetime.now(timezone.utc)
    cfg = pre_reg["scanner"]["coarse_filter"]
    reasons: list[str] = []

    # 1. 最少交易數
    min_trades = int(cfg["min_trades_total"]["value"])
    if len(trades) < min_trades:
        reasons.append(f"insufficient_trades({len(trades)}<{min_trades})")

    # 2. 最近活動時效
    max_stale_days = int(cfg["max_days_since_last_trade"]["value"])
    if trades:
        last_trade = max(t.match_time for t in trades)
        days_since = (now - last_trade).days
        if days_since > max_stale_days:
            reasons.append(f"stale_activity({days_since}d>{max_stale_days}d)")
    else:
        reasons.append("no_trades")

    # 3. 累積 PnL（已結算倉位）不可顯著為負
    min_pnl = float(cfg["min_cumulative_pnl_usdc"]["value"])
    resolved_pnl = float(sum((p.cash_pnl for p in positions if p.is_resolved), Decimal(0)))
    if resolved_pnl < min_pnl:
        reasons.append(f"negative_pnl({resolved_pnl:.0f}<{min_pnl:.0f})")

    # 4. 市場集中度——避免抓到單一市場做市商
    max_concentration = float(cfg["max_market_concentration"]["value"])
    if trades:
        concentration = _market_concentration(trades)
        if concentration > max_concentration:
            reasons.append(f"market_maker_concentration({concentration:.2f}>{max_concentration:.2f})")

    return CoarseFilterResult(passed=not reasons, reasons=reasons)


def _market_concentration(trades: list[Trade]) -> float:
    """單一市場佔總成交額的最大比例 (0.0-1.0)."""
    by_market: dict[str, Decimal] = {}
    total = Decimal(0)
    for t in trades:
        notional = t.notional_usdc()
        by_market[t.market] = by_market.get(t.market, Decimal(0)) + notional
        total += notional
    if total == 0:
        return 0.0
    top = max(by_market.values())
    return float(top / total)
