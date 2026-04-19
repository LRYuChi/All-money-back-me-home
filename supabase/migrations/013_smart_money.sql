-- ================================================================
-- Smart Money 跟單系統 schema
-- Migration: 013_smart_money.sql
-- 見 docs/SMART_MONEY_MIGRATION.md §7
-- ================================================================

-- ----------------------------------------------------------------
-- 1) wallets — 追蹤的 Hyperliquid 錢包基本資料
-- ----------------------------------------------------------------
create table if not exists sm_wallets (
    id              uuid primary key default gen_random_uuid(),
    address         text unique not null,               -- HL wallet address (0x...)
    first_seen_at   timestamptz not null,
    last_active_at  timestamptz not null,
    tags            text[] not null default '{}',       -- 'whitelisted' | 'watchlist' | 'banned'
    notes           text,                               -- 人工備註
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_sm_wallets_last_active
    on sm_wallets (last_active_at desc);
create index if not exists idx_sm_wallets_tags
    on sm_wallets using gin (tags);

-- ----------------------------------------------------------------
-- 2) wallet_trades — HL 每筆交易紀錄
-- ----------------------------------------------------------------
create table if not exists sm_wallet_trades (
    id              bigserial primary key,
    wallet_id       uuid not null references sm_wallets(id) on delete cascade,
    hl_trade_id     text not null,                      -- HL 原始 trade id
    symbol          text not null,                      -- HL native symbol, e.g. "BTC"
    side            text not null check (side in ('long', 'short')),
    action          text not null check (action in ('open', 'close', 'increase', 'decrease')),
    size            numeric(30, 10) not null,
    price           numeric(30, 10) not null,
    pnl             numeric(30, 10),                    -- 僅 close 有值
    fee             numeric(30, 10) not null default 0,
    ts              timestamptz not null,
    raw             jsonb,                              -- 原始 API payload(除錯用)
    created_at      timestamptz not null default now(),
    unique (wallet_id, hl_trade_id)
);

create index if not exists idx_sm_wallet_trades_wallet_ts
    on sm_wallet_trades (wallet_id, ts desc);
create index if not exists idx_sm_wallet_trades_symbol_ts
    on sm_wallet_trades (symbol, ts desc);

-- ----------------------------------------------------------------
-- 3) rankings — 排名快照(週更新)
-- ----------------------------------------------------------------
create table if not exists sm_rankings (
    id              bigserial primary key,
    snapshot_date   date not null,
    wallet_id       uuid not null references sm_wallets(id) on delete cascade,
    rank            int not null,
    score           numeric(10, 6) not null,
    metrics         jsonb not null,                     -- Sortino / PF / MDD / martingale 等細項
    ai_analysis     jsonb,                              -- Phase 6 才填
    created_at      timestamptz not null default now(),
    unique (snapshot_date, wallet_id)
);

create index if not exists idx_sm_rankings_date_rank
    on sm_rankings (snapshot_date desc, rank);

-- ----------------------------------------------------------------
-- 4) paper_trades — Shadow mode 紙上交易
-- ----------------------------------------------------------------
create table if not exists sm_paper_trades (
    id                  bigserial primary key,
    source_wallet_id    uuid references sm_wallets(id),
    symbol              text not null,                  -- OKX symbol e.g. "BTC/USDT:USDT"
    side                text not null check (side in ('long', 'short')),
    size                numeric(30, 10) not null,
    entry_price         numeric(30, 10) not null,
    exit_price          numeric(30, 10),
    pnl                 numeric(30, 10),
    signal_latency_ms   int,                            -- HL fill → 本端偵測
    opened_at           timestamptz not null,
    closed_at           timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists idx_sm_paper_trades_wallet
    on sm_paper_trades (source_wallet_id, opened_at desc);
create index if not exists idx_sm_paper_trades_open
    on sm_paper_trades (closed_at) where closed_at is null;

-- ----------------------------------------------------------------
-- 5) live_trades — 實盤交易紀錄
-- ----------------------------------------------------------------
create table if not exists sm_live_trades (
    id                  bigserial primary key,
    source_wallet_id    uuid references sm_wallets(id),
    okx_order_id        text unique,
    symbol              text not null,
    side                text not null check (side in ('long', 'short')),
    size                numeric(30, 10) not null,
    entry_price         numeric(30, 10),
    exit_price          numeric(30, 10),
    pnl                 numeric(30, 10),
    signal_latency_ms   int,
    guard_decisions     jsonb,                          -- 所有 guards 的 judge 紀錄
    opened_at           timestamptz not null,
    closed_at           timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists idx_sm_live_trades_wallet
    on sm_live_trades (source_wallet_id, opened_at desc);
create index if not exists idx_sm_live_trades_open
    on sm_live_trades (closed_at) where closed_at is null;

-- ----------------------------------------------------------------
-- updated_at trigger
-- ----------------------------------------------------------------
create or replace function sm_set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_sm_wallets_updated_at on sm_wallets;
create trigger trg_sm_wallets_updated_at
    before update on sm_wallets
    for each row execute function sm_set_updated_at();

-- ----------------------------------------------------------------
-- RLS:Phase 5 上線前才啟用,此處只建骨架
-- ----------------------------------------------------------------
-- alter table sm_wallets enable row level security;
-- alter table sm_wallet_trades enable row level security;
-- alter table sm_rankings enable row level security;
-- alter table sm_paper_trades enable row level security;
-- alter table sm_live_trades enable row level security;
