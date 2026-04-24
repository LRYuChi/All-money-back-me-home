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
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

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

    def __init__(self, master_keys: list[str] | None = None):
        # Allow tests to inject a key without monkeypatching env
        self._keys = master_keys or _master_keys_from_env()
        self._rows: dict[str, _StoredRow] = {}

    def write(self, name: str, plaintext: str, *, description: str = "") -> None:
        ct = encrypt(plaintext, self._keys)
        existing = self._rows.get(name)
        self._rows[name] = _StoredRow(
            ciphertext=ct,
            description=description or (existing.description if existing else ""),
            rotated_at=datetime.now(timezone.utc) if existing else None,
            created_at=existing.created_at if existing else datetime.now(timezone.utc),
        )

    def read(self, name: str) -> CredentialRecord:
        row = self._rows.get(name)
        if row is None:
            raise CredentialNotFound(name)
        plaintext = decrypt(row.ciphertext, self._keys)
        return CredentialRecord(
            name=name, plaintext=plaintext,
            description=row.description,
            rotated_at=row.rotated_at, created_at=row.created_at,
        )

    def delete(self, name: str) -> bool:
        return self._rows.pop(name, None) is not None

    def list_names(self) -> list[str]:
        return sorted(self._rows.keys())


class SupabaseCredentialStore:
    """Stores rows in `secrets` table via supabase-py REST."""

    TABLE = "secrets"

    def __init__(self, client: Any, master_keys: list[str] | None = None):
        self._client = client
        self._keys = master_keys or _master_keys_from_env()

    def write(self, name: str, plaintext: str, *, description: str = "") -> None:
        ct = encrypt(plaintext, self._keys)
        # Upsert by name (PK)
        self._client.table(self.TABLE).upsert({
            "name": name,
            "ciphertext": ct,
            "description": description,
            "rotated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="name").execute()

    def read(self, name: str) -> CredentialRecord:
        res = self._client.table(self.TABLE).select("*").eq("name", name).limit(1).execute()
        if not res.data:
            raise CredentialNotFound(name)
        row = res.data[0]
        plaintext = decrypt(row["ciphertext"], self._keys)
        return CredentialRecord(
            name=name,
            plaintext=plaintext,
            description=row.get("description") or "",
            rotated_at=_parse_iso(row.get("rotated_at")),
            created_at=_parse_iso(row.get("created_at")),
        )

    def delete(self, name: str) -> bool:
        res = self._client.table(self.TABLE).delete().eq("name", name).execute()
        return bool(res.data)

    def list_names(self) -> list[str]:
        res = self._client.table(self.TABLE).select("name").order("name").execute()
        return [r["name"] for r in (res.data or [])]


class PostgresCredentialStore:
    """Stores rows in `secrets` table via direct psycopg."""

    def __init__(self, dsn: str, master_keys: list[str] | None = None):
        self._dsn = dsn
        self._keys = master_keys or _master_keys_from_env()

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def write(self, name: str, plaintext: str, *, description: str = "") -> None:
        ct = encrypt(plaintext, self._keys)
        sql = (
            "insert into secrets (name, ciphertext, description, created_at, rotated_at) "
            "values (%s, %s, %s, now(), now()) "
            "on conflict (name) do update set "
            "ciphertext = excluded.ciphertext, "
            "description = coalesce(nullif(excluded.description, ''), secrets.description), "
            "rotated_at = now()"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (name, ct, description))
            conn.commit()

    def read(self, name: str) -> CredentialRecord:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select ciphertext, description, rotated_at, created_at "
                "from secrets where name = %s",
                (name,),
            )
            row = cur.fetchone()
        if row is None:
            raise CredentialNotFound(name)
        ct, description, rotated_at, created_at = row
        plaintext = decrypt(ct, self._keys)
        return CredentialRecord(
            name=name, plaintext=plaintext,
            description=description or "",
            rotated_at=rotated_at, created_at=created_at,
        )

    def delete(self, name: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("delete from secrets where name = %s", (name,))
            n = cur.rowcount
            conn.commit()
        return n > 0

    def list_names(self) -> list[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("select name from secrets order by name")
            rows = cur.fetchall()
        return [r[0] for r in rows]


# ================================================================== #
# Helpers + factory + cached top-level accessor
# ================================================================== #
def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_store(settings) -> CredentialStore:  # noqa: ANN001
    """Factory mirroring signals.history priority: Postgres > Supabase > InMemory.

    InMemory is OK as a fallback for tests / dev; in prod we always have
    one of the DB options.
    """
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("credential store: PostgresCredentialStore")
        return PostgresCredentialStore(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("credential store: SupabaseCredentialStore")
            return SupabaseCredentialStore(client)
        except ImportError:
            logger.warning("credential store: supabase-py not installed; using InMemory")

    logger.warning(
        "credential store: no DB configured → InMemory (NOT for prod). "
        "Set DATABASE_URL or SUPABASE_URL+KEY.",
    )
    return InMemoryCredentialStore()


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
