"""Polymarket Telegram 推播 — 復用 market_monitor.telegram_zh 的 send_message.

訊息前綴：[POLY-{tier}] 鯨魚交易
格式對齊：不使用 Markdown 特殊字元以避免發送失敗（fallback plain text）.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from market_monitor.telegram_zh import send_message as _send_message

logger = logging.getLogger(__name__)


def format_whale_alert(
    *,
    tier: str,
    wallet_address: str,
    market_question: str,
    market_category: str = "",
    side: str,
    outcome: str,
    price: float | Decimal,
    size: float | Decimal,
    notional: float | Decimal,
    match_time: datetime | None = None,
    wallet_stats: dict | None = None,
    profile_extras: dict | None = None,  # 1.5b: specialist_categories, time_slice info
) -> str:
    """組成單一鯨魚交易的 Telegram 訊息.

    profile_extras 結構（皆為 optional）：
      {
        "specialist_categories": ["Politics"],
        "primary_category": "Politics",
        "is_consistent": True,
        "match_specialist": True,  # 此筆交易是否落在錢包專長類別
      }
    """
    wallet_short = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    time_str = (match_time or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 情境標記
    direction = "做多" if side == "BUY" else "做空"
    size_flag = " (大額)" if float(notional) >= 10000 else ""

    # 1.5b: header tag with specialist info
    extras = profile_extras or {}
    specialists = extras.get("specialist_categories") or []
    header_extra = ""
    if specialists:
        if extras.get("match_specialist"):
            header_extra = f"・{specialists[0]}專家 ✓"
        else:
            header_extra = f"・{specialists[0]}專家 (本筆非專長領域)"

    lines = [
        f"[POLY-{tier}{header_extra}] 鯨魚交易{size_flag}",
        f"錢包: {wallet_short} (Tier {tier})",
        f"市場: {market_question[:80]}",
    ]
    if market_category:
        lines.append(f"類別: {market_category}")
    lines.extend(
        [
            f"方向: {side} {outcome} @ {float(price):.4f}  ({direction})",
            f"金額: ${float(notional):,.0f}  (size: {float(size):,.2f})",
        ]
    )

    if wallet_stats:
        trade_count = int(wallet_stats.get("trade_count_90d", 0))
        win_rate = float(wallet_stats.get("win_rate", 0)) * 100
        cum_pnl = float(wallet_stats.get("cumulative_pnl", 0))
        avg_size = float(wallet_stats.get("avg_trade_size", 0))
        pnl_sign = "+" if cum_pnl >= 0 else "-"
        lines.extend(
            [
                "──────────────",
                f"錢包 90d 統計:",
                f"  交易數: {trade_count}",
                f"  勝率: {win_rate:.1f}%",
                f"  累積 PnL: {pnl_sign}${abs(cum_pnl):,.0f}",
                f"  平均尺寸: ${avg_size:,.0f}",
            ]
        )

    # 1.5b: 加入 specialist 詳情區塊
    if specialists or extras.get("primary_category"):
        lines.append("──────────────")
        if specialists:
            lines.append(f"專長類別: {', '.join(specialists)}")
        elif extras.get("primary_category"):
            lines.append(f"主要類別: {extras['primary_category']} (尚未達 specialist)")
        if extras.get("is_consistent") is True:
            lines.append("時間切片: 穩定 ✓")
        elif extras.get("is_consistent") is False:
            lines.append("時間切片: 不穩定（少數爆發）")

    lines.append(f"時間: {time_str}")
    return "\n".join(lines)


def format_tier_change(
    wallet_address: str,
    from_tier: str | None,
    to_tier: str,
    reason: str,
) -> str:
    wallet_short = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    arrow = " → "
    prev = from_tier or "(新)"
    return f"[POLY] 鯨魚層級變動\n錢包: {wallet_short}\n{prev}{arrow}{to_tier}\n理由: {reason}"


def send(text: str) -> bool:
    """發送訊息（復用 market_monitor telegram_zh）.

    回傳成功與否。實際執行仰賴環境變數 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID。
    """
    try:
        return _send_message(text, parse_mode="")  # plain text, avoid Markdown escape issues
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_whale_alert(
    *,
    tier: str,
    wallet_address: str,
    market_question: str,
    market_category: str = "",
    side: str,
    outcome: str,
    price: float | Decimal,
    size: float | Decimal,
    notional: float | Decimal,
    match_time: datetime | None = None,
    wallet_stats: dict | None = None,
    profile_extras: dict | None = None,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """便利入口：格式化 + 發送。dry_run=True 僅回傳文字不真送。"""
    text = format_whale_alert(
        tier=tier,
        wallet_address=wallet_address,
        market_question=market_question,
        market_category=market_category,
        side=side,
        outcome=outcome,
        price=price,
        size=size,
        notional=notional,
        match_time=match_time,
        wallet_stats=wallet_stats,
        profile_extras=profile_extras,
    )
    if dry_run:
        return True, text
    ok = send(text)
    return ok, text
