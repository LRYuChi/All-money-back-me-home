-- ================================================================
-- Backtest snapshots — every backtest run is captured for reproducibility
-- Migration: 017_backtest_snapshots.sql
-- 見 docs/QUANTDINGER_REFERENCE_PLAN.md P1-4 / docs/AI_MULTIMARKET_ROADMAP.md Phase B
-- ================================================================

-- ----------------------------------------------------------------
-- backtest_snapshots — every gate decision / strategy backtest is
-- captured as a complete, replayable record. 6 months ago you should
-- be able to query "why did P3 gate pass on 2026-04-19?" and get back
-- exact code commit + config + data window + result.
-- ----------------------------------------------------------------
create table if not exists backtest_snapshots (
    id              bigserial primary key,

    -- What kind of backtest? Examples:
    --   'smart_money_p3_gate'         — P3 walk-forward gate
    --   'strategy:<strategy_id>'      — Phase E strategy DSL backtest
    --   'kronos_finetune_eval'        — Phase C Kronos finetune eval
    kind            text not null,

    -- Git commit at the time of the run. NULL if running outside git
    -- (e.g. one-off notebook). Short hash + a flag for clean/dirty.
    git_commit      text,
    git_dirty       boolean,

    -- Full snapshot of the inputs:
    --   ranking_config: metric weights + thresholds
    --   strategy_config: DSL yaml content
    --   model_config: kronos hyperparams
    config          jsonb not null,

    -- Walk-forward / multi-cutoff backtests record their cutoffs;
    -- single-window backtests use {start, end}.
    cutoffs         jsonb,
    data_window     jsonb,                                 -- {from, to}

    -- For deterministic reruns. NULL if RNG was not seeded.
    rng_seed        bigint,

    -- Full result payload. Schema varies by kind; consumers must look at
    -- `kind` first. Keeping JSONB rather than per-kind columns lets us
    -- evolve report shape without migrations.
    report          jsonb not null,

    -- Gate decision (PASS/FAIL). NULL if the run isn't a gate (e.g.
    -- exploratory backtest).
    decision_pass   boolean,
    decision_reason text,

    -- Cardinality summaries — enables fast dashboard queries without
    -- pulling the full report blob.
    n_trades        integer,
    median_pnl_pct  numeric(8,4),
    max_drawdown    numeric(8,4),

    created_at      timestamptz not null default now()
);

create index if not exists idx_backtest_snapshots_kind_created
    on backtest_snapshots (kind, created_at desc);
create index if not exists idx_backtest_snapshots_decision
    on backtest_snapshots (kind, decision_pass, created_at desc);
