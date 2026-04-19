"""儲存層抽象 + 兩個實作 (InMemory for tests / Supabase for prod).

所有操作以 Protocol 定義,注入到 scanner / ranking 中.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from smart_money.store.schema import Ranking, Trade, Wallet

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Store Protocol
# ------------------------------------------------------------------ #
class TradeStore(Protocol):
    """儲存層介面;任何實作都須維持 idempotent upsert 語意."""

    def upsert_wallet(self, address: str, *, seen_at: datetime) -> Wallet: ...
    def get_wallet_by_address(self, address: str) -> Wallet | None: ...
    def list_wallets(self, tag: str | None = None) -> list[Wallet]: ...
    def add_tag(self, wallet_id: UUID, tag: str) -> None: ...

    def upsert_trades(self, trades: Iterable[Trade]) -> int: ...
    def get_trades(
        self,
        wallet_id: UUID,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Trade]: ...
    def get_last_trade_ts(self, wallet_id: UUID) -> datetime | None: ...
    def count_trades(self, wallet_id: UUID) -> int: ...

    def save_ranking(self, rankings: list[Ranking]) -> int: ...


# ------------------------------------------------------------------ #
# InMemoryStore — 測試 & local runs
# ------------------------------------------------------------------ #
class InMemoryStore:
    """不落地的 store,拿來做單元測試與 smoke test."""

    def __init__(self) -> None:
        self._wallets: dict[UUID, Wallet] = {}
        self._wallets_by_addr: dict[str, UUID] = {}
        # (wallet_id, hl_trade_id) -> Trade (for idempotent upsert)
        self._trades: dict[tuple[UUID, str], Trade] = {}
        self._rankings: list[Ranking] = []

    # -- wallets -----------------------------------------------------------
    def upsert_wallet(self, address: str, *, seen_at: datetime) -> Wallet:
        seen_at = seen_at.astimezone(timezone.utc)
        existing_id = self._wallets_by_addr.get(address)
        if existing_id:
            w = self._wallets[existing_id]
            w.last_active_at = max(w.last_active_at, seen_at)
            return w
        w = Wallet(address=address, first_seen_at=seen_at, last_active_at=seen_at, id=uuid4())
        self._wallets[w.id] = w
        self._wallets_by_addr[address] = w.id
        return w

    def get_wallet_by_address(self, address: str) -> Wallet | None:
        wid = self._wallets_by_addr.get(address)
        return self._wallets[wid] if wid else None

    def list_wallets(self, tag: str | None = None) -> list[Wallet]:
        wallets = list(self._wallets.values())
        if tag:
            wallets = [w for w in wallets if tag in w.tags]
        return sorted(wallets, key=lambda w: w.last_active_at, reverse=True)

    def add_tag(self, wallet_id: UUID, tag: str) -> None:
        w = self._wallets.get(wallet_id)
        if w is None:
            raise KeyError(wallet_id)
        if tag not in w.tags:
            w.tags.append(tag)

    # -- trades ------------------------------------------------------------
    def upsert_trades(self, trades: Iterable[Trade]) -> int:
        n = 0
        for t in trades:
            key = (t.wallet_id, t.hl_trade_id)
            if key in self._trades:
                continue  # idempotent:already present
            self._trades[key] = t
            n += 1
            # touch wallet last_active
            w = self._wallets.get(t.wallet_id)
            if w and t.ts > w.last_active_at:
                w.last_active_at = t.ts
        return n

    def get_trades(
        self,
        wallet_id: UUID,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Trade]:
        out = [t for (wid, _), t in self._trades.items() if wid == wallet_id]
        if since:
            out = [t for t in out if t.ts >= since]
        if until:
            out = [t for t in out if t.ts < until]
        return sorted(out, key=lambda t: t.ts)

    def get_last_trade_ts(self, wallet_id: UUID) -> datetime | None:
        trades = self.get_trades(wallet_id)
        return trades[-1].ts if trades else None

    def count_trades(self, wallet_id: UUID) -> int:
        return sum(1 for (wid, _) in self._trades if wid == wallet_id)

    # -- rankings ----------------------------------------------------------
    def save_ranking(self, rankings: list[Ranking]) -> int:
        self._rankings.extend(rankings)
        return len(rankings)

    def list_rankings(self, snapshot_date: datetime | None = None) -> list[Ranking]:
        if snapshot_date is None:
            return sorted(self._rankings, key=lambda r: (r.snapshot_date, r.rank))
        return sorted(
            [r for r in self._rankings if r.snapshot_date == snapshot_date],
            key=lambda r: r.rank,
        )


# ------------------------------------------------------------------ #
# SupabaseStore — prod 實作
# ------------------------------------------------------------------ #
class SupabaseStore:
    """使用 supabase-py 的 TradeStore 實作.

    Note: 依賴 `supabase` client,service_role key 權限.
    批次 upsert 走 table.upsert(on_conflict=...) with batching.
    """

    BATCH_SIZE = 500

    def __init__(self, client: Any):
        """client: supabase.Client instance."""
        self._client = client

    def upsert_wallet(self, address: str, *, seen_at: datetime) -> Wallet:
        seen_utc = seen_at.astimezone(timezone.utc).isoformat()
        # 先查,避免每次都 upsert 造成 updated_at 雜訊
        res = (
            self._client.table("sm_wallets")
            .select("*")
            .eq("address", address)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            # 更新 last_active_at (只增不減)
            existing_last = datetime.fromisoformat(row["last_active_at"].replace("Z", "+00:00"))
            new_last = max(existing_last, seen_at.astimezone(timezone.utc))
            if new_last > existing_last:
                self._client.table("sm_wallets").update(
                    {"last_active_at": new_last.isoformat()}
                ).eq("id", row["id"]).execute()
                row["last_active_at"] = new_last.isoformat()
            return _row_to_wallet(row)

        # insert
        new_row = {
            "address": address,
            "first_seen_at": seen_utc,
            "last_active_at": seen_utc,
            "tags": [],
        }
        ins = self._client.table("sm_wallets").insert(new_row).execute()
        return _row_to_wallet(ins.data[0])

    def get_wallet_by_address(self, address: str) -> Wallet | None:
        res = (
            self._client.table("sm_wallets")
            .select("*")
            .eq("address", address)
            .limit(1)
            .execute()
        )
        return _row_to_wallet(res.data[0]) if res.data else None

    def list_wallets(self, tag: str | None = None) -> list[Wallet]:
        q = self._client.table("sm_wallets").select("*").order("last_active_at", desc=True)
        if tag:
            q = q.contains("tags", [tag])
        res = q.execute()
        return [_row_to_wallet(r) for r in res.data or []]

    def add_tag(self, wallet_id: UUID, tag: str) -> None:
        res = self._client.table("sm_wallets").select("tags").eq("id", str(wallet_id)).execute()
        if not res.data:
            raise KeyError(wallet_id)
        tags = list(res.data[0].get("tags") or [])
        if tag not in tags:
            tags.append(tag)
            self._client.table("sm_wallets").update({"tags": tags}).eq("id", str(wallet_id)).execute()

    def upsert_trades(self, trades: Iterable[Trade]) -> int:
        batch: list[dict[str, Any]] = []
        total = 0
        for t in trades:
            batch.append(t.to_row())
            if len(batch) >= self.BATCH_SIZE:
                total += self._flush_trades(batch)
                batch = []
        if batch:
            total += self._flush_trades(batch)
        return total

    def _flush_trades(self, rows: list[dict[str, Any]]) -> int:
        res = (
            self._client.table("sm_wallet_trades")
            .upsert(rows, on_conflict="wallet_id,hl_trade_id")
            .execute()
        )
        return len(res.data or [])

    def get_trades(
        self,
        wallet_id: UUID,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Trade]:
        q = self._client.table("sm_wallet_trades").select("*").eq("wallet_id", str(wallet_id)).order("ts")
        if since:
            q = q.gte("ts", since.astimezone(timezone.utc).isoformat())
        if until:
            q = q.lt("ts", until.astimezone(timezone.utc).isoformat())
        res = q.execute()
        return [_row_to_trade(r) for r in res.data or []]

    def get_last_trade_ts(self, wallet_id: UUID) -> datetime | None:
        res = (
            self._client.table("sm_wallet_trades")
            .select("ts")
            .eq("wallet_id", str(wallet_id))
            .order("ts", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        return datetime.fromisoformat(res.data[0]["ts"].replace("Z", "+00:00"))

    def count_trades(self, wallet_id: UUID) -> int:
        res = (
            self._client.table("sm_wallet_trades")
            .select("id", count="exact")
            .eq("wallet_id", str(wallet_id))
            .execute()
        )
        return res.count or 0

    def save_ranking(self, rankings: list[Ranking]) -> int:
        if not rankings:
            return 0
        rows = [
            {
                "snapshot_date": r.snapshot_date.date().isoformat(),
                "wallet_id": str(r.wallet_id),
                "rank": r.rank,
                "score": r.score,
                "metrics": r.metrics,
                "ai_analysis": r.ai_analysis,
            }
            for r in rankings
        ]
        res = (
            self._client.table("sm_rankings")
            .upsert(rows, on_conflict="snapshot_date,wallet_id")
            .execute()
        )
        return len(res.data or [])


def _row_to_wallet(row: dict[str, Any]) -> Wallet:
    return Wallet(
        id=UUID(row["id"]),
        address=row["address"],
        first_seen_at=datetime.fromisoformat(row["first_seen_at"].replace("Z", "+00:00")),
        last_active_at=datetime.fromisoformat(row["last_active_at"].replace("Z", "+00:00")),
        tags=list(row.get("tags") or []),
        notes=row.get("notes"),
    )


def _row_to_trade(row: dict[str, Any]) -> Trade:
    return Trade(
        wallet_id=UUID(row["wallet_id"]),
        hl_trade_id=row["hl_trade_id"],
        symbol=row["symbol"],
        side=row["side"],
        action=row["action"],
        size=float(row["size"]),
        price=float(row["price"]),
        pnl=(float(row["pnl"]) if row.get("pnl") is not None else None),
        fee=float(row.get("fee", 0) or 0),
        ts=datetime.fromisoformat(row["ts"].replace("Z", "+00:00")),
        raw=row.get("raw"),
    )


# ------------------------------------------------------------------ #
# Factory
# ------------------------------------------------------------------ #
def build_store(settings) -> TradeStore:  # noqa: ANN001  (circular import avoid)
    """根據 settings 建立合適的 store.

    若 supabase_url/key 都有 → SupabaseStore;否則 InMemoryStore (dev/test).
    """
    if settings.supabase_url and settings.supabase_service_key:
        try:
            from supabase import create_client  # type: ignore
        except ImportError as exc:
            raise RuntimeError("supabase package not installed") from exc
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        logger.info("Using SupabaseStore (url=%s)", settings.supabase_url[:40])
        return SupabaseStore(client)
    logger.warning("Supabase creds absent → falling back to InMemoryStore (dev only)")
    return InMemoryStore()


__all__ = [
    "InMemoryStore",
    "SupabaseStore",
    "TradeStore",
    "build_store",
]
