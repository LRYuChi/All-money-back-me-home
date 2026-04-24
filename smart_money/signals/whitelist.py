"""Dynamic whitelist supplier for the shadow / live daemons (P4b).

Priority order (later rules override earlier):
    1. Latest ranking snapshot, top N (config.ranking.whitelist_size)
    2. Freshness filter: drop any wallet with no HL fill in the last N days
       (demoted to 'watch-only' — kept in the state machine but not tradeable).
    3. Manual override YAML (config/smart_money/whitelist_manual.yaml),
       gitignored so each host can tweak locally.

The YAML format:
    include:
      - "0xabc..."    # force-include even if not in top N
    exclude:
      - "0xdef..."    # force-exclude even if in top N

Returns a list of `WhitelistEntry` — each one has an `is_tradeable` flag
so downstream can distinguish "track but don't follow" (freshness demoted,
warmup window, ranking-dropped-mid-week) from "actively follow".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from smart_money.store.db import TradeStore
from smart_money.store.schema import Ranking, Wallet

logger = logging.getLogger(__name__)


WhitelistSource = Literal[
    "ranking",           # came from latest sm_rankings top N
    "manual_include",    # forced in via whitelist_manual.yaml
]

DemotionReason = Literal[
    "none",              # fully tradeable
    "manual_exclude",
    "stale_no_fills",
    "warmup",
]


@dataclass(slots=True, frozen=True)
class WhitelistEntry:
    """One wallet on the active whitelist with its tradeable flag + provenance."""

    wallet_id: UUID
    address: str
    score: float
    rank: int | None                 # rank from latest snapshot (None if manual-only)
    source: WhitelistSource
    is_tradeable: bool
    demotion_reason: DemotionReason  # 'none' when is_tradeable=True


@dataclass(slots=True, frozen=True)
class WhitelistOverride:
    include: set[str]                # lowercased addresses to force-include
    exclude: set[str]                # lowercased addresses to force-exclude


def load_manual_override(path: Path | None) -> WhitelistOverride:
    """Load include/exclude lists from YAML. Missing file or key → empty set.

    Silent on missing file because the file is *expected* to be absent on
    fresh installs; log at DEBUG only.
    """
    if path is None or not path.exists():
        logger.debug("whitelist manual override not found: %s", path)
        return WhitelistOverride(include=set(), exclude=set())
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        logger.warning("whitelist override ignored: yaml not installed: %s", e)
        return WhitelistOverride(include=set(), exclude=set())

    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("whitelist override %s parse failed: %s — ignoring", path, e)
        return WhitelistOverride(include=set(), exclude=set())

    include = {a.lower() for a in (data.get("include") or []) if isinstance(a, str)}
    exclude = {a.lower() for a in (data.get("exclude") or []) if isinstance(a, str)}
    return WhitelistOverride(include=include, exclude=exclude)


def build_whitelist(
    store: TradeStore,
    *,
    as_of: datetime | None = None,
    whitelist_size: int = 10,
    freshness_days: int = 14,
    override: WhitelistOverride | None = None,
    warmup_cutoff: datetime | None = None,
) -> list[WhitelistEntry]:
    """Compose the active whitelist from ranking + override + freshness.

    Args:
        store: TradeStore implementation (reads sm_rankings and sm_wallets).
        as_of: "now" for freshness checks. Defaults to UTC now.
        whitelist_size: how many top-ranked wallets to consider.
        freshness_days: if a wallet hasn't had a fill in this window it's
            marked not-tradeable (but still tracked for future recovery).
        override: manual include/exclude. None = no manual override.
        warmup_cutoff: any wallet whose `first_seen_at` > this cutoff is
            marked warmup (not-tradeable). None = no warmup gating.

    Returns:
        List of WhitelistEntry, deduplicated by wallet_id, sorted by rank
        (with manual-include at the end ordered by address for stability).
    """
    as_of = as_of or datetime.now(timezone.utc)
    override = override or WhitelistOverride(include=set(), exclude=set())

    # ---- 1. Ranking-sourced candidates ------------------------------
    latest_date = store.latest_ranking_snapshot_date()
    ranking_entries: list[tuple[Ranking, Wallet]] = []
    if latest_date is not None:
        top = store.list_rankings(snapshot_date=latest_date, limit=whitelist_size)
        for r in top:
            w = _find_wallet(store, r.wallet_id)
            if w is not None:
                ranking_entries.append((r, w))
    else:
        logger.info("build_whitelist: no ranking snapshots yet; manual-include only")

    # ---- 2. Manual include additions (may duplicate ranking) --------
    # Dedup: if a manual-included wallet is already in ranking top, keep
    # the ranking entry (has rank + score).
    ranking_ids: set[UUID] = {r.wallet_id for r, _ in ranking_entries}
    manual_wallets: list[Wallet] = []
    for addr in override.include:
        w = store.get_wallet_by_address(addr)
        if w is None:
            logger.warning("manual include %s not in sm_wallets — skip", addr[:10])
            continue
        if w.id in ranking_ids:
            continue
        manual_wallets.append(w)

    # ---- 3. Apply freshness + warmup + manual exclude ---------------
    entries: list[WhitelistEntry] = []
    fresh_cutoff = as_of - timedelta(days=freshness_days)

    for ranking, wallet in ranking_entries:
        entries.append(_compose_entry(
            wallet=wallet,
            score=ranking.score,
            rank=ranking.rank,
            source="ranking",
            override=override,
            fresh_cutoff=fresh_cutoff,
            warmup_cutoff=warmup_cutoff,
        ))

    for wallet in manual_wallets:
        entries.append(_compose_entry(
            wallet=wallet,
            score=0.0,
            rank=None,
            source="manual_include",
            override=override,
            fresh_cutoff=fresh_cutoff,
            warmup_cutoff=warmup_cutoff,
        ))

    return entries


def _compose_entry(
    *,
    wallet: Wallet,
    score: float,
    rank: int | None,
    source: WhitelistSource,
    override: WhitelistOverride,
    fresh_cutoff: datetime,
    warmup_cutoff: datetime | None,
) -> WhitelistEntry:
    addr_lower = wallet.address.lower()

    # Manual exclude is the strongest signal — overrides rank & freshness.
    if addr_lower in override.exclude:
        return WhitelistEntry(
            wallet_id=wallet.id, address=wallet.address, score=score,
            rank=rank, source=source, is_tradeable=False,
            demotion_reason="manual_exclude",
        )

    # Warmup: new wallet seen after the warmup cutoff — track but don't trade.
    if warmup_cutoff is not None and wallet.first_seen_at > warmup_cutoff:
        return WhitelistEntry(
            wallet_id=wallet.id, address=wallet.address, score=score,
            rank=rank, source=source, is_tradeable=False,
            demotion_reason="warmup",
        )

    # Stale: no recent fills → track but don't trade.
    if wallet.last_active_at < fresh_cutoff:
        return WhitelistEntry(
            wallet_id=wallet.id, address=wallet.address, score=score,
            rank=rank, source=source, is_tradeable=False,
            demotion_reason="stale_no_fills",
        )

    return WhitelistEntry(
        wallet_id=wallet.id, address=wallet.address, score=score,
        rank=rank, source=source, is_tradeable=True,
        demotion_reason="none",
    )


def _find_wallet(store: TradeStore, wallet_id: UUID) -> Wallet | None:
    """TradeStore has no direct get_by_id (only by address). Scan list_wallets
    once; whitelist is small so no perf concern."""
    for w in store.list_wallets():
        if w.id == wallet_id:
            return w
    return None


__all__ = [
    "WhitelistEntry",
    "WhitelistOverride",
    "WhitelistSource",
    "DemotionReason",
    "build_whitelist",
    "load_manual_override",
]
