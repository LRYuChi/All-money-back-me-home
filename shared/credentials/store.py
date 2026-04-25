"""Encrypted credential store + 4 backends.

Reads MASTER_SECRET from env at construction time; raises if absent so
callers fail loudly rather than fall back to plaintext silently.

Each row stores:
  - name (e.g. 'OKX_API_KEY')
  - ciphertext (Fernet base64)
  - description (free-form, for ops convenience)
  - rotated_at, created_at

`get_credential(name)` is the convenience top-level function — it calls
`build_store()` once per process (cached) and decrypts on demand.
Callers don't need to know about the store.

Round 34: every write/read/delete triggers `audit_hook.record(...)` so
`secret_access_log` has a complete trail of who/what accessed which key
when. Audit is fire-and-forget (NoOpAuditHook by default; build_store
wires the matching DB backend).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from shared.credentials.audit import (
    AuditHook,
    NoOpAuditHook,
    build_audit_hook,
)
from shared.credentials.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

_MASTER_KEY_ENV = "MASTER_SECRET"


class CredentialNotFound(KeyError):
    """Requested name has no row in the store."""


@dataclass(slots=True, frozen=True)
class CredentialRecord:
    """Returned by `read()`. Already decrypted."""

    name: str
    plaintext: str
    description: str = ""
    rotated_at: datetime | None = None
    created_at: datetime | None = None


class CredentialStore(Protocol):
    def write(
        self, name: str, plaintext: str, *, description: str = ""
    ) -> None: ...
    def read(self, name: str) -> CredentialRecord: ...
    def delete(self, name: str) -> bool: ...
    def list_names(self) -> list[str]: ...


# ================================================================== #
# Master-key helpers
# ================================================================== #
def _master_keys_from_env() -> list[str]:
    """Read MASTER_SECRET (primary) + optional MASTER_SECRET_OLD (rotation grace).

    During key rotation:
      MASTER_SECRET=<new key>     ← encrypts new writes; decrypts new ciphertexts
      MASTER_SECRET_OLD=<old key> ← decrypts ciphertexts written before rotation
    Drop MASTER_SECRET_OLD after every secret is re-encrypted with the new key.
    """
    primary = (os.environ.get(_MASTER_KEY_ENV, "") or "").strip()
    if not primary:
        raise RuntimeError(
            f"{_MASTER_KEY_ENV} env var is required for credential store. "
            "Generate one with `python -m shared.credentials.cli.gen_key`."
        )
    keys = [primary]
    legacy = (os.environ.get("MASTER_SECRET_OLD", "") or "").strip()
    if legacy:
        keys.append(legacy)
    return keys


# ================================================================== #
# Implementations
# ================================================================== #
@dataclass(slots=True)
class _StoredRow:
    """Internal — what each backend persists. Plaintext is NEVER stored."""
    ciphertext: str
    description: str = ""
    rotated_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryCredentialStore:
    """Tests + smoke. Master key from env at construction (same contract as prod)."""

    TABLE = "secrets"

    def __init__(
        self,
        master_keys: list[str] | None = None,
        *,
        audit_hook: AuditHook | None = None,
    ):
        # Allow tests to inject a key without monkeypatching env
        self._keys = master_keys or _master_keys_from_env()
        self._rows: dict[str, _StoredRow] = {}
        self._audit = audit_hook or NoOpAuditHook()

    def write(
        self, name: str, plaintext: str, *,
        description: str = "", actor: str | None = None,
    ) -> None:
        try:
            ct = encrypt(plaintext, self._keys)
            existing = self._rows.get(name)
            self._rows[name] = _StoredRow(
                ciphertext=ct,
                description=description or (existing.description if existing else ""),
                rotated_at=datetime.now(timezone.utc) if existing else None,
                created_at=existing.created_at if existing else datetime.now(timezone.utc),
            )
        except Exception as e:
            self._audit.record(name, "write", actor=actor, success=False,
                               notes=f"{type(e).__name__}: {e}")
            raise
        op = "rotate" if existing else "write"
        self._audit.record(name, op, actor=actor, success=True)

    def read(
        self, name: str, *, actor: str | None = None,
    ) -> CredentialRecord:
        row = self._rows.get(name)
        if row is None:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes="not found")
            raise CredentialNotFound(name)
        try:
            plaintext = decrypt(row.ciphertext, self._keys)
        except Exception as e:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes=f"decrypt error: {type(e).__name__}")
            raise
        self._audit.record(name, "read", actor=actor, success=True)
        return CredentialRecord(
            name=name, plaintext=plaintext,
            description=row.description,
            rotated_at=row.rotated_at, created_at=row.created_at,
        )

    def delete(self, name: str, *, actor: str | None = None) -> bool:
        existed = self._rows.pop(name, None) is not None
        self._audit.record(
            name, "delete", actor=actor, success=existed,
            notes=None if existed else "not found",
        )
        return existed

    def list_names(self) -> list[str]:
        return sorted(self._rows.keys())

    def audit_history(self, name: str, *, limit: int = 50):
        return self._audit.history(name, limit=limit)


class SupabaseCredentialStore:
    """Stores rows in `secrets` table via supabase-py REST."""

    TABLE = "secrets"

    def __init__(
        self, client: Any, master_keys: list[str] | None = None,
        *,
        audit_hook: AuditHook | None = None,
    ):
        self._client = client
        self._keys = master_keys or _master_keys_from_env()
        self._audit = audit_hook or NoOpAuditHook()

    def write(
        self, name: str, plaintext: str, *,
        description: str = "", actor: str | None = None,
    ) -> None:
        existed = self._exists(name)
        try:
            ct = encrypt(plaintext, self._keys)
            self._client.table(self.TABLE).upsert({
                "name": name,
                "ciphertext": ct,
                "description": description,
                "rotated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="name").execute()
        except Exception as e:
            self._audit.record(name, "write", actor=actor, success=False,
                               notes=f"{type(e).__name__}: {e}")
            raise
        self._audit.record(
            name, "rotate" if existed else "write",
            actor=actor, success=True,
        )

    def _exists(self, name: str) -> bool:
        try:
            res = (
                self._client.table(self.TABLE).select("name")
                .eq("name", name).limit(1).execute()
            )
            return bool(res.data)
        except Exception:
            return False

    def read(
        self, name: str, *, actor: str | None = None,
    ) -> CredentialRecord:
        try:
            res = self._client.table(self.TABLE).select("*").eq("name", name).limit(1).execute()
        except Exception as e:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes=f"query error: {type(e).__name__}")
            raise
        if not res.data:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes="not found")
            raise CredentialNotFound(name)
        row = res.data[0]
        try:
            plaintext = decrypt(row["ciphertext"], self._keys)
        except Exception as e:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes=f"decrypt error: {type(e).__name__}")
            raise
        self._audit.record(name, "read", actor=actor, success=True)
        return CredentialRecord(
            name=name,
            plaintext=plaintext,
            description=row.get("description") or "",
            rotated_at=_parse_iso(row.get("rotated_at")),
            created_at=_parse_iso(row.get("created_at")),
        )

    def delete(self, name: str, *, actor: str | None = None) -> bool:
        try:
            res = self._client.table(self.TABLE).delete().eq("name", name).execute()
        except Exception as e:
            self._audit.record(name, "delete", actor=actor, success=False,
                               notes=f"{type(e).__name__}: {e}")
            raise
        ok = bool(res.data)
        self._audit.record(
            name, "delete", actor=actor, success=ok,
            notes=None if ok else "not found",
        )
        return ok

    def list_names(self) -> list[str]:
        res = self._client.table(self.TABLE).select("name").order("name").execute()
        return [r["name"] for r in (res.data or [])]

    def audit_history(self, name: str, *, limit: int = 50):
        return self._audit.history(name, limit=limit)


class PostgresCredentialStore:
    """Stores rows in `secrets` table via direct psycopg."""

    def __init__(
        self, dsn: str, master_keys: list[str] | None = None,
        *,
        audit_hook: AuditHook | None = None,
    ):
        self._dsn = dsn
        self._keys = master_keys or _master_keys_from_env()
        self._audit = audit_hook or NoOpAuditHook()

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def write(
        self, name: str, plaintext: str, *,
        description: str = "", actor: str | None = None,
    ) -> None:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "select 1 from secrets where name = %s", (name,),
                )
                existed = cur.fetchone() is not None
                ct = encrypt(plaintext, self._keys)
                sql = (
                    "insert into secrets (name, ciphertext, description, created_at, rotated_at) "
                    "values (%s, %s, %s, now(), now()) "
                    "on conflict (name) do update set "
                    "ciphertext = excluded.ciphertext, "
                    "description = coalesce(nullif(excluded.description, ''), secrets.description), "
                    "rotated_at = now()"
                )
                cur.execute(sql, (name, ct, description))
                conn.commit()
        except Exception as e:
            self._audit.record(name, "write", actor=actor, success=False,
                               notes=f"{type(e).__name__}: {e}")
            raise
        self._audit.record(
            name, "rotate" if existed else "write",
            actor=actor, success=True,
        )

    def read(
        self, name: str, *, actor: str | None = None,
    ) -> CredentialRecord:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "select ciphertext, description, rotated_at, created_at "
                    "from secrets where name = %s",
                    (name,),
                )
                row = cur.fetchone()
        except Exception as e:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes=f"query error: {type(e).__name__}")
            raise
        if row is None:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes="not found")
            raise CredentialNotFound(name)
        ct, description, rotated_at, created_at = row
        try:
            plaintext = decrypt(ct, self._keys)
        except Exception as e:
            self._audit.record(name, "read", actor=actor, success=False,
                               notes=f"decrypt error: {type(e).__name__}")
            raise
        self._audit.record(name, "read", actor=actor, success=True)
        return CredentialRecord(
            name=name, plaintext=plaintext,
            description=description or "",
            rotated_at=rotated_at, created_at=created_at,
        )

    def delete(self, name: str, *, actor: str | None = None) -> bool:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("delete from secrets where name = %s", (name,))
                n = cur.rowcount
                conn.commit()
        except Exception as e:
            self._audit.record(name, "delete", actor=actor, success=False,
                               notes=f"{type(e).__name__}: {e}")
            raise
        ok = n > 0
        self._audit.record(
            name, "delete", actor=actor, success=ok,
            notes=None if ok else "not found",
        )
        return ok

    def list_names(self) -> list[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("select name from secrets order by name")
            rows = cur.fetchall()
        return [r[0] for r in rows]

    def audit_history(self, name: str, *, limit: int = 50):
        return self._audit.history(name, limit=limit)


# ================================================================== #
# Helpers + factory + cached top-level accessor
# ================================================================== #
def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_store(settings) -> CredentialStore:  # noqa: ANN001
    """Factory mirroring signals.history priority: Postgres > Supabase > InMemory.

    Round 34: also wires the matching audit hook so every store call
    appends a row to `secret_access_log`. Tests can construct stores
    directly to opt out of audit (audit_hook=None → defaults to NoOp).
    """
    audit = build_audit_hook(settings)

    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("credential store: PostgresCredentialStore (audit=PG)")
        return PostgresCredentialStore(dsn, audit_hook=audit)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("credential store: SupabaseCredentialStore (audit=Supabase)")
            return SupabaseCredentialStore(client, audit_hook=audit)
        except ImportError:
            logger.warning("credential store: supabase-py not installed; using InMemory")

    logger.warning(
        "credential store: no DB configured → InMemory (NOT for prod). "
        "Set DATABASE_URL or SUPABASE_URL+KEY.",
    )
    return InMemoryCredentialStore(audit_hook=audit)


_cached_store: CredentialStore | None = None


def get_credential(name: str, *, settings=None, refresh: bool = False) -> str:  # noqa: ANN001
    """Top-level convenience. Caches the store across the process lifetime
    (encrypt/decrypt is fast but DB round-trip per get is wasteful).

    Pass `refresh=True` when you need to bust the cache (rotation, tests).
    """
    global _cached_store
    if _cached_store is None or refresh:
        # Lazy-import settings to avoid pulling smart_money at module import
        if settings is None:
            from smart_money.config import settings as _settings
            settings = _settings
        _cached_store = build_store(settings)
    return _cached_store.read(name).plaintext


__all__ = [
    "CredentialNotFound",
    "CredentialRecord",
    "CredentialStore",
    "InMemoryCredentialStore",
    "SupabaseCredentialStore",
    "PostgresCredentialStore",
    "build_store",
    "get_credential",
]


# Re-export audit primitives so callers can `from shared.credentials.store
# import with_actor` without a second import line.
from shared.credentials.audit import with_actor  # noqa: E402,F401
