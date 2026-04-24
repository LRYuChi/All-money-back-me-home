"""儲存層抽象 + 兩個實作 (InMemory for tests / Supabase for prod).

所有操作以 Protocol 定義,注入到 scanner / ranking 中.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from smart_money.store.schema import (
    Ranking,
    SkippedSignal,
    Trade,
    Wallet,
    WalletPosition,
)

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

    # -- positions (P4b) ----------------------------------------------
    def get_position(self, wallet_id: UUID, symbol: str) -> WalletPosition | None: ...
    def upsert_position(self, position: WalletPosition) -> None: ...
    def list_positions(self, wallet_id: UUID) -> list[WalletPosition]: ...

    # -- skipped signals (P4b) ----------------------------------------
    def record_skipped_signal(self, skipped: SkippedSignal) -> None: ...

    # -- ranking reads (P4b whitelist) --------------------------------
    def latest_ranking_snapshot_date(self) -> datetime | None: ...
    def list_rankings(
        self,
        snapshot_date: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> list[Ranking]: ...


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
        # P4b: per-(wallet, symbol) position state + skipped signals audit log
        self._positions: dict[tuple[UUID, str], WalletPosition] = {}
        self._skipped: list[SkippedSignal] = []

    # -- wallets -----------------------------------------------------------
    def upsert_wallet(self, address: str, *, seen_at: datetime) -> Wallet:
        # EVM addresses are case-insensitive; normalize for consistent lookups.
        addr_lc = address.lower()
        seen_at = seen_at.astimezone(timezone.utc)
        existing_id = self._wallets_by_addr.get(addr_lc)
        if existing_id:
            w = self._wallets[existing_id]
            w.last_active_at = max(w.last_active_at, seen_at)
            return w
        w = Wallet(address=addr_lc, first_seen_at=seen_at, last_active_at=seen_at, id=uuid4())
        self._wallets[w.id] = w
        self._wallets_by_addr[addr_lc] = w.id
        return w

    def get_wallet_by_address(self, address: str) -> Wallet | None:
        wid = self._wallets_by_addr.get(address.lower())
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

    def list_rankings(
        self,
        snapshot_date: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> list[Ranking]:
        if snapshot_date is None:
            rows = sorted(self._rankings, key=lambda r: (r.snapshot_date, r.rank))
        else:
            rows = sorted(
                [r for r in self._rankings if r.snapshot_date == snapshot_date],
                key=lambda r: r.rank,
            )
        return rows[:limit] if limit is not None else rows

    def latest_ranking_snapshot_date(self) -> datetime | None:
        if not self._rankings:
            return None
        return max(r.snapshot_date for r in self._rankings)

    # -- positions (P4b) ----------------------------------------------
    def get_position(self, wallet_id: UUID, symbol: str) -> WalletPosition | None:
        return self._positions.get((wallet_id, symbol))

    def upsert_position(self, position: WalletPosition) -> None:
        self._positions[(position.wallet_id, position.symbol)] = position

    def list_positions(self, wallet_id: UUID) -> list[WalletPosition]:
        return [p for (wid, _), p in self._positions.items() if wid == wallet_id]

    # -- skipped signals (P4b) ----------------------------------------
    def record_skipped_signal(self, skipped: SkippedSignal) -> None:
        self._skipped.append(skipped)


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

    # -- P4b: position state ------------------------------------------
    def get_position(self, wallet_id: UUID, symbol: str) -> WalletPosition | None:
        res = (
            self._client.table("sm_wallet_positions")
            .select("*")
            .eq("wallet_id", str(wallet_id))
            .eq("symbol", symbol)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        return _row_to_position(res.data[0])

    def upsert_position(self, position: WalletPosition) -> None:
        self._client.table("sm_wallet_positions").upsert(
            position.to_row(), on_conflict="wallet_id,symbol"
        ).execute()

    def list_positions(self, wallet_id: UUID) -> list[WalletPosition]:
        res = (
            self._client.table("sm_wallet_positions")
            .select("*")
            .eq("wallet_id", str(wallet_id))
            .execute()
        )
        return [_row_to_position(r) for r in (res.data or [])]

    # -- P4b: skipped signals audit -----------------------------------
    def record_skipped_signal(self, skipped: SkippedSignal) -> None:
        self._client.table("sm_skipped_signals").insert(skipped.to_row()).execute()

    # -- P4b: ranking reads -------------------------------------------
    def latest_ranking_snapshot_date(self) -> datetime | None:
        res = (
            self._client.table("sm_rankings")
            .select("snapshot_date")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        d = res.data[0]["snapshot_date"]
        return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)

    def list_rankings(
        self,
        snapshot_date: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> list[Ranking]:
        q = self._client.table("sm_rankings").select("*")
        if snapshot_date is not None:
            q = q.eq("snapshot_date", snapshot_date.date().isoformat())
        q = q.order("snapshot_date", desc=True).order("rank")
        if limit is not None:
            q = q.limit(limit)
        res = q.execute()
        return [_row_to_ranking(r) for r in (res.data or [])]


def _row_to_position(row: dict[str, Any]) -> WalletPosition:
    avg = row.get("avg_entry_px")
    return WalletPosition(
        wallet_id=UUID(row["wallet_id"]),
        symbol=row["symbol"],
        side=row["side"],
        size=float(row["size"]),
        avg_entry_px=(float(avg) if avg is not None else None),
        last_updated_ts=datetime.fromisoformat(row["last_updated_ts"].replace("Z", "+00:00")),
    )


def _row_to_ranking(row: dict[str, Any]) -> Ranking:
    return Ranking(
        snapshot_date=datetime.fromisoformat(row["snapshot_date"]).replace(tzinfo=timezone.utc),
        wallet_id=UUID(row["wallet_id"]),
        rank=int(row["rank"]),
        score=float(row["score"]),
        metrics=dict(row.get("metrics") or {}),
        ai_analysis=(dict(row["ai_analysis"]) if row.get("ai_analysis") else None),
    )


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


# ================================================================== #
# PostgresStore — 直連 Supabase Postgres(效能最好,推薦)
# ================================================================== #
class PostgresStore:
    """TradeStore backed by a direct postgres connection (psycopg v3).

    與 SupabaseStore 的差異:
      * bulk upsert 使用 `executemany` + `ON CONFLICT DO NOTHING`,快 5-10x
      * 一律透過 pgbouncer,建議 URL 用 transaction-mode port 6543
      * 不需要 supabase-py 依賴
    """

    BATCH_SIZE = 1000

    def __init__(self, dsn: str):
        # lazy import 避免 import-time 依賴
        import psycopg       # type: ignore

        self._dsn = dsn
        self._psycopg = psycopg
        # transaction-mode pooler 需關掉 prepared statement cache
        self._connect_kwargs = {"prepare_threshold": None}

    def _conn(self):
        """Context manager: each op opens a fresh connection (pgbouncer-friendly)."""
        return self._psycopg.connect(self._dsn, **self._connect_kwargs)

    # -- wallets -----------------------------------------------------------
    def upsert_wallet(self, address: str, *, seen_at: datetime) -> Wallet:
        seen_utc = seen_at.astimezone(timezone.utc)
        with self._conn() as conn, conn.cursor() as cur:
            # 先查是否存在
            cur.execute(
                "select id, address, first_seen_at, last_active_at, tags, notes "
                "from sm_wallets where address = %s",
                (address,),
            )
            row = cur.fetchone()
            if row:
                new_last = max(row[3], seen_utc)
                if new_last > row[3]:
                    cur.execute(
                        "update sm_wallets set last_active_at = %s where id = %s",
                        (new_last, row[0]),
                    )
                    conn.commit()
                return Wallet(
                    id=row[0], address=row[1],
                    first_seen_at=row[2], last_active_at=new_last,
                    tags=list(row[4] or []), notes=row[5],
                )
            # insert
            cur.execute(
                "insert into sm_wallets (address, first_seen_at, last_active_at, tags) "
                "values (%s, %s, %s, %s) returning id",
                (address, seen_utc, seen_utc, []),
            )
            wid = cur.fetchone()[0]
            conn.commit()
            return Wallet(
                id=wid, address=address,
                first_seen_at=seen_utc, last_active_at=seen_utc,
                tags=[], notes=None,
            )

    def get_wallet_by_address(self, address: str) -> Wallet | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select id, address, first_seen_at, last_active_at, tags, notes "
                "from sm_wallets where address = %s",
                (address,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Wallet(
                id=row[0], address=row[1],
                first_seen_at=row[2], last_active_at=row[3],
                tags=list(row[4] or []), notes=row[5],
            )

    def list_wallets(self, tag: str | None = None) -> list[Wallet]:
        sql = ("select id, address, first_seen_at, last_active_at, tags, notes "
               "from sm_wallets ")
        params: tuple = ()
        if tag:
            sql += "where %s = any(tags) "
            params = (tag,)
        sql += "order by last_active_at desc"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                Wallet(
                    id=r[0], address=r[1],
                    first_seen_at=r[2], last_active_at=r[3],
                    tags=list(r[4] or []), notes=r[5],
                )
                for r in cur.fetchall()
            ]

    def add_tag(self, wallet_id: UUID, tag: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("select tags from sm_wallets where id = %s", (str(wallet_id),))
            row = cur.fetchone()
            if row is None:
                raise KeyError(wallet_id)
            tags = list(row[0] or [])
            if tag in tags:
                return
            tags.append(tag)
            cur.execute(
                "update sm_wallets set tags = %s where id = %s",
                (tags, str(wallet_id)),
            )
            conn.commit()

    # -- trades ------------------------------------------------------------
    def upsert_trades(self, trades: Iterable[Trade]) -> int:
        import json as _json   # local import
        trades_list = list(trades)
        if not trades_list:
            return 0

        # INSERT ... ON CONFLICT DO NOTHING — 保持 idempotent
        sql = (
            "insert into sm_wallet_trades "
            "(wallet_id, hl_trade_id, symbol, side, action, size, price, pnl, fee, ts, raw) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (wallet_id, hl_trade_id) do nothing"
        )
        inserted = 0
        with self._conn() as conn, conn.cursor() as cur:
            for i in range(0, len(trades_list), self.BATCH_SIZE):
                batch = trades_list[i:i + self.BATCH_SIZE]
                params = [
                    (
                        str(t.wallet_id), t.hl_trade_id, t.symbol, t.side, t.action,
                        t.size, t.price, t.pnl, t.fee,
                        t.ts.astimezone(timezone.utc),
                        _json.dumps(t.raw) if t.raw is not None else None,
                    )
                    for t in batch
                ]
                cur.executemany(sql, params)
                # executemany 不回 rowcount 準確值;以 batch 長度為估
                inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(batch)
            conn.commit()
        return inserted

    def get_trades(
        self,
        wallet_id: UUID,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Trade]:
        sql = ("select wallet_id, hl_trade_id, symbol, side, action, size, price, pnl, fee, ts, raw "
               "from sm_wallet_trades where wallet_id = %s")
        params: list = [str(wallet_id)]
        if since:
            sql += " and ts >= %s"
            params.append(since.astimezone(timezone.utc))
        if until:
            sql += " and ts < %s"
            params.append(until.astimezone(timezone.utc))
        sql += " order by ts"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                Trade(
                    wallet_id=r[0], hl_trade_id=r[1], symbol=r[2],
                    side=r[3], action=r[4],
                    size=float(r[5]), price=float(r[6]),
                    pnl=(float(r[7]) if r[7] is not None else None),
                    fee=float(r[8] or 0), ts=r[9],
                    raw=r[10],
                )
                for r in cur.fetchall()
            ]

    def get_last_trade_ts(self, wallet_id: UUID) -> datetime | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select ts from sm_wallet_trades where wallet_id = %s "
                "order by ts desc limit 1",
                (str(wallet_id),),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def count_trades(self, wallet_id: UUID) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select count(*) from sm_wallet_trades where wallet_id = %s",
                (str(wallet_id),),
            )
            return cur.fetchone()[0]

    def save_ranking(self, rankings: list[Ranking]) -> int:
        if not rankings:
            return 0
        import json as _json
        sql = (
            "insert into sm_rankings (snapshot_date, wallet_id, rank, score, metrics, ai_analysis) "
            "values (%s, %s, %s, %s, %s, %s) "
            "on conflict (snapshot_date, wallet_id) do update set "
            "rank = excluded.rank, score = excluded.score, metrics = excluded.metrics, "
            "ai_analysis = excluded.ai_analysis"
        )
        params = [
            (
                r.snapshot_date.date(), str(r.wallet_id), r.rank, r.score,
                _json.dumps(r.metrics),
                _json.dumps(r.ai_analysis) if r.ai_analysis is not None else None,
            )
            for r in rankings
        ]
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(sql, params)
            conn.commit()
        return len(rankings)

    # -- P4b: position state ------------------------------------------
    def get_position(self, wallet_id: UUID, symbol: str) -> WalletPosition | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select wallet_id, symbol, side, size, avg_entry_px, last_updated_ts "
                "from sm_wallet_positions where wallet_id = %s and symbol = %s",
                (str(wallet_id), symbol),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return WalletPosition(
            wallet_id=UUID(str(row[0])),
            symbol=row[1],
            side=row[2],
            size=float(row[3]),
            avg_entry_px=(float(row[4]) if row[4] is not None else None),
            last_updated_ts=row[5],
        )

    def upsert_position(self, position: WalletPosition) -> None:
        sql = (
            "insert into sm_wallet_positions "
            "(wallet_id, symbol, side, size, avg_entry_px, last_updated_ts, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, now()) "
            "on conflict (wallet_id, symbol) do update set "
            "side = excluded.side, size = excluded.size, "
            "avg_entry_px = excluded.avg_entry_px, "
            "last_updated_ts = excluded.last_updated_ts, "
            "updated_at = now()"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                str(position.wallet_id), position.symbol, position.side,
                position.size, position.avg_entry_px,
                position.last_updated_ts.astimezone(timezone.utc),
            ))
            conn.commit()

    def list_positions(self, wallet_id: UUID) -> list[WalletPosition]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select wallet_id, symbol, side, size, avg_entry_px, last_updated_ts "
                "from sm_wallet_positions where wallet_id = %s",
                (str(wallet_id),),
            )
            rows = cur.fetchall()
        return [
            WalletPosition(
                wallet_id=UUID(str(r[0])),
                symbol=r[1], side=r[2], size=float(r[3]),
                avg_entry_px=(float(r[4]) if r[4] is not None else None),
                last_updated_ts=r[5],
            )
            for r in rows
        ]

    # -- P4b: skipped signals audit -----------------------------------
    def record_skipped_signal(self, skipped: SkippedSignal) -> None:
        import json as _json
        sql = (
            "insert into sm_skipped_signals "
            "(wallet_id, wallet_address, symbol_hl, reason, signal_latency_ms, "
            " direction_raw, hl_trade_id, detail, created_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                str(skipped.wallet_id) if skipped.wallet_id else None,
                skipped.wallet_address, skipped.symbol_hl, skipped.reason,
                skipped.signal_latency_ms, skipped.direction_raw, skipped.hl_trade_id,
                _json.dumps(skipped.detail) if skipped.detail is not None else None,
                skipped.created_at.astimezone(timezone.utc),
            ))
            conn.commit()

    # -- P4b: ranking reads -------------------------------------------
    def latest_ranking_snapshot_date(self) -> datetime | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("select max(snapshot_date) from sm_rankings")
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return datetime(row[0].year, row[0].month, row[0].day, tzinfo=timezone.utc)

    def list_rankings(
        self,
        snapshot_date: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> list[Ranking]:
        sql = (
            "select snapshot_date, wallet_id, rank, score, metrics, ai_analysis "
            "from sm_rankings"
        )
        params: list[Any] = []
        if snapshot_date is not None:
            sql += " where snapshot_date = %s"
            params.append(snapshot_date.date())
        sql += " order by snapshot_date desc, rank asc"
        if limit is not None:
            sql += " limit %s"
            params.append(limit)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = r[0]
            out.append(Ranking(
                snapshot_date=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                wallet_id=UUID(str(r[1])),
                rank=int(r[2]),
                score=float(r[3]),
                metrics=dict(r[4] or {}),
                ai_analysis=(dict(r[5]) if r[5] else None),
            ))
        return out


# ------------------------------------------------------------------ #
# Factory
# ------------------------------------------------------------------ #
def build_store(settings) -> TradeStore:  # noqa: ANN001  (circular import avoid)
    """根據 settings 建立合適的 store.

    優先順序:
      1. DATABASE_URL → PostgresStore(最快)
      2. SUPABASE_URL + SUPABASE_SERVICE_KEY → SupabaseStore(REST)
      3. 都無 → InMemoryStore(dev only)
    """
    if settings.database_url:
        logger.info("Using PostgresStore (direct DSN)")
        return PostgresStore(settings.database_url)

    if settings.supabase_url and settings.supabase_service_key:
        try:
            from supabase import create_client  # type: ignore
        except ImportError as exc:
            raise RuntimeError("supabase package not installed") from exc
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        logger.info("Using SupabaseStore (url=%s)", settings.supabase_url[:40])
        return SupabaseStore(client)

    logger.warning("No DB creds → falling back to InMemoryStore (dev only)")
    return InMemoryStore()


__all__ = [
    "InMemoryStore",
    "PostgresStore",
    "SupabaseStore",
    "TradeStore",
    "build_store",
]
