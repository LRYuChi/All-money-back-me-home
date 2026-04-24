-- ================================================================
-- L7 Observability — signal_history + fused_signals + strategy_intents
-- Migration: 016_signal_history.sql
-- 見 docs/AI_MULTIMARKET_ROADMAP.md §5 (L2), §6 (L3), §7 (L4), §10 (L7)
-- ================================================================

-- ----------------------------------------------------------------
-- signal_history — every UniversalSignal from every source is persisted
-- here for reflection/calibration. 90 days retention (per R6 decision).
-- ----------------------------------------------------------------
create table if not exists signal_history (
    id                bigserial primary key,
    source            text not null check (source in ('kronos','smart_money','ta','ai_llm','macro')),
    symbol            text not null,                     -- canonical, e.g. "crypto:OKX:BTC/USDT:USDT"
    horizon           text not null check (horizon in ('15m','1h','4h','1d')),
    direction         text not null check (direction in ('long','short','neutral')),
    strength          numeric(5,4) not null check (strength between 0 and 1),
    reason            text,
    details           jsonb not null default '{}'::jsonb, -- source-specific payload
    ts                timestamptz not null,
    expires_at        timestamptz not null,

    -- Reflection (L7) fills these 7+ days later:
    actual_return_pct numeric(10,4),                     -- realised forward return at horizon
    was_correct       boolean,                           -- direction match with actual
    validated_at      timestamptz,

    created_at        timestamptz not null default now()
);

create index if not exists idx_signal_history_ts
    on signal_history (ts desc);
create index if not exists idx_signal_history_source_symbol
    on signal_history (source, symbol, ts desc);
create index if not exists idx_signal_history_unvalidated
    on signal_history (ts)
    where validated_at is null;

-- ----------------------------------------------------------------
-- fused_signals — fusion layer (L3) outputs, one row per (symbol, horizon, ts)
-- ----------------------------------------------------------------
create table if not exists fused_signals (
    id                bigserial primary key,
    symbol            text not null,
    horizon           text not null check (horizon in ('15m','1h','4h','1d')),
    direction         text not null check (direction in ('long','short','neutral')),
    ensemble_score    numeric(5,4) not null check (ensemble_score between 0 and 1),
    regime            text not null,                     -- e.g. BULL_TRENDING
    sources_count     int not null,
    contributions     jsonb not null default '{}'::jsonb,  -- {source: weighted_score}
    conflict          boolean not null default false,
    ts                timestamptz not null,
    created_at        timestamptz not null default now()
);

create index if not exists idx_fused_signals_ts
    on fused_signals (ts desc);
create index if not exists idx_fused_signals_symbol_horizon
    on fused_signals (symbol, horizon, ts desc);

-- ----------------------------------------------------------------
-- strategy_intents — L4 strategy layer outputs (pre-risk-sizing)
-- ----------------------------------------------------------------
create table if not exists strategy_intents (
    id                    bigserial primary key,
    strategy_id           text not null,
    symbol                text not null,
    direction             text not null check (direction in ('long','short','neutral')),
    target_notional_usd   numeric(18,2) not null,
    entry_price_ref       numeric(30,10),
    stop_loss_pct         numeric(5,4),
    take_profit_pct       numeric(5,4),
    fused_signal_id       bigint references fused_signals(id) on delete set null,
    ts                    timestamptz not null,
    created_at            timestamptz not null default now()
);

create index if not exists idx_strategy_intents_strategy_ts
    on strategy_intents (strategy_id, ts desc);
create index if not exists idx_strategy_intents_ts
    on strategy_intents (ts desc);
