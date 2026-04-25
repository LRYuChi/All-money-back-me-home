-- ================================================================
-- Strategy enable/disable audit trail — round 25
-- Migration: 021_strategy_enable_history.sql
-- 見 docs/AI_MULTIMARKET_ROADMAP.md Phase G (G9 ConsecutiveLossDays trigger
-- writes here when it disables a strategy; humans flipping back are also
-- recorded so we have a full audit of every state change.)
-- ================================================================

-- Mutation log: every flip of strategies.enabled, with reason + actor.
-- Append-only; never UPDATE or DELETE rows here.
create table if not exists strategy_enable_history (
    id              bigserial primary key,
    strategy_id     text not null
                    references strategies(id) on delete cascade,
    enabled         boolean not null,                -- new state after flip
    reason          text,                            -- free-form, e.g.
                                                     -- "G9: 3-day loss streak ([-100,-50,-25])"
    actor           text,                            -- who/what flipped it:
                                                     -- "guard:consecutive_loss_cb"
                                                     -- "human:yuchi"
                                                     -- "cli:strategy admin"
    created_at      timestamptz not null default now()
);

create index if not exists idx_strategy_enable_history_strategy_created
    on strategy_enable_history (strategy_id, created_at desc);
create index if not exists idx_strategy_enable_history_created
    on strategy_enable_history (created_at desc);
