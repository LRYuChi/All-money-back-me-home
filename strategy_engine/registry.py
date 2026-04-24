"""Strategy registry — store/load YAML strategies in DB.

Storage shape:
  - strategies table: id (PK) + yaml_text + enabled + mode + updated_at
  - load_active() returns parsed StrategyDef list ready for evaluation
  - upsert(yaml_text) parses to validate before storing — bad YAML never
    lands in DB

Same backend pattern as shared.signals.history / shared.snapshots:
NoOp + InMemory + Supabase + Postgres + factory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from strategy_engine.dsl import load_strategy_str
from strategy_engine.types import StrategyDef

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class StrategyRecord:
    """One row in `strategies` — has YAML text + parsed def + metadata."""

    id: str
    yaml_text: str
    parsed: StrategyDef
    updated_at: datetime | None = None
    created_at: datetime | None = None


class StrategyRegistry(Protocol):
    def upsert(self, yaml_text: str) -> StrategyRecord: ...
    def get(self, strategy_id: str) -> StrategyRecord: ...
    def delete(self, strategy_id: str) -> bool: ...
    def list_all(self) -> list[StrategyRecord]: ...
    def list_active(self) -> list[StrategyRecord]: ...


class StrategyNotFound(KeyError):
    """No row with that id."""


# ================================================================== #
# Implementations
# ================================================================== #
class InMemoryStrategyRegistry:
    """For tests + smoke. Maintains insertion order for stable list_all."""

    def __init__(self) -> None:
        self._records: dict[str, StrategyRecord] = {}

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)  # raises DSLError on bad input
        now = datetime.now(timezone.utc)
        existing = self._records.get(parsed.id)
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
        return self._records.pop(strategy_id, None) is not None

    def list_all(self) -> list[StrategyRecord]:
        return list(self._records.values())

    def list_active(self) -> list[StrategyRecord]:
        return [r for r in self._records.values() if r.parsed.enabled]


class SupabaseStrategyRegistry:
    TABLE = "strategies"

    def __init__(self, client: Any):
        self._client = client

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)
        now = datetime.now(timezone.utc).isoformat()
        self._client.table(self.TABLE).upsert({
            "id": parsed.id,
            "yaml_text": yaml_text,
            "enabled": parsed.enabled,
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


class PostgresStrategyRegistry:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def upsert(self, yaml_text: str) -> StrategyRecord:
        parsed = load_strategy_str(yaml_text)
        sql = (
            "insert into strategies "
            "(id, yaml_text, enabled, mode, market, symbol, timeframe, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, now(), now()) "
            "on conflict (id) do update set "
            "yaml_text = excluded.yaml_text, "
            "enabled = excluded.enabled, "
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
                "select id, yaml_text, created_at, updated_at "
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

    def _list_query(self, where: str) -> list[StrategyRecord]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"select id, yaml_text, created_at, updated_at "
                f"from strategies{where} order by id"
            )
            rows = cur.fetchall()
        return [_row_tuple_to_record(r) for r in rows]


# ================================================================== #
# Helpers
# ================================================================== #
def _row_to_record(row: dict[str, Any]) -> StrategyRecord:
    parsed = load_strategy_str(row["yaml_text"])
    return StrategyRecord(
        id=row["id"],
        yaml_text=row["yaml_text"],
        parsed=parsed,
        created_at=_parse_iso(row.get("created_at")),
        updated_at=_parse_iso(row.get("updated_at")),
    )


def _row_tuple_to_record(row: tuple) -> StrategyRecord:
    rid, yaml_text, created_at, updated_at = row
    return StrategyRecord(
        id=rid,
        yaml_text=yaml_text,
        parsed=load_strategy_str(yaml_text),
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
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
    "StrategyRecord",
    "StrategyRegistry",
    "StrategyNotFound",
    "InMemoryStrategyRegistry",
    "SupabaseStrategyRegistry",
    "PostgresStrategyRegistry",
    "build_registry",
]
