-- ================================================================
-- Strategy registry — DB-backed YAML strategies
-- Migration: 019_strategies.sql
-- 見 docs/AI_MULTIMARKET_ROADMAP.md Phase E
-- ================================================================

create table if not exists strategies (
    id            text primary key,                -- e.g. 'crypto_btc_follow_whales_kronos_v1'
    yaml_text     text not null,                   -- full strategy YAML for replay
    enabled       boolean not null default true,
    mode          text not null default 'shadow'
                  check (mode in ('shadow','paper','live','notify')),
    market        text not null,                   -- 'crypto' | 'us' | 'tw' | ...
    symbol        text not null,                   -- canonical
    timeframe     text not null check (timeframe in ('15m','1h','4h','1d')),
    description   text,
    tags          text[] not null default '{}',
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists idx_strategies_enabled
    on strategies (enabled) where enabled = true;
create index if not exists idx_strategies_market_symbol
    on strategies (market, symbol);
