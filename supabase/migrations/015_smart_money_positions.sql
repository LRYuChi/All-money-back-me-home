-- ================================================================
-- Smart Money 跟單系統 — P4b: wallet position state + skipped signals
-- Migration: 015_smart_money_positions.sql
-- 見 docs/SMART_MONEY_SIGNAL_EXECUTION.md §3, §9
-- ================================================================

-- ----------------------------------------------------------------
-- sm_wallet_positions — classifier 狀態機的持久層
-- Per (wallet, symbol): 最新已知持倉方向與數量。
-- daemon 重啟後從此表復原,避免每次冷啟動都靠 REST 對帳.
-- ----------------------------------------------------------------
create table if not exists sm_wallet_positions (
    wallet_id        uuid not null references sm_wallets(id) on delete cascade,
    symbol           text not null,
    side             text not null check (side in ('long', 'short', 'flat')),
    size             numeric(30, 10) not null,
    avg_entry_px     numeric(30, 10),                      -- null when side='flat'
    last_updated_ts  timestamptz not null,
    updated_at       timestamptz not null default now(),
    primary key (wallet_id, symbol)
);

create index if not exists idx_sm_wallet_positions_symbol
    on sm_wallet_positions (symbol);

-- ----------------------------------------------------------------
-- sm_skipped_signals — 被 classifier / mapper / guards 跳過的訊號
-- 作為可觀測性 ground truth:「我們漏掉了哪些訊號,為什麼?」
-- ----------------------------------------------------------------
create table if not exists sm_skipped_signals (
    id                  bigserial primary key,
    wallet_id           uuid references sm_wallets(id) on delete cascade,
    wallet_address      text not null,                     -- denorm: 方便查詢無需 join
    symbol_hl           text not null,
    reason              text not null,                     -- 見 docs §6.1 reason 列表
    signal_latency_ms   integer,
    direction_raw       text,                              -- HL dir 欄位原文,供除錯
    hl_trade_id         bigint,
    detail              jsonb,                             -- 結構化附加資訊 (e.g. prev_state, observed_state)
    created_at          timestamptz not null default now()
);

create index if not exists idx_sm_skipped_signals_created
    on sm_skipped_signals (created_at desc);
create index if not exists idx_sm_skipped_signals_reason
    on sm_skipped_signals (reason);
create index if not exists idx_sm_skipped_signals_wallet
    on sm_skipped_signals (wallet_id, created_at desc);

-- ----------------------------------------------------------------
-- sm_paper_trades extensions (P4c)
-- Preserve backward compat: all columns nullable, idempotent.
-- ----------------------------------------------------------------
alter table sm_paper_trades
    add column if not exists signal_mode      text,     -- 'independent' | 'aggregated'
    add column if not exists source_wallets   uuid[],   -- for aggregated multi-source trades
    add column if not exists exit_reason      text,     -- 'whale_close' | 'reverse' | 'sl_hit' | ...
    add column if not exists network_latency_ms   integer,
    add column if not exists processing_latency_ms integer;

create index if not exists idx_sm_paper_trades_opened
    on sm_paper_trades (opened_at desc);
create index if not exists idx_sm_paper_trades_open
    on sm_paper_trades (source_wallet_id, symbol) where closed_at is null;
