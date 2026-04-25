"""Encrypted credential storage — keep secrets out of `.env` files.

Threat model
------------
Today: VPS root sees `.env` in plaintext → all keys exposed if VPS is
breached or someone snapshots the disk. We trade *one* secret (master
key, kept in env) for *many* secrets (encrypted in DB).

After this layer:
- VPS .env contains MASTER_SECRET only (32 bytes, base64-urlsafe)
- All other secrets (OKX API keys, broker tokens, etc.) live encrypted
  in the `secrets` Supabase table
- Code calls `get_credential("OKX_API_KEY")` → decrypts on demand
- Audit log captures every read/write (who, when, which key)

This is NOT a hardware HSM, but it raises the bar significantly:
- Disk snapshot of VPS no longer leaks keys
- Supabase backup leaks no longer leak keys
- Old git commits with .env contents (if any) become invalid

API:
    from shared.credentials import (
        encrypt, decrypt, generate_master_key,
        CredentialStore, build_store, get_credential,
    )
"""

from shared.credentials.crypto import (
    decrypt,
    encrypt,
    generate_master_key,
    InvalidMasterKey,
    DecryptionFailure,
)
from shared.credentials.audit import (
    AuditEvent,
    AuditHook,
    AuditOp,
    InMemoryAuditHook,
    NoOpAuditHook,
    PostgresAuditHook,
    SupabaseAuditHook,
    build_audit_hook,
    resolve_actor,
    with_actor,
)
from shared.credentials.store import (
    CredentialNotFound,
    CredentialRecord,
    CredentialStore,
    InMemoryCredentialStore,
    SupabaseCredentialStore,
    PostgresCredentialStore,
    build_store,
    get_credential,
)

__all__ = [
    # crypto
    "encrypt",
    "decrypt",
    "generate_master_key",
    "InvalidMasterKey",
    "DecryptionFailure",
    # store
    "CredentialRecord",
    "CredentialStore",
    "CredentialNotFound",
    "InMemoryCredentialStore",
    "SupabaseCredentialStore",
    "PostgresCredentialStore",
    "build_store",
    "get_credential",
    # audit (round 34)
    "AuditEvent",
    "AuditHook",
    "AuditOp",
    "NoOpAuditHook",
    "InMemoryAuditHook",
    "SupabaseAuditHook",
    "PostgresAuditHook",
    "build_audit_hook",
    "resolve_actor",
    "with_actor",
]
