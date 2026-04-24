"""Shadow paper-trade simulator (P4c).

Consumes `FollowOrder` from the aggregator and records paper trades in
`sm_paper_trades`. Does NOT touch any exchange.

Open/close matching
-------------------
Paper trades are keyed by `(source_wallet_id, symbol_okx)`. When the
FollowOrder is `action='open'`, we insert a new row; `action='close'`
matches the oldest open paper trade for that (wallet, symbol) pair and
stamps `exit_price` + `pnl` + `closed_at`.

For aggregated-mode orders (multiple source wallets):
  - Open: `source_wallet_id` is set to the FIRST source signal's wallet.
    `source_wallets` array stores all contributors for later audit.
  - Close: matched against the same first-wallet attribution. If that
    specific wallet closes but others are still long, we close our paper
    trade (per-signal close is the minimum-risk interpretation).

Exit attribution
----------------
`exit_reason`:
  - 'whale_close' — source wallet emitted CLOSE_* or SCALE_DOWN_*
  - 'reverse'     — source wallet flipped via REVERSE_*
  - 'aggregator_downsize' — (future, P5) — aggregator-level risk reduction

Entry/exit prices
-----------------
P4c uses the HL fill price directly (100% mirror, no slippage). P5 live
mode will substitute OKX mid at signal receipt time, and slippage will
show up in sm_live_trades as the delta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from smart_money.execution.mapper import SizeCheck, SymbolMapper
from smart_money.signals.types import FollowOrder, Signal, SignalType
from smart_money.store.db import TradeStore
from smart_money.store.schema import PaperTrade, SkippedSignal

logger = logging.getLogger(__name__)


# Signal types that count as "close the existing paper trade"
_CLOSE_SIGNAL_TYPES = {
    SignalType.CLOSE_LONG,
    SignalType.CLOSE_SHORT,
    SignalType.SCALE_DOWN_LONG,
    SignalType.SCALE_DOWN_SHORT,
    SignalType.REVERSE_TO_LONG,
    SignalType.REVERSE_TO_SHORT,
}


@dataclass(slots=True, frozen=True)
class SimulateResult:
    """What the simulator did with one FollowOrder. Exactly one of the three
    outcome fields is non-None."""

    opened_id: int | None = None         # newly inserted paper trade id
    closed_id: int | None = None         # the paper trade id we closed
    skipped_reason: str | None = None    # why we did nothing


class ShadowSimulator:
    """Stateless coordinator: given a FollowOrder, pick open/close/skip and
    persist via the store."""

    def __init__(
        self,
        store: TradeStore,
        symbol_mapper: SymbolMapper,
        *,
        signal_mode: str = "independent",
    ) -> None:
        self._store = store
        self._mapper = symbol_mapper
        self._signal_mode = signal_mode

    # ---------------------------------------------------------------- #
    # Public entry
    # ---------------------------------------------------------------- #
    def process(self, order: FollowOrder) -> SimulateResult:
        """Route a FollowOrder to either open / close a paper trade or skip.

        Records SkippedSignal audit rows for every skip path so we can
        compare "signals received" vs "signals we could actually act on".
        """
        primary_signal = order.source_signals[0]
        size_check = self._mapper.check(
            primary_signal.symbol_hl,
            size_coin=order.size_coin,
            px=primary_signal.px,
        )
        if not size_check.ok:
            return self._skip(primary_signal, size_check, order.size_coin)

        # Now we have a valid OKX symbol and notional above min.
        if order.action == "open":
            return self._open(order, size_check)
        if order.action == "close":
            return self._close(order, size_check)
        # action == "scale" — P4c treats scale-up as "just log, no new entry"
        # to avoid size-stacking bugs in shadow mode. P5 properly adjusts
        # the live position.
        self._record_skip(
            primary_signal, reason="scale_not_simulated_in_shadow",
            detail={"action": "scale", "size_coin": order.size_coin},
        )
        return SimulateResult(skipped_reason="scale_not_simulated_in_shadow")

    # ---------------------------------------------------------------- #
    # Open
    # ---------------------------------------------------------------- #
    def _open(self, order: FollowOrder, size: SizeCheck) -> SimulateResult:
        primary = order.source_signals[0]
        # Don't stack opens for the same (wallet, symbol) — defensive.
        # If classifier says "SCALE_UP" the action would be "scale", not "open";
        # duplicate open is only possible if the state machine drifted.
        if self._store.find_open_paper_trades(primary.wallet_id, size.okx_symbol):
            self._record_skip(
                primary, reason="duplicate_open",
                detail={"okx_symbol": size.okx_symbol},
            )
            return SimulateResult(skipped_reason="duplicate_open")

        paper = PaperTrade(
            source_wallet_id=primary.wallet_id,
            symbol=size.okx_symbol or "",
            side=_follow_side_to_position_side(order.side),
            size=order.size_coin,
            entry_price=primary.px,
            opened_at=datetime.fromtimestamp(
                primary.source_event.ts_hl_fill_ms / 1000, tz=timezone.utc,
            ),
            signal_latency_ms=primary.total_latency_ms,
            signal_mode=self._signal_mode,
            source_wallets=[s.wallet_id for s in order.source_signals],
        )
        tid = self._store.open_paper_trade(paper)
        logger.info(
            "shadow OPEN id=%d wallet=%s %s %s size=%s px=%s lat_ms=%d",
            tid, primary.wallet_address[:10], size.okx_symbol, paper.side,
            paper.size, paper.entry_price, paper.signal_latency_ms,
        )
        return SimulateResult(opened_id=tid)

    # ---------------------------------------------------------------- #
    # Close
    # ---------------------------------------------------------------- #
    def _close(self, order: FollowOrder, size: SizeCheck) -> SimulateResult:
        primary = order.source_signals[0]
        opens = self._store.find_open_paper_trades(primary.wallet_id, size.okx_symbol)
        if not opens:
            self._record_skip(
                primary, reason="close_without_open",
                detail={"okx_symbol": size.okx_symbol},
            )
            return SimulateResult(skipped_reason="close_without_open")

        # FIFO: close the oldest open
        paper = opens[0]
        assert paper.id is not None  # guaranteed by store

        exit_px = primary.px
        pnl = _compute_pnl(paper.side, paper.size, paper.entry_price, exit_px)
        closed_at = datetime.fromtimestamp(
            primary.source_event.ts_hl_fill_ms / 1000, tz=timezone.utc,
        )
        exit_reason = _exit_reason_from_signal(primary)
        ok = self._store.close_paper_trade(
            paper.id, exit_price=exit_px, pnl=pnl,
            closed_at=closed_at, exit_reason=exit_reason,
        )
        if not ok:
            # Someone else closed it between our read and write (race). Log.
            logger.warning(
                "shadow CLOSE race: paper_id=%d already closed",
                paper.id,
            )
            return SimulateResult(skipped_reason="close_race")

        logger.info(
            "shadow CLOSE id=%d wallet=%s %s exit=%s pnl=%+.2f reason=%s",
            paper.id, primary.wallet_address[:10], paper.symbol,
            exit_px, pnl, exit_reason,
        )
        return SimulateResult(closed_id=paper.id)

    # ---------------------------------------------------------------- #
    # Skip helpers
    # ---------------------------------------------------------------- #
    def _skip(
        self,
        signal: Signal,
        size: SizeCheck,
        size_coin: float,
    ) -> SimulateResult:
        self._record_skip(
            signal, reason=size.reason,
            detail={
                "okx_symbol": size.okx_symbol,
                "size_coin": size_coin,
                "notional_usd": size.notional_usd,
                "min_notional_usd": (size.entry.min_notional_usd if size.entry else None),
            },
        )
        return SimulateResult(skipped_reason=size.reason)

    def _record_skip(
        self,
        signal: Signal,
        *,
        reason: str,
        detail: dict,
    ) -> None:
        self._store.record_skipped_signal(SkippedSignal(
            wallet_id=signal.wallet_id,
            wallet_address=signal.wallet_address,
            symbol_hl=signal.symbol_hl,
            reason=reason,
            signal_latency_ms=signal.total_latency_ms,
            direction_raw=signal.source_event.direction_raw,
            hl_trade_id=signal.source_event.hl_trade_id,
            detail=detail,
        ))


# ================================================================== #
# Helpers
# ================================================================== #
def _follow_side_to_position_side(side: str) -> str:
    """FollowOrder uses OKX order side (buy/sell); PaperTrade uses position
    side (long/short)."""
    return "long" if side == "buy" else "short"


def _compute_pnl(
    side: str,
    size: float,
    entry_price: float,
    exit_price: float,
) -> float:
    """Simple unlevered PnL — for shadow accounting. P5 live will use OKX
    fills + fees."""
    if side == "long":
        return (exit_price - entry_price) * size
    return (entry_price - exit_price) * size


def _exit_reason_from_signal(signal: Signal) -> str:
    if signal.signal_type in (SignalType.REVERSE_TO_LONG, SignalType.REVERSE_TO_SHORT):
        return "reverse"
    if signal.signal_type in (SignalType.SCALE_DOWN_LONG, SignalType.SCALE_DOWN_SHORT):
        return "whale_scale_down"
    return "whale_close"


__all__ = ["ShadowSimulator", "SimulateResult"]
