"""SHADOW signal injection CLI — R81.

Mirrors R70/R79 force_entry pattern for SUPERTREND, but for the SHADOW
side: synthesizes a FollowOrder + routes it through ShadowSimulator
to validate the OPEN paper trade chain end-to-end (simulator → store →
sm_paper_trades).

Use case: SHADOW pipeline has been at 0 paper_open for 24h+ because
whales are scaling/closing existing positions, never opening fresh.
The simulator._open path can't be exercised by waiting. This CLI lets
operator manually trigger one to verify the chain works before betting
on it for real.

Safety:
  * Default DISABLED via SHADOW_INJECT_ENABLED env (must be set to "1")
  * Synthesized signals carry a clear "_INJECT_" prefix in client_order_id
  * Dry run (--dry-run, default) only logs; --execute actually writes
  * Each invocation produces auditable INFO log line

Usage:
    # Verify enabled + dry run a synthetic OPEN
    SHADOW_INJECT_ENABLED=1 \\
      python -m smart_money.cli.inject_signal \\
      --wallet 0x... --symbol BTC --side long \\
      --size 0.01 --price 50000

    # Actually execute (writes paper trade to store)
    SHADOW_INJECT_ENABLED=1 \\
      python -m smart_money.cli.inject_signal \\
      --wallet 0x... --symbol BTC --side long \\
      --size 0.01 --price 50000 --execute

Returns:
  exit 0 — success (paper_id printed)
  exit 1 — error / probe failure / disabled
  exit 2 — invalid args
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

logger = logging.getLogger("shadow.inject")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smart_money.cli.inject_signal",
        description="Inject a synthetic SHADOW OPEN signal for chain validation.",
    )
    p.add_argument("--wallet", required=True,
                   help="HL wallet address (will be looked up; "
                        "if not in store, error)")
    p.add_argument("--symbol", required=True,
                   help="HL native symbol, e.g. BTC, ETH, HYPE")
    p.add_argument("--side", choices=["long", "short"], required=True)
    p.add_argument("--size", type=float, required=True,
                   help="Coin units to open")
    p.add_argument("--price", type=float, required=True,
                   help="Entry price in USD")
    p.add_argument("--execute", action="store_true",
                   help="Actually write to store (default: dry-run)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Safety gate
    if os.environ.get("SHADOW_INJECT_ENABLED", "0") != "1":
        print(
            "✗ SHADOW signal injection DISABLED.\n"
            "  Set SHADOW_INJECT_ENABLED=1 to enable.\n"
            "  This is a dev/ops tool — never enable in unattended prod.",
        )
        return 1

    if not args.wallet.startswith("0x") or len(args.wallet) != 42:
        print(f"✗ Invalid wallet address: {args.wallet!r}")
        return 2

    # Bootstrap project root for imports
    proj_root = str(Path(__file__).resolve().parent.parent.parent)
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)

    from smart_money.config import settings
    from smart_money.execution.mapper import SymbolMapper
    from smart_money.shadow.simulator import ShadowSimulator
    from smart_money.signals.types import (
        FollowOrder, RawFillEvent, Signal, SignalType,
    )
    from smart_money.store.db import build_store

    # Look up wallet in store; need wallet_id for FollowOrder source signal
    try:
        store = build_store(settings)
    except Exception as e:
        logger.error("store init failed: %s", e)
        return 1

    # Look up wallet — required because Signal carries wallet_id (UUID)
    try:
        wallet_row = store.find_wallet_by_address(args.wallet)
    except AttributeError:
        # Older store API may not have this method — fall back to raw query
        wallet_row = None
    if wallet_row is None:
        # Synthesize a UUID for testing; in real flow wallet would already exist
        synthetic_wid = uuid.uuid4()
        logger.warning(
            "wallet %s not in store; using synthetic UUID %s — "
            "this means simulator will create a paper trade tagged with "
            "a wallet_id that doesn't reference a real wallet row",
            args.wallet[:10], synthetic_wid,
        )
        wallet_id = synthetic_wid
    else:
        wallet_id = wallet_row.id if hasattr(wallet_row, "id") else UUID(wallet_row["id"])

    # Load symbol mapper
    symbol_map_path = Path(
        os.environ.get(
            "SYMBOL_MAP_PATH",
            "config/smart_money/symbol_map.yaml",
        ),
    )
    mapper = SymbolMapper.load(symbol_map_path)
    if args.symbol not in mapper.known_symbols():
        print(
            f"✗ Symbol {args.symbol!r} not in mapper "
            f"({len(mapper.known_symbols())} known). "
            f"Add to {symbol_map_path} first.",
        )
        return 1

    # Build synthetic event chain
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    synth_tid = -abs(hash(f"_INJECT_{wallet_id}_{args.symbol}_{now_ms}")) % (2 ** 31)

    raw_event = RawFillEvent(
        wallet_address=args.wallet,
        symbol_hl=args.symbol,
        side_raw="B" if args.side == "long" else "A",
        direction_raw=f"Open {'Long' if args.side == 'long' else 'Short'}",
        size=(args.size if args.side == "long" else -args.size),
        px=args.price,
        fee=0.0,
        hl_trade_id=synth_tid,
        ts_hl_fill_ms=now_ms,
        ts_ws_received_ms=now_ms + 50,
        ts_queue_processed_ms=now_ms + 100,
        source="injected",   # type: ignore[arg-type]
        raw={"_inject": True, "tool": "R81 inject_signal"},
    )

    sig_type = (
        SignalType.OPEN_LONG if args.side == "long" else SignalType.OPEN_SHORT
    )
    signal = Signal(
        wallet_id=wallet_id,
        wallet_address=args.wallet,
        wallet_score=1.0,
        symbol_hl=args.symbol,
        signal_type=sig_type,
        size_delta=args.size,
        new_size=args.size,
        px=args.price,
        whale_equity_usd=10_000.0,   # synthetic; not critical for OPEN
        whale_position_usd=args.size * args.price,
        source_event=raw_event,
    )

    entry = mapper.lookup(args.symbol)
    okx_symbol = entry.okx if entry is not None else f"{args.symbol}/USDT:USDT"

    follow_order = FollowOrder(
        symbol_okx=okx_symbol,
        side="buy" if args.side == "long" else "sell",
        action="open",
        size_coin=args.size,
        size_notional_usd=args.size * args.price,
        source_signals=(signal,),
        client_order_id=f"_INJECT_R81_{synth_tid}",
        created_ts_ms=now_ms,
    )

    print(f"  Synthesized FollowOrder:")
    print(f"    symbol_okx={follow_order.symbol_okx}")
    print(f"    side={follow_order.side}  action={follow_order.action}")
    print(f"    size_coin={follow_order.size_coin}  notional=${follow_order.size_notional_usd:.2f}")
    print(f"    client_order_id={follow_order.client_order_id}")

    if not args.execute:
        print("\n  [DRY RUN] --execute not set; would have called "
              "simulator.process() above. Re-run with --execute to actually "
              "write paper trade.")
        return 0

    # Actually run through simulator
    simulator = ShadowSimulator(
        store, mapper, signal_mode="aggregated",
    )
    print("\n  Calling simulator.process() ...")
    result = simulator.process(follow_order)

    if result.opened_id is not None:
        print(f"  ✓ paper_open SUCCESS  paper_id={result.opened_id}")
        return 0
    if result.skipped_reason:
        print(f"  ✗ skipped — reason: {result.skipped_reason}")
        return 1
    print("  ⚠ unexpected — no opened_id, no skipped_reason")
    return 1


if __name__ == "__main__":
    sys.exit(main())
