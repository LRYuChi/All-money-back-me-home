"""Strategy registry — store/load YAML strategies in DB.

Storage shape:
  - strategies table: id (PK) + yaml_text + enabled + mode + updated_at
  - load_active() returns parsed StrategyDef list ready for evaluation
  - upsert(yaml_text) parses to validate before storing — bad YAML never
    lands in DB

Round 25: `set_enabled(id, enabled, reason, actor)` flips a strategy's
enabled flag without re-uploading YAML, and writes a row to
`strategy_enable_history` for audit. G9 ConsecutiveLossDaysGuard tripping
should call this with `actor="guard:consecutive_loss_cb"`. Manual unlocks
should call with `actor="human:..."` so the trail is complete.

The DB column `strategies.enabled` is the source of truth — `parsed.enabled`
on the loaded YAML is overridden to match (frozen dataclass replace).

Same backend pattern as shared.signals.history / shared.snapshots:
NoOp + InMemory + Supabase + Postgres + factory.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from strategy_engine.dsl import load_strategy_str
from strategy_engine.types import StrategyDef

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class StrategyRecord:
    """One row in `strategies` — has YAML text + parsed def + metadata.

    `parsed.enabled` reflects the DB column (round 25), NOT the YAML's
    `enabled` field. Use `set_enabled` to flip durably.
    """

    id: str
    yaml_text: str
    parsed: StrategyDef
    updated_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class EnableEvent:
    """One row in `strategy_enable_history` — audit of a state change."""

    strategy_id: str
    enabled: bool
    reason: str | None
    actor: str | None
    created_at: datetime


class StrategyRegistry(Protocol):
    def upsert(self, yaml_text: str) -> StrategyRecord: ...
    def get(self, strategy_id: str) -> StrategyRecord: ...
    def delete(self, strategy_id: str) -> bool: ...
    def list_all(self) -> list[StrategyRecord]: ...
    def list_active(self) -> list[StrategyRecord]: ...
    def set_enabled(
        self,
        strategy_id: str,
        enabled: bool,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> StrategyRecord: ...
    def enable_history(
        self, strategy_id: str, *, limit: int = 50,
    ) -> list[EnableEvent]: ...


class StrategyNotFound(KeyError):
    """No row with that id."""


def _override_enabled(parsed: StrategyDef, enabled: bool) -> StrategyDef:
    """Return a copy of `parsed` with `enabled` set. StrategyDef is frozen,
    so we use dataclasses.replace."""
    if parsed.enabled == enabled:
        return parsed
    return dataclasses.replace(parsed, enabled=enabled)


# ================================================================== #
# Implementations
# ================================================================== #
class InMemoryStrategyRegistry:
    """For tests + smoke. Maintains insertion order for stable list_all.

    `_enabled_overrides` tracks DB-style enabled state (separate from YAML)
    so set_enabled can flip without rewriting YAML — same semantic as the
    DB-backed implementations.
    """

    def __init__(self) -> None:
        self._records: dict[str, StrategyRecord] = {}
        self._enabled_overrides: dict[str, bool] = {}
        self._history: list[EnableEvent] = []

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)  # raises DSLError on bad input
        now = datetime.now(timezone.utc)
        existing = self._records.get(parsed.id)
        # Preserve DB-side enabled flag across YAML re-uploads (so a
        # previously-disabled strategy stays disabled after a YAML update).
        if parsed.id in self._enabled_overrides:
            parsed = _override_enabled(parsed, self._enabled_overrides[parsed.id])
        rec = StrategyRecord(
            id=parsed.id, yaml_text=yaml_text, parsed=parsed,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._records[parsed.id] = rec
        return rec

    def get(self, strategy_id: str) -> StrategyRecord:
        rec = self._records.get(strategy_id)
        if rec is None:
            raise StrategyNotFound(strategy_id)
        return rec

    def delete(self, strategy_id: str) -> bool:
        self._enabled_overrides.pop(strategy_id, None)
        return self._records.pop(strategy_id, None) is not None

    def list_all(self) -> list[StrategyRecord]:
        return list(self._records.values())

    def list_active(self) -> list[StrategyRecord]:
        return [r for r in self._records.values() if r.parsed.enabled]

    def set_enabled(
        self,
        strategy_id: str,
        enabled: bool,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> StrategyRecord:
        if strategy_id not in self._records:
            raise StrategyNotFound(strategy_id)
        rec = self._records[strategy_id]
        new_parsed = _override_enabled(rec.parsed, enabled)
        new_rec = StrategyRecord(
            id=rec.id,
            yaml_text=rec.yaml_text,
            parsed=new_parsed,
            created_at=rec.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._records[strategy_id] = new_rec
        self._enabled_overrides[strategy_id] = enabled
        self._history.append(EnableEvent(
            strategy_id=strategy_id, enabled=enabled,
            reason=reason, actor=actor,
            created_at=new_rec.updated_at,
        ))
        return new_rec

    def enable_history(
        self, strategy_id: str, *, limit: int = 50,
    ) -> list[EnableEvent]:
        # Newest first
        rows = [e for e in self._history if e.strategy_id == strategy_id]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]


class SupabaseStrategyRegistry:
    TABLE = "strategies"
    HISTORY_TABLE = "strategy_enable_history"

    def __init__(self, client: Any):
        self._client = client

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)
        # Preserve DB-side enabled flag if it differs from YAML
        existing = self._fetch_existing(parsed.id)
        enabled = parsed.enabled if existing is None else existing.get("enabled", parsed.enabled)
        now = datetime.now(timezone.utc).isoformat()
        self._client.table(self.TABLE).upsert({
            "id": parsed.id,
            "yaml_text": yaml_text,
            "enabled": enabled,
            "mode": parsed.mode,
            "market": parsed.market,
            "symbol": parsed.symbol,
            "timeframe": parsed.timeframe,
            "updated_at": now,
        }, on_conflict="id").execute()
        return self.get(parsed.id)

    def get(self, strategy_id: str) -> StrategyRecord:
        res = (
            self._client.table(self.TABLE).select("*")
            .eq("id", strategy_id).limit(1).execute()
        )
        if not res.data:
            raise StrategyNotFound(strategy_id)
        return _row_to_record(res.data[0])

    def delete(self, strategy_id: str) -> bool:
        res = self._client.table(self.TABLE).delete().eq("id", strategy_id).execute()
        return bool(res.data)

    def list_all(self) -> list[StrategyRecord]:
        res = self._client.table(self.TABLE).select("*").order("id").execute()
        return [_row_to_record(r) for r in (res.data or [])]

    def list_active(self) -> list[StrategyRecord]:
        res = (
            self._client.table(self.TABLE).select("*")
            .eq("enabled", True).order("id").execute()
        )
        return [_row_to_record(r) for r in (res.data or [])]

    def set_enabled(
        self,
        strategy_id: str,
        enabled: bool,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> StrategyRecord:
        # Existence check up-front so the audit row never references a
        # missing strategy_id (FK would reject it but the caller error
        # would be confusing).
        if self._fetch_existing(strategy_id) is None:
            raise StrategyNotFound(strategy_id)
        now = datetime.now(timezone.utc).isoformat()
        self._client.table(self.TABLE).update({
            "enabled": enabled, "updated_at": now,
        }).eq("id", strategy_id).execute()
        self._client.table(self.HISTORY_TABLE).insert({
            "strategy_id": strategy_id,
            "enabled": enabled,
            "reason": reason,
            "actor": actor,
            "created_at": now,
        }).execute()
        return self.get(strategy_id)

    def enable_history(
        self, strategy_id: str, *, limit: int = 50,
    ) -> list[EnableEvent]:
        res = (
            self._client.table(self.HISTORY_TABLE).select("*")
            .eq("strategy_id", strategy_id)
            .order("created_at", desc=True).limit(limit).execute()
        )
        return [_row_to_event(r) for r in (res.data or [])]

    def _fetch_existing(self, strategy_id: str) -> dict | None:
        res = (
            self._client.table(self.TABLE).select("enabled")
            .eq("id", strategy_id).limit(1).execute()
        )
        return res.data[0] if res.data else None


class PostgresStrategyRegistry:
    HISTORY_TABLE = "strategy_enable_history"

    def __init__(self, dsn: str):
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)
        # Preserve DB-side enabled across YAML re-uploads.
        sql = (
            "insert into strategies "
            "(id, yaml_text, enabled, mode, market, symbol, timeframe, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, now(), now()) "
            "on conflict (id) do update set "
            "yaml_text = excluded.yaml_text, "
            # NB: do NOT overwrite enabled — set_enabled is the source of truth
            "mode = excluded.mode, "
            "market = excluded.market, "
            "symbol = excluded.symbol, "
            "timeframe = excluded.timeframe, "
            "updated_at = now()"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                parsed.id, yaml_text, parsed.enabled, parsed.mode,
                parsed.market, parsed.symbol, parsed.timeframe,
            ))
            conn.commit()
        return self.get(parsed.id)

    def get(self, strategy_id: str) -> StrategyRecord:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select id, yaml_text, enabled, created_at, updated_at "
                "from strategies where id = %s",
                (strategy_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise StrategyNotFound(strategy_id)
        return _row_tuple_to_record(row)

    def delete(self, strategy_id: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from strategies where id = %s", (strategy_id,))
            n = cur.rowcount
            conn.commit()
        return n > 0

    def list_all(self) -> list[StrategyRecord]:
        return self._list_query("")

    def list_active(self) -> list[StrategyRecord]:
        return self._list_query(" where enabled = true")

    def set_enabled(
        self,
        strategy_id: str,
        enabled: bool,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> StrategyRecord:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update strategies set enabled = %s, updated_at = now() "
                "where id = %s returning id",
                (enabled, strategy_id),
            )
            if cur.fetchone() is None:
                conn.rollback()
                raise StrategyNotFound(strategy_id)
            cur.execute(
                f"insert into {self.HISTORY_TABLE} "
                "(strategy_id, enabled, reason, actor, created_at) "
                "values (%s, %s, %s, %s, now())",
                (strategy_id, enabled, reason, actor),
            )
            conn.commit()
        return self.get(strategy_id)

    def enable_history(
        self, strategy_id: str, *, limit: int = 50,
    ) -> list[EnableEvent]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"select strategy_id, enabled, reason, actor, created_at "
                f"from {self.HISTORY_TABLE} where strategy_id = %s "
                f"order by created_at desc limit %s",
                (strategy_id, limit),
            )
            rows = cur.fetchall()
        return [
            EnableEvent(
                strategy_id=r[0], enabled=r[1], reason=r[2],
                actor=r[3], created_at=r[4],
            )
            for r in rows
        ]

    def _list_query(self, where: str) -> list[StrategyRecord]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"select id, yaml_text, enabled, created_at, updated_at "
                f"from strategies{where} order by id"
            )
            rows = cur.fetchall()
        return [_row_tuple_to_record(r) for r in rows]


# ================================================================== #
# Helpers
# ================================================================== #
def _row_to_record(row: dict[str, Any]) -> StrategyRecord:
    parsed = load_strategy_str(row["yaml_text"])
    # DB column wins over YAML for `enabled` (round 25)
    if "enabled" in row and row["enabled"] is not None:
        parsed = _override_enabled(parsed, bool(row["enabled"]))
    return StrategyRecord(
        id=row["id"],
        yaml_text=row["yaml_text"],
        parsed=parsed,
        created_at=_parse_iso(row.get("created_at")),
        updated_at=_parse_iso(row.get("updated_at")),
    )


def _row_to_event(row: dict[str, Any]) -> EnableEvent:
    return EnableEvent(
        strategy_id=row["strategy_id"],
        enabled=bool(row["enabled"]),
        reason=row.get("reason"),
        actor=row.get("actor"),
        created_at=_parse_iso(row.get("created_at")) or datetime.now(timezone.utc),
    )


def _row_tuple_to_record(row: tuple) -> StrategyRecord:
    # New shape (round 25): (id, yaml_text, enabled, created_at, updated_at)
    rid, yaml_text, enabled, created_at, updated_at = row
    parsed = load_strategy_str(yaml_text)
    if enabled is not None:
        parsed = _override_enabled(parsed, bool(enabled))
    return StrategyRecord(
        id=rid,
        yaml_text=yaml_text,
        parsed=parsed,
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_registry(settings) -> StrategyRegistry:  # noqa: ANN001
    """Pick best backend. Mirrors signals.history / snapshots / credentials."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("strategy registry: PostgresStrategyRegistry")
        return PostgresStrategyRegistry(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("strategy registry: SupabaseStrategyRegistry")
            return SupabaseStrategyRegistry(client)
        except ImportError:
            logger.warning("strategy registry: supabase-py not installed")

    logger.warning("strategy registry: InMemory (NOT for prod)")
    return InMemoryStrategyRegistry()


__all__ = [
    "EnableEvent",
    "StrategyRecord",
    "StrategyRegistry",
    "StrategyNotFound",
    "InMemoryStrategyRegistry",
    "SupabaseStrategyRegistry",
    "PostgresStrategyRegistry",
    "build_registry",
]
