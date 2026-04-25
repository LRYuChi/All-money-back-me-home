"""Order book microstructure signals — R52.

K-lines are lagging artifacts of order flow. Looking at the order book
itself at the moment of entry can confirm/deny the price-action signal.

Two signals:

  1. bid_ask_imbalance(book, depth=5) → float in [-1, 1]
     Sums size at top N bid & ask levels. Returns
     (bid_size - ask_size) / total. Positive = buyers stronger.

  2. large_order_pressure(recent_trades, threshold_usd) → float in [-1, 1]
     Looks at recent fills > threshold_usd. Returns
     (long_size - short_size) / total. Positive = market longs hitting bids.

The combine() helper:
  - imbalance × 0.6 + pressure × 0.4 → composite [-1, 1]
  - Caller compares against intended direction:
      * Strong agreement (>= 0.3 same direction) → confirm entry
      * Disagreement (< -0.3 opposite direction) → ABORT entry
      * Neutral → proceed (no signal either way)

Used as last-mile entry confirm in confirm_trade_entry. Default OFF
(SUPERTREND_ORDERBOOK_CONFIRM=1 to enable) — avoids extra REST latency
unless explicitly requested.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


# Confidence thresholds for combine()
CONFIRM_THRESHOLD = 0.3       # composite ≥ this AND same direction → confirm
ABORT_THRESHOLD = -0.3        # composite ≤ -this in opposite direction → abort


@dataclass(slots=True, frozen=True)
class OrderBookSignal:
    """Composite microstructure signal at one moment."""
    imbalance: float          # [-1, 1] from book depth
    pressure: float           # [-1, 1] from recent fills
    composite: float          # weighted combination
    n_bid_levels: int
    n_ask_levels: int
    n_recent_trades: int


def bid_ask_imbalance(
    book: dict, *, depth: int = 5,
) -> tuple[float, int, int]:
    """Sum top-N bid/ask sizes.

    Expects ccxt-format book dict:
      {"bids": [[price, size], ...], "asks": [[price, size], ...]}

    Returns (imbalance, n_bids_seen, n_asks_seen).
    Imbalance ∈ [-1, 1]: +1 = all bid, -1 = all ask, 0 = balanced.
    Returns 0.0 when book empty/malformed (defensive — caller treats
    no signal).
    """
    if not isinstance(book, dict):
        return 0.0, 0, 0
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return 0.0, 0, 0

    # Take up to `depth` levels
    bid_levels = bids[:depth]
    ask_levels = asks[:depth]

    bid_size = sum(_safe_size(lvl) for lvl in bid_levels)
    ask_size = sum(_safe_size(lvl) for lvl in ask_levels)

    total = bid_size + ask_size
    if total == 0:
        return 0.0, len(bid_levels), len(ask_levels)
    return (bid_size - ask_size) / total, len(bid_levels), len(ask_levels)


def _safe_size(level) -> float:
    """ccxt level is [price, size]. Defensive: tolerate malformed."""
    try:
        return float(level[1])
    except (IndexError, TypeError, ValueError):
        return 0.0


def large_order_pressure(
    recent_trades: Sequence[dict],
    *,
    threshold_usd: float = 50_000,
    max_lookback: int = 100,
) -> tuple[float, int]:
    """Pressure from large fills. Long fills (price >= ask of t-1) = +,
    Short fills (price <= bid of t-1) = -.

    ccxt trades dict format:
      {"side": "buy"|"sell", "amount": ..., "price": ...}
      "side": "buy" = market BUY (taker hits ask) → bullish pressure
      "side": "sell" = market SELL (taker hits bid) → bearish pressure

    Returns (pressure, n_trades_qualifying).
    """
    if not recent_trades:
        return 0.0, 0

    long_size = 0.0
    short_size = 0.0
    counted = 0
    # Walk most-recent first
    for trade in list(recent_trades)[:max_lookback]:
        if not isinstance(trade, dict):
            continue
        try:
            amount = float(trade.get("amount", 0) or 0)
            price = float(trade.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        notional = amount * price
        if notional < threshold_usd:
            continue

        side = trade.get("side", "")
        if side == "buy":
            long_size += notional
            counted += 1
        elif side == "sell":
            short_size += notional
            counted += 1

    total = long_size + short_size
    if total == 0:
        return 0.0, counted
    return (long_size - short_size) / total, counted


def combine(
    imbalance: float,
    pressure: float,
    *,
    imbalance_weight: float = 0.6,
    pressure_weight: float = 0.4,
) -> float:
    """Weighted composite. Default 60/40 in favor of book depth
    (live state) over recent trades (lagging by however long they took)."""
    return imbalance_weight * imbalance + pressure_weight * pressure


def evaluate(book: dict, recent_trades: Sequence[dict],
             *, depth: int = 5,
             threshold_usd: float = 50_000) -> OrderBookSignal:
    """Single-call helper: full microstructure signal."""
    imb, n_bid, n_ask = bid_ask_imbalance(book, depth=depth)
    pres, n_trades = large_order_pressure(
        recent_trades, threshold_usd=threshold_usd,
    )
    return OrderBookSignal(
        imbalance=imb,
        pressure=pres,
        composite=combine(imb, pres),
        n_bid_levels=n_bid,
        n_ask_levels=n_ask,
        n_recent_trades=n_trades,
    )


def should_confirm_entry(
    signal: OrderBookSignal, intended_side: str,
) -> tuple[bool, str]:
    """Decide whether the entry should proceed given the orderbook signal.

    Returns (proceed, reason).

    Logic:
      - composite STRONG agreement (>= CONFIRM in our direction) → proceed
      - composite STRONG disagreement (<= -ABORT in our direction) → ABORT
      - composite weak/neutral → proceed (no actionable signal)
    """
    composite = signal.composite
    is_long = (intended_side == "long")

    # For long: positive composite = book leans bid = supports long
    # For short: negative composite supports short
    in_favor = composite if is_long else -composite

    if in_favor <= ABORT_THRESHOLD:
        return False, (
            f"orderbook strongly against {intended_side}: "
            f"composite={composite:+.2f} (in_favor={in_favor:+.2f})"
        )
    if in_favor >= CONFIRM_THRESHOLD:
        return True, f"orderbook confirms {intended_side}: composite={composite:+.2f}"
    return True, f"orderbook neutral: composite={composite:+.2f}"


__all__ = [
    "OrderBookSignal",
    "bid_ask_imbalance",
    "large_order_pressure",
    "combine",
    "evaluate",
    "should_confirm_entry",
    "CONFIRM_THRESHOLD",
    "ABORT_THRESHOLD",
]
