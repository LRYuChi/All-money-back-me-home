"""Tests for shared.credentials — crypto round-trip + store backends."""
from __future__ import annotations

import pytest

from shared.credentials import (
    CredentialNotFound,
    DecryptionFailure,
    InMemoryCredentialStore,
    InvalidMasterKey,
    build_store,
    decrypt,
    encrypt,
    generate_master_key,
)


# ================================================================== #
# crypto.py — encrypt/decrypt
# ================================================================== #
def test_round_trip_basic():
    key = generate_master_key()
    plaintext = "OKX_API_KEY_value_with_spaces and 數字 123"
    ct = encrypt(plaintext, key)
    assert ct != plaintext
    assert decrypt(ct, key) == plaintext


def test_round_trip_empty_string():
    key = generate_master_key()
    assert decrypt(encrypt("", key), key) == ""


def test_round_trip_unicode():
    key = generate_master_key()
    pt = "日本語テスト 🚀 emoji"
    assert decrypt(encrypt(pt, key), key) == pt


def test_ciphertext_changes_per_call_iv_randomization():
    """Same plaintext + key should produce different ciphertexts (IV randomized)."""
    key = generate_master_key()
    a = encrypt("hello", key)
    b = encrypt("hello", key)
    assert a != b
    assert decrypt(a, key) == "hello" == decrypt(b, key)


def test_decrypt_with_wrong_key_raises():
    key1 = generate_master_key()
    key2 = generate_master_key()
    ct = encrypt("secret", key1)
    with pytest.raises(DecryptionFailure):
        decrypt(ct, key2)


def test_tampered_ciphertext_raises():
    key = generate_master_key()
    ct = encrypt("secret", key)
    # Flip a character in the middle
    bad = ct[:30] + ("A" if ct[30] != "A" else "B") + ct[31:]
    with pytest.raises(DecryptionFailure):
        decrypt(bad, key)


def test_invalid_master_key_raises():
    with pytest.raises(InvalidMasterKey):
        encrypt("x", "not-a-valid-fernet-key")


def test_empty_keys_raises():
    with pytest.raises(InvalidMasterKey):
        encrypt("x", [])


# ================================================================== #
# Key rotation: MultiFernet accepts list, encrypts with first
# ================================================================== #
def test_rotation_grace_period():
    old = generate_master_key()
    new = generate_master_key()
    # Phase 1: encrypted with OLD only
    ct = encrypt("api_key", old)

    # Phase 2: rotation — env now has NEW first, OLD second
    # NEW writes go through; OLD ciphertexts still readable
    rotation_keys = [new, old]
    assert decrypt(ct, rotation_keys) == "api_key"

    # Phase 3: re-encrypt with new
    fresh_ct = encrypt("api_key", rotation_keys)
    # After rotation period ends, drop old key — fresh_ct still works with NEW alone
    assert decrypt(fresh_ct, new) == "api_key"


def test_old_ciphertext_fails_after_rotation_completed():
    """If we've dropped the old key, ciphertexts written under it can't be read."""
    old = generate_master_key()
    new = generate_master_key()
    ct = encrypt("secret", old)
    with pytest.raises(DecryptionFailure):
        decrypt(ct, new)


# ================================================================== #
# Type contracts
# ================================================================== #
def test_encrypt_rejects_bytes():
    """We require str input — Fernet handles utf-8 internally; preventing
    type confusion saves debugging."""
    key = generate_master_key()
    with pytest.raises(TypeError):
        encrypt(b"bytes", key)  # type: ignore[arg-type]


def test_decrypt_rejects_bytes():
    key = generate_master_key()
    ct = encrypt("x", key)
    with pytest.raises(TypeError):
        decrypt(ct.encode("ascii"), key)  # type: ignore[arg-type]


# ================================================================== #
# InMemoryCredentialStore
# ================================================================== #
@pytest.fixture
def store():
    """Inject a fresh master key without touching env."""
    key = generate_master_key().decode("ascii")
    return InMemoryCredentialStore(master_keys=[key])


def test_store_write_then_read(store):
    store.write("OKX_API_KEY", "sk_live_abc123", description="Production OKX")
    rec = store.read("OKX_API_KEY")
    assert rec.plaintext == "sk_live_abc123"
    assert rec.description == "Production OKX"
    assert rec.created_at is not None


def test_store_overwrite_sets_rotated_at(store):
    store.write("X", "v1")
    rec1 = store.read("X")
    assert rec1.rotated_at is None  # first write

    store.write("X", "v2")
    rec2 = store.read("X")
    assert rec2.plaintext == "v2"
    assert rec2.rotated_at is not None
    # created_at should NOT change on rotate
    assert rec2.created_at == rec1.created_at


def test_store_overwrite_preserves_description_when_empty(store):
    store.write("X", "v1", description="initial")
    store.write("X", "v2")  # no description
    rec = store.read("X")
    assert rec.description == "initial"  # preserved


def test_store_overwrite_replaces_description_when_provided(store):
    store.write("X", "v1", description="initial")
    store.write("X", "v2", description="updated")
    rec = store.read("X")
    assert rec.description == "updated"


def test_store_unknown_name_raises(store):
    with pytest.raises(CredentialNotFound):
        store.read("DOES_NOT_EXIST")


def test_store_delete(store):
    store.write("X", "v")
    assert store.delete("X") is True
    assert store.delete("X") is False  # idempotent
    with pytest.raises(CredentialNotFound):
        store.read("X")


def test_store_list_names(store):
    store.write("BBB", "1")
    store.write("AAA", "2")
    store.write("CCC", "3")
    assert store.list_names() == ["AAA", "BBB", "CCC"]


def test_store_ciphertext_stored_not_plaintext(store):
    """Defensive: introspect _rows to confirm we never persist plaintext."""
    store.write("X", "the secret value")
    raw = store._rows["X"].ciphertext
    assert "the secret value" not in raw
    assert "the_secret_value" not in raw


# ================================================================== #
# Master key from env
# ================================================================== #
def test_inmemory_reads_master_secret_from_env(monkeypatch):
    key = generate_master_key().decode("ascii")
    monkeypatch.setenv("MASTER_SECRET", key)
    monkeypatch.delenv("MASTER_SECRET_OLD", raising=False)
    s = InMemoryCredentialStore()  # no explicit keys — should read env
    s.write("X", "v")
    assert s.read("X").plaintext == "v"


def test_missing_master_secret_raises(monkeypatch):
    monkeypatch.delenv("MASTER_SECRET", raising=False)
    monkeypatch.delenv("MASTER_SECRET_OLD", raising=False)
    with pytest.raises(RuntimeError, match="MASTER_SECRET"):
        InMemoryCredentialStore()


def test_master_secret_old_enables_rotation(monkeypatch):
    """During rotation, both keys live in env; old ciphertexts still decrypt."""
    old_key = generate_master_key().decode("ascii")
    new_key = generate_master_key().decode("ascii")

    # Phase 1: write with OLD as primary
    monkeypatch.setenv("MASTER_SECRET", old_key)
    monkeypatch.delenv("MASTER_SECRET_OLD", raising=False)
    s1 = InMemoryCredentialStore()
    s1.write("X", "rotation_test")
    raw_ct = s1._rows["X"].ciphertext

    # Phase 2: switch — NEW is primary, OLD is fallback. New store same DB.
    monkeypatch.setenv("MASTER_SECRET", new_key)
    monkeypatch.setenv("MASTER_SECRET_OLD", old_key)
    s2 = InMemoryCredentialStore()
    # Manually inject the row from phase 1 (simulates same DB)
    s2._rows["X"] = s1._rows["X"]
    assert s2.read("X").plaintext == "rotation_test"


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_postgres_when_dsn_set(monkeypatch):
    from shared.credentials.store import PostgresCredentialStore

    monkeypatch.setenv("MASTER_SECRET", generate_master_key().decode("ascii"))

    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""

    w = build_store(S())
    assert isinstance(w, PostgresCredentialStore)


def test_factory_inmemory_fallback(monkeypatch):
    monkeypatch.setenv("MASTER_SECRET", generate_master_key().decode("ascii"))

    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""

    w = build_store(S())
    assert isinstance(w, InMemoryCredentialStore)
