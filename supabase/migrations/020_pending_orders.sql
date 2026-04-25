-- ================================================================
-- L6 Pending Orders middleware — strategy intent → pending_orders → workers
-- Migration: 020_pending_orders.sql
-- 見 docs/QUANTDINGER_REFERENCE_PLAN.md P0-1 / docs/AI_MULTIMARKET_ROADMAP.md §9
-- ================================================================

-- ----------------------------------------------------------------
-- pending_orders — every StrategyIntent is captured here before being
-- dispatched. Decouples evaluator (which fires immediately when its
-- predicates pass) from execution (which can be paused / rate-limited
-- / retried / human-vetoed independently).
--
-- Lifecycle:
--   pending → dispatching → submitted → filled
--                                   └──→ partially_filled
--                                   └──→ rejected   (exchange-side reason)
--   pending → cancelled   (human / CB)
--   pending → expired     (waited too long without dispatch)
--   submitted → cancelled (open order cancelled)
--
-- Idempotency:
--   `client_order_id` is unique (NULL allowed for shadow/notify modes).
--   Live mode generates deterministic id from
--   (strategy_id, symbol, side, intent_ts_ms) so re-submission of the
--   same intent (e.g. on worker restart) doesn't double-open positions.
-- ----------------------------------------------------------------
create table if not exists pending_orders (
    id                    bigserial primary key,
    strategy_id           text not null,
    symbol                text not null,                  -- canonical
    side                  text not null check (side in ('long', 'short')),
    target_notional_usd   numeric(18, 4) not null check (target_notional_usd > 0),
    entry_price_ref       numeric(30, 10),
    stop_loss_pct         numeric(8, 4),
    take_profit_pct       numeric(8, 4),

    -- Execution mode determines which dispatcher worker picks this up
    --   shadow  — paper-trade simulator (writes sm_paper_trades)
    --   paper   — manual UI test orders (writes paper_trades, broader)
    --   live    — real exchange orders (writes live_trades)
    --   notify  — Telegram only, no order at all
    mode                  text not null check (mode in ('shadow','paper','live','notify')),

    -- State machine
    status                text not null default 'pending'
                          check (status in ('pending','dispatching','submitted',
                                            'filled','partially_filled','rejected',
                                            'cancelled','expired')),
    attempts              integer not null default 0,
    last_error            text,

    -- Audit links
    fused_signal_id       bigint references fused_signals(id) on delete set null,
    client_order_id       text unique,                    -- exchange idempotency key

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    dispatched_at         timestamptz,
    completed_at          timestamptz
);

create index if not exists idx_pending_orders_status_created
    on pending_orders (status, created_at) where status = 'pending';
create index if not exists idx_pending_orders_strategy_created
    on pending_orders (strategy_id, created_at desc);
create index if not exists idx_pending_orders_symbol_created
    on pending_orders (symbol, created_at desc);

-- Audit table for status transitions — enables "why was this order
-- cancelled?" replay even after rows have been compacted.
create table if not exists pending_order_events (
    id              bigserial primary key,
    order_id        bigint not null references pending_orders(id) on delete cascade,
    from_status     text,
    to_status       text not null,
    reason          text,
    detail          jsonb,
    created_at      timestamptz not null default now()
);

create index if not exists idx_pending_order_events_order
    on pending_order_events (order_id, created_at);
create index if not exists idx_pending_order_events_created
    on pending_order_events (created_at desc);
