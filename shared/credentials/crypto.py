"""Symmetric AEAD over Fernet.

Fernet (cryptography lib) is AES-128-CBC + HMAC-SHA256, with built-in
timestamping and replay protection. Industry standard for "I just need
to encrypt small secrets in a DB" — used by Hashicorp Vault, AWS SSM,
and a million Django apps.

Why Fernet vs raw AES:
  - Authenticated (tampering = decryption error, not silent corruption)
  - Includes timestamp (we don't use TTL but it's safe)
  - Single-key API — no IV management, no padding mistakes
  - Round-trip-safe Base64 ciphertext (DB-friendly)

Master key:
  - 32 bytes URL-safe base64
  - Generated once with `generate_master_key()` and stored in
    `MASTER_SECRET` env on each host (NOT git-checked-in!)
  - Same master key across services that share secrets
  - Rotation: encrypt every secret with new key, update env, restart;
    `decrypt()` accepts a list of keys for grace period
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class InvalidMasterKey(ValueError):
    """Master key is not a valid 32-byte URL-safe base64 Fernet key."""


class DecryptionFailure(Exception):
    """Ciphertext is corrupt, expired, or encrypted with a different key."""


def generate_master_key() -> bytes:
    """Create a fresh master key. Caller stores in env. Never log this."""
    return Fernet.generate_key()


def _make_engine(master_keys: bytes | str | list[bytes | str]) -> MultiFernet:
    """Build MultiFernet from one or many keys (rotation grace).

    First key encrypts; all keys can decrypt. So during rotation:
      - Add NEW key first → still using OLD key for encryption
      - Re-encrypt all stored secrets with NEW (loop)
      - Drop OLD key
    """
    keys_iter = master_keys if isinstance(master_keys, list) else [master_keys]
    fernets = []
    for k in keys_iter:
        if isinstance(k, str):
            k = k.encode("ascii")
        try:
            fernets.append(Fernet(k))
        except (ValueError, TypeError) as e:
            raise InvalidMasterKey(f"key invalid: {e}") from e
    if not fernets:
        raise InvalidMasterKey("no master keys provided")
    return MultiFernet(fernets)


def encrypt(plaintext: str, master_keys: bytes | str | list[bytes | str]) -> str:
    """Encrypt UTF-8 plaintext. Returns base64-encoded ciphertext (str)."""
    engine = _make_engine(master_keys)
    if not isinstance(plaintext, str):
        raise TypeError(f"plaintext must be str, got {type(plaintext).__name__}")
    return engine.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, master_keys: bytes | str | list[bytes | str]) -> str:
    """Decrypt to UTF-8 plaintext. Raises DecryptionFailure on tamper / wrong key."""
    engine = _make_engine(master_keys)
    if not isinstance(ciphertext, str):
        raise TypeError(f"ciphertext must be str, got {type(ciphertext).__name__}")
    try:
        return engine.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise DecryptionFailure("invalid token (tampered, wrong key, or corrupt)") from e


__all__ = [
    "encrypt",
    "decrypt",
    "generate_master_key",
    "InvalidMasterKey",
    "DecryptionFailure",
]
