-- ================================================================
-- Encrypted credential storage
-- Migration: 018_secrets.sql
-- 見 docs/QUANTDINGER_REFERENCE_PLAN.md P2-8 / docs/AI_MULTIMARKET_ROADMAP.md Phase B
-- ================================================================

-- ----------------------------------------------------------------
-- secrets — Fernet-encrypted blobs keyed by stable name.
-- ciphertext = base64(IV || HMAC || ciphertext) — Fernet token, ~150B
-- for a typical API key. Master key NEVER lives in this row; only in
-- env (MASTER_SECRET) on each application host.
--
-- RLS recommendation: enable strict RLS so only service-role can read.
-- ----------------------------------------------------------------
create table if not exists secrets (
    name          text primary key,                 -- e.g. 'OKX_API_KEY'
    ciphertext    text not null,                    -- Fernet base64
    description   text,                             -- ops convenience
    created_at    timestamptz not null default now(),
    rotated_at    timestamptz                       -- last write
);

-- Audit log for read/write events. Keep separate from `secrets` so we
-- can apply different RLS / retention policies. Avoid logging plaintext
-- values; only metadata (who, when, op).
create table if not exists secret_access_log (
    id           bigserial primary key,
    name         text not null,
    op           text not null check (op in ('read', 'write', 'delete', 'rotate')),
    actor        text,                              -- which service / user
    success      boolean not null,
    notes        text,
    created_at   timestamptz not null default now()
);

create index if not exists idx_secret_access_log_name_ts
    on secret_access_log (name, created_at desc);
create index if not exists idx_secret_access_log_ts
    on secret_access_log (created_at desc);
