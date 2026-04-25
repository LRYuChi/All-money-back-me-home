"""Shadow mode daemon — Phase 4.

P4a: WS listener + REST reconciler → RawFillEvent queue.
P4b (current): classifier turns RawFillEvent → Signal, persists position state.
P4c (pending): aggregator + shadow simulator.

Run:
    python -m smart_money.cli.shadow [--whitelist path/to/override.yaml]

Addresses to subscribe come from the dynamic whitelist:
    1. latest sm_rankings top N (config: ranking.whitelist_size)
    2. + manual include (from --whitelist yaml)
    3. - manual exclude, stale-no-fills, warmup-demoted

Graceful shutdown on SIGINT/SIGTERM. Exit codes: 0=clean, 1=no wallets,
2=WS failed to connect, 3=store init failed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from uuid import UUID

from fusion import (
    CachedContextProvider,
    HLBTCContextProvider,
    Regime,
    SignalFuser,
    StaticContextProvider,
    detect_regime,
    load_weights,
    yfinance_vix_provider,
)
from execution.pending_orders import (
    NoOpIntentDeduper,
    WindowedIntentDeduper,
    build_queue,
    make_intent_callback,
)
from fusion.regime import MarketContext
from shared.signals.adapters import from_smart_money
from shared.signals.history import SignalHistoryWriter, build_writer, record_safe
from smart_money.config import settings
from strategy_engine import StrategyRuntime, build_registry
from smart_money.execution.mapper import SymbolMapper
from smart_money.scanner.reconciler import FillsReconciler
from smart_money.scanner.realtime import HLFillsListener
from smart_money.shadow.simulator import ShadowSimulator
from smart_money.signals.aggregator import AggregationMode, SignalAggregator
from smart_money.signals.classifier import classify
from smart_money.signals.dispatcher import now_ms
from smart_money.signals.types import RawFillEvent
from smart_money.signals.whitelist import (
    WhitelistEntry,
    build_whitelist,
    load_manual_override,
)
from smart_money.store.db import TradeStore, build_store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.shadow",
        description="Shadow mode daemon — subscribe to HL WS, classify fills, log signals.",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=Path("config/smart_money/whitelist_manual.yaml"),
        help="Manual include/exclude override YAML (default: config/smart_money/whitelist_manual.yaml).",
    )
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        help="Force subscribe this address (repeatable). Bypasses ranking lookup.",
    )
    parser.add_argument(
        "--whitelist-size", type=int, default=settings.ranking.whitelist_size,
        help=f"Top N from latest ranking (default {settings.ranking.whitelist_size}).",
    )
    parser.add_argument(
        "--freshness-days", type=int, default=14,
        help="Wallets with no fill in this window are demoted to watch-only.",
    )
    parser.add_argument(
        "--warmup-hours", type=int, default=None,
        help="New wallets (first_seen within this window) demoted to watch-only.",
    )
    parser.add_argument(
        "--reconciler-interval", type=int, default=60,
        help="Seconds between REST reconciler sweeps.",
    )
    parser.add_argument(
        "--reconciler-lookback", type=int, default=300,
        help="Reconciler lookback window in seconds.",
    )
    parser.add_argument(
        "--aggregation-mode", choices=["independent", "aggregated"],
        default="aggregated",
        help="Signal aggregation (default aggregated: merge multi-wallet OPENs).",
    )
    parser.add_argument(
        "--aggregation-window-sec", type=int, default=300,
        help="Aggregated-mode accumulation window (default 300s).",
    )
    parser.add_argument(
        "--min-wallets-for-signal", type=int, default=2,
        help="Aggregated mode: min distinct wallets to emit OPEN (default 2).",
    )
    parser.add_argument(
        "--symbol-map", type=Path,
        default=Path("config/smart_money/symbol_map.yaml"),
        help="HL → OKX symbol mapping YAML.",
    )
    parser.add_argument(
        "--strategies", action="store_true",
        help="Enable Phase D/E rule chain (fuser + strategy runtime). "
             "When set, requires config/fusion/weights.yaml + at least one "
             "enabled row in `strategies` table.",
    )
    parser.add_argument(
        "--strategy-eval-interval", type=int, default=30,
        help="Seconds between strategy_eval ticks (default 30).",
    )
    parser.add_argument(
        "--shadow-capital", type=float, default=10_000.0,
        help="Notional capital used for fixed_pct sizing (default 10000).",
    )
    # R72: cold-start warmup
    parser.add_argument(
        "--skip-warmup", action="store_true",
        help="Skip per-wallet clearinghouseState warmup at startup. "
             "By default the daemon seeds prev_position from HL "
             "current state for each whitelist wallet, eliminating "
             "the cold_start_drift skip on first observed Close/Reverse. "
             "Disable for fast dev startup.",
    )
    parser.add_argument(
        "--warmup-timeout-sec", type=float, default=30.0,
        help="Per-wallet timeout for clearinghouseState fetch (default 30s).",
    )
    parser.add_argument(
        "--real-market-context", action="store_true",
        help="Use HLBTCContextProvider for live regime detection. Without "
             "this flag the daemon uses an empty MarketContext (regime=UNKNOWN).",
    )
    parser.add_argument(
        "--enable-vix", action="store_true",
        help="When --real-market-context, also pull ^VIX from yfinance "
             "(needs yfinance installed in the container).",
    )
    parser.add_argument(
        "--context-cache-ttl", type=int, default=300,
        help="Seconds to cache MarketContext lookups (default 300).",
    )
    parser.add_argument(
        "--intent-mode",
        choices=["shadow", "paper", "live", "notify"],
        default="shadow",
        help="Execution mode tagged on every pending_order produced by "
             "the strategy runtime (default shadow).",
    )
    parser.add_argument(
        "--intent-dedup-window-sec",
        type=float,
        default=60.0,
        help="Round 44 intent dedup: skip a (strategy, symbol, side) "
             "intent if an identical one was enqueued within N seconds. "
             "0 = disabled. Default 60s — covers double-fire from "
             "WebSocket reconnects, signal aggregator double-counts, "
             "and tight-cluster wallet events.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def _build_deduper(window_sec: float):
    """Round 45: pick the right IntentDeduper for the configured window.

    window_sec ≤ 0 → NoOp (dedup disabled). Negative isn't a meaningful
    "even more disabled"; we treat it identically to 0 to keep the CLI
    forgiving.

    Factored out for testability: lets the wiring test verify type
    selection without spinning up the full runtime.
    """
    if window_sec > 0:
        return WindowedIntentDeduper(window_sec=window_sec)
    return NoOpIntentDeduper()


def _maybe_build_strategy_runtime(args: argparse.Namespace) -> StrategyRuntime | None:
    """Build StrategyRuntime if --strategies is set + at least one strategy
    is enabled. Returns None to fall back to legacy daemon behaviour."""
    if not args.strategies:
        return None

    from pathlib import Path
    from fusion import DEFAULT_WEIGHTS_PATH
    weights_path = Path(DEFAULT_WEIGHTS_PATH)
    if not weights_path.exists():
        logger.warning(
            "strategy runtime: %s not found — skipping strategies", weights_path,
        )
        return None

    try:
        weights = load_weights(weights_path)
    except Exception as e:
        logger.error("strategy runtime: weights load failed: %s — skipping", e)
        return None

    try:
        registry = build_registry(settings)
    except Exception as e:
        logger.error("strategy runtime: registry init failed: %s — skipping", e)
        return None

    active = registry.list_active()
    if not active:
        logger.warning(
            "strategy runtime: no active strategies in registry — skipping. "
            "Upsert one with `python -c 'from strategy_engine import ...'`.",
        )
        return None

    fuser = SignalFuser(weights)
    capital = args.shadow_capital

    # Round 15: real MarketContext provider — pulls BTC daily candles from HL,
    # computes MA200 / slope / vol / DD, optionally pulls VIX from yfinance.
    # Wrapped in 5-min TTL cache so we don't hammer HL each strategy tick.
    # Falls back to StaticContextProvider(SIDEWAYS_HIGH_VOL) if HL init fails,
    # so daemon still runs (predictable but conservative regime).
    context_provider = _build_context_provider(args)

    def _regime_provider() -> Regime:
        try:
            ctx = context_provider.get()
            return detect_regime(ctx)
        except Exception as e:
            logger.warning(
                "regime_provider: failed (%s) — fallback SIDEWAYS_HIGH_VOL", e,
            )
            return Regime.SIDEWAYS_HIGH_VOL

    # Round 16: persist intents into pending_orders queue. Mode comes from
    # CLI flag — `shadow` for first deployment, `paper`/`live` after Phase F
    # adapters land. NoOp queue (when DB not configured) just logs.
    queue = build_queue(settings)

    # Round 45: wire intent dedup so a strategy double-firing within
    # window_sec doesn't open two positions. window=0 disables.
    deduper = _build_deduper(args.intent_dedup_window_sec)

    on_intent = make_intent_callback(
        queue, mode=args.intent_mode, deduper=deduper,
    )

    runtime = StrategyRuntime(
        registry=registry,
        fuser=fuser,
        regime_provider=_regime_provider,
        capital_provider=lambda: capital,
        on_intent=on_intent,
    )
    logger.info(
        "strategy runtime: enabled (%d active strategies, capital=$%.0f, "
        "context_provider=%s, queue=%s, intent_mode=%s, "
        "dedup=%s window=%.0fs)",
        len(active), capital, type(context_provider).__name__,
        type(queue).__name__, args.intent_mode,
        type(deduper).__name__, args.intent_dedup_window_sec,
    )
    return runtime


def _build_context_provider(args: argparse.Namespace):
    """Construct MarketContextProvider per args. Falls back to Static
    when HL SDK init fails (so daemon stays usable in offline tests)."""
    if not args.real_market_context:
        # Conservative deterministic default — what daemon used pre-round-15
        return StaticContextProvider(MarketContext())  # all None → UNKNOWN regime

    try:
        from hyperliquid.info import Info
        info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
    except Exception as e:
        logger.warning(
            "real_market_context requested but HL Info init failed (%s) — "
            "falling back to StaticContextProvider", e,
        )
        return StaticContextProvider(MarketContext())

    vix_fn = yfinance_vix_provider if args.enable_vix else None
    upstream = HLBTCContextProvider(info, vix_provider=vix_fn)
    return CachedContextProvider(upstream, ttl_seconds=args.context_cache_ttl)


def _resolve_whitelist(
    args: argparse.Namespace,
    store: TradeStore,
) -> list[WhitelistEntry]:
    """Build the active whitelist from CLI args + store state."""
    # --address overrides: go direct without ranking logic.
    if args.address:
        entries: list[WhitelistEntry] = []
        for addr in args.address:
            w = store.get_wallet_by_address(addr.lower())
            if w is None:
                logger.warning("--address %s not in sm_wallets — skip", addr[:10])
                continue
            entries.append(WhitelistEntry(
                wallet_id=w.id, address=w.address, score=0.0, rank=None,
                source="manual_include", is_tradeable=True, demotion_reason="none",
            ))
        return entries

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    override = load_manual_override(args.whitelist)

    warmup_cutoff = None
    if args.warmup_hours is not None:
        warmup_cutoff = now - timedelta(hours=args.warmup_hours)

    return build_whitelist(
        store,
        as_of=now,
        whitelist_size=args.whitelist_size,
        freshness_days=args.freshness_days,
        override=override,
        warmup_cutoff=warmup_cutoff,
    )


async def _drain_loop(
    queue: asyncio.Queue[RawFillEvent],
    store: TradeStore,
    address_to_wallet: dict[str, tuple[UUID, float, bool]],  # (id, score, is_tradeable)
    aggregator: SignalAggregator,
    simulator: ShadowSimulator,
    history_writer: SignalHistoryWriter,
    strategy_runtime: StrategyRuntime | None,
    stop: asyncio.Event,
) -> None:
    """Full pipeline: RawFillEvent → classifier → aggregator → simulator.

    Non-tradeable (watch-only) wallets run through the classifier (so their
    position state stays fresh for recovery detection) but do NOT feed the
    aggregator/simulator.
    """
    while not stop.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        info = address_to_wallet.get(event.wallet_address.lower())
        if info is None:
            logger.debug(
                "drain: fill for non-whitelisted wallet %s — ignoring",
                event.wallet_address[:10],
            )
            continue
        wallet_id, wallet_score, is_tradeable = info

        prev = store.get_position(wallet_id, event.symbol_hl)
        classify_result = classify(
            event, prev=prev, wallet_id=wallet_id, wallet_score=wallet_score,
        )
        # Always persist updated state (even on skip, 'flat' state becomes our prev)
        store.upsert_position(classify_result.new_position)

        if classify_result.skipped is not None:
            store.record_skipped_signal(classify_result.skipped)
            logger.info(
                "skip wallet=%s sym=%s reason=%s dir=%r tid=%d",
                event.wallet_address[:10], event.symbol_hl,
                classify_result.skipped.reason, event.direction_raw,
                event.hl_trade_id,
            )
            continue

        sig = classify_result.signal
        assert sig is not None
        logger.info(
            "signal wallet=%s sym=%s type=%s size=%s→%s px=%s lat_ms=%d",
            event.wallet_address[:10], event.symbol_hl, sig.signal_type.value,
            round(sig.size_delta, 4), round(sig.new_size, 4), sig.px,
            sig.total_latency_ms,
        )

        # Dual-write to signal_history for L7 reflection loop consumption.
        # Failure is logged but never propagated — history is observability,
        # not the primary pipeline. Watch-only wallets still count: we want
        # their signals in history for recovery-detection analysis.
        universal = from_smart_money(sig)
        record_safe(history_writer, universal)

        # Feed Phase D/E rule chain: fuser + strategy runtime (round 14).
        # Errors swallowed: rule chain failure must not break SM aggregator.
        if strategy_runtime is not None:
            try:
                strategy_runtime.ingest(universal)
            except Exception as e:
                logger.warning("strategy_runtime.ingest failed: %s", e)

        if not is_tradeable:
            # Watch-only: state updated, no shadow trade
            continue

        for order in aggregator.ingest(sig, now_ms=now_ms()):
            simulator.process(order)


async def _aggregator_flush_loop(
    aggregator: SignalAggregator,
    stop: asyncio.Event,
    interval_sec: int = 30,
) -> None:
    """Periodically drop expired aggregation buckets (prevents old signals
    from being combined with much-later ones)."""
    while not stop.is_set():
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break
        aggregator.flush_expired(now_ms=now_ms())


async def _strategy_eval_loop(
    runtime: StrategyRuntime,
    stop: asyncio.Event,
    interval_sec: int = 30,
) -> None:
    """Periodically run StrategyRuntime.evaluate_all() — fuses buffered signals
    + dispatches StrategyIntents via on_intent callback. P4c daemon runs this
    in shadow mode (intents only logged); Phase F live daemon will swap
    on_intent to push pending_orders."""
    while not stop.is_set():
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break
        try:
            intents = runtime.evaluate_all()
            if intents:
                logger.info("strategy_eval: %d intent(s) fired", len(intents))
        except Exception as e:
            logger.warning("strategy_eval failed: %s", e)


# =================================================================== #
# R72: cold-start warmup — seed prev_position from HL current state
# =================================================================== #
def _parse_clearinghouse_positions(
    state: dict,
    wallet_id: UUID,
    now,
) -> list:
    """Parse HL clearinghouseState response → list[WalletPosition].

    HL response shape (relevant slice):
      {
        "assetPositions": [
          {"type": "oneWay",
           "position": {"coin": "BTC", "szi": "0.5", "entryPx": "50000.0", ...}},
          ...
        ],
        ...
      }

    `szi` is signed: positive = long, negative = short, exact zero = flat
    (which HL usually omits — we don't insert flat).
    """
    from datetime import datetime as _dt, timezone as _tz
    from smart_money.store.schema import WalletPosition

    if not isinstance(state, dict):
        return []
    asset_positions = state.get("assetPositions") or []
    if not isinstance(asset_positions, list):
        return []

    ts = now if isinstance(now, _dt) else _dt.now(_tz.utc)
    out = []
    for ap in asset_positions:
        if not isinstance(ap, dict):
            continue
        pos = ap.get("position")
        if not isinstance(pos, dict):
            continue
        coin = pos.get("coin")
        szi_raw = pos.get("szi")
        entry_raw = pos.get("entryPx")
        if not coin or szi_raw is None:
            continue
        try:
            szi = float(szi_raw)
        except (TypeError, ValueError):
            continue
        if abs(szi) < 1e-9:
            continue   # flat — skip
        side = "long" if szi > 0 else "short"
        try:
            entry_px = float(entry_raw) if entry_raw is not None else None
        except (TypeError, ValueError):
            entry_px = None
        out.append(WalletPosition(
            wallet_id=wallet_id,
            symbol=str(coin),
            side=side,
            size=abs(szi),
            avg_entry_px=entry_px,
            last_updated_ts=ts,
        ))
    return out


def _warmup_wallet_positions(
    store, hl_client, whitelist, *,
    timeout_sec: float = 30.0,
) -> dict:
    """For each whitelist wallet, fetch HL clearinghouseState and
    upsert any open positions as prev_position seeds.

    Returns a summary dict for logging:
      {wallets_seeded: N, positions_seeded: N, fetch_errors: N,
       per_wallet: [{address, n_positions, error}]}

    Defensive: any wallet fetch failure logs + continues (other wallets
    still get seeded). Daemon startup must not block on a single bad wallet.
    """
    from datetime import datetime as _dt, timezone as _tz

    now = _dt.now(_tz.utc)
    summary = {
        "wallets_seeded": 0,
        "positions_seeded": 0,
        "fetch_errors": 0,
        "per_wallet": [],
    }

    for entry in whitelist:
        info = {"address": entry.address[:10] + "…", "n_positions": 0,
                "error": None}
        try:
            state = hl_client.get_current_state(entry.address)
        except Exception as e:
            info["error"] = str(e)[:100]
            summary["fetch_errors"] += 1
            summary["per_wallet"].append(info)
            logger.warning(
                "warmup: wallet %s clearinghouse_state failed: %s",
                entry.address[:10], e,
            )
            continue

        positions = _parse_clearinghouse_positions(
            state, entry.wallet_id, now,
        )
        for pos in positions:
            try:
                store.upsert_position(pos)
            except Exception as e:
                logger.warning(
                    "warmup: upsert failed for %s/%s: %s",
                    entry.address[:10], pos.symbol, e,
                )
                continue
            info["n_positions"] += 1
            summary["positions_seeded"] += 1
        if info["n_positions"] > 0:
            summary["wallets_seeded"] += 1
        summary["per_wallet"].append(info)

    return summary


async def run_shadow_daemon(args: argparse.Namespace) -> int:
    """Main async entry. Returns exit code."""
    try:
        store = build_store(settings)
    except Exception as e:
        logger.error("store init failed: %s", e)
        return 3

    whitelist = _resolve_whitelist(args, store)
    if not whitelist:
        logger.error("whitelist is empty — nothing to subscribe")
        return 1

    tradeable = [e for e in whitelist if e.is_tradeable]
    watch_only = [e for e in whitelist if not e.is_tradeable]
    logger.info(
        "shadow daemon: %d tradeable, %d watch-only",
        len(tradeable), len(watch_only),
    )
    for e in watch_only:
        logger.info(
            "  watch-only: %s rank=%s reason=%s",
            e.address[:10], e.rank, e.demotion_reason,
        )

    # Subscribe ALL (tradeable + watch-only) — state machine still tracks
    # non-tradeable wallets so we can detect recovery.
    addresses = [e.address for e in whitelist]
    address_to_wallet = {
        e.address.lower(): (e.wallet_id, e.score, e.is_tradeable)
        for e in whitelist
    }

    # Symbol mapper — must load successfully (empty = all symbols rejected)
    mapper = SymbolMapper.load(args.symbol_map)
    if not mapper.known_symbols():
        logger.warning(
            "symbol map is empty (%s) — every signal will be skipped as "
            "unknown_symbol. Populate the yaml to enable shadowing.",
            args.symbol_map,
        )

    aggregator = SignalAggregator(
        mode=args.aggregation_mode,
        window_sec=args.aggregation_window_sec,
        min_wallets=args.min_wallets_for_signal,
    )
    simulator = ShadowSimulator(
        store, mapper, signal_mode=args.aggregation_mode,
    )

    # L7 observability: dual-write every classified signal to signal_history.
    # Writer picks itself based on settings (Postgres > Supabase > NoOp).
    history_writer = build_writer(settings)

    # Phase D + E: build StrategyRuntime if --strategies flag is set + at
    # least one strategy is enabled. Otherwise skip the entire chain
    # (legacy daemon behaviour, useful for shadow-only smoke runs).
    strategy_runtime = _maybe_build_strategy_runtime(args)

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[RawFillEvent] = asyncio.Queue()

    # Shared dedup set between WS listener and reconciler
    seen_tids: set[int] = set()

    def _mark_seen(ev: RawFillEvent) -> None:
        seen_tids.add(ev.hl_trade_id)

    listener = HLFillsListener(addresses, event_queue, loop, on_dispatch=_mark_seen)
    await listener.start()

    if listener._info is None:
        logger.error("WS listener failed to connect; aborting")
        return 2

    # R72: cold-start warmup — seed prev_position from HL clearinghouseState
    # for every whitelist wallet. Without this, every first-observed
    # Close/Reverse fill is rejected as cold_start_drift (user-reported
    # 2026-04-26: 24h showed 66/66 cold_start_drift skips).
    if not getattr(args, "skip_warmup", False):
        try:
            from smart_money.scanner.hl_client import HLClient
            hl_client = HLClient(listener._info)
            warmup = _warmup_wallet_positions(
                store, hl_client, whitelist,
                timeout_sec=args.warmup_timeout_sec,
            )
            logger.info(
                "R72 warmup: %d positions seeded across %d wallets "
                "(errors: %d)",
                warmup["positions_seeded"],
                warmup["wallets_seeded"],
                warmup["fetch_errors"],
            )
        except Exception as e:
            logger.warning(
                "R72 warmup failed (non-fatal — daemon continues with "
                "cold-start state): %s", e,
            )
    else:
        logger.info("R72 warmup skipped (--skip-warmup)")

    reconciler = FillsReconciler(
        addresses, listener._info, event_queue,
        interval_sec=args.reconciler_interval,
        lookback_sec=args.reconciler_lookback,
        seen_tids=seen_tids,
    )
    reconciler_task = asyncio.create_task(reconciler.run())

    stop = asyncio.Event()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, _on_signal)

    drain_task = asyncio.create_task(_drain_loop(
        event_queue, store, address_to_wallet,
        aggregator, simulator, history_writer, strategy_runtime, stop,
    ))
    flush_task = asyncio.create_task(_aggregator_flush_loop(aggregator, stop))

    strategy_eval_task: asyncio.Task | None = None
    if strategy_runtime is not None:
        strategy_eval_task = asyncio.create_task(_strategy_eval_loop(
            strategy_runtime, stop, interval_sec=args.strategy_eval_interval,
        ))

    try:
        await stop.wait()
    finally:
        logger.info("stopping daemon...")
        reconciler_task.cancel()
        drain_task.cancel()
        flush_task.cancel()
        if strategy_eval_task is not None:
            strategy_eval_task.cancel()
        await listener.stop()
        cancellable = [reconciler_task, drain_task, flush_task]
        if strategy_eval_task is not None:
            cancellable.append(strategy_eval_task)
        for t in cancellable:
            try:
                await t
            except asyncio.CancelledError:
                pass

    logger.info("shadow daemon stopped cleanly")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run_shadow_daemon(args))


if __name__ == "__main__":
    sys.exit(main())
