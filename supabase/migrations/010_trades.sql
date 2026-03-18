-- ============================================================
-- Paper Trades & Backtest Results
-- ============================================================

-- Paper trade positions (open + closed)
CREATE TABLE paper_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID REFERENCES instruments(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    strategy TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')) DEFAULT 'open',

    -- Entry
    entry_price NUMERIC(20,8) NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    stop_loss NUMERIC(20,8),
    take_profit_levels JSONB DEFAULT '[]',
    position_size_usd NUMERIC(20,4) NOT NULL,
    leverage NUMERIC(5,2) DEFAULT 1.0,
    confidence NUMERIC(5,4) DEFAULT 0.0,
    reason TEXT DEFAULT '',

    -- Exit (populated on close)
    exit_price NUMERIC(20,8),
    exit_time TIMESTAMPTZ,
    exit_reason TEXT,
    pnl_usd NUMERIC(20,4),
    pnl_pct NUMERIC(10,4),
    commission_paid NUMERIC(20,4) DEFAULT 0,
    duration_bars INTEGER,
    r_multiple NUMERIC(10,4),

    -- Source: 'scanner' for hourly_scanner, 'backtest' for backtest engine
    source TEXT NOT NULL DEFAULT 'scanner',

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_paper_trades_symbol ON paper_trades(symbol, status);
CREATE INDEX idx_paper_trades_status ON paper_trades(status);
CREATE INDEX idx_paper_trades_source ON paper_trades(source);
CREATE INDEX idx_paper_trades_entry_time ON paper_trades(entry_time DESC);

-- Capital snapshots (equity curve for paper trading)
CREATE TABLE capital_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'scanner',
    capital NUMERIC(20,4) NOT NULL,
    equity NUMERIC(20,4) NOT NULL,
    unrealized_pnl NUMERIC(20,4) DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    ts TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_capital_snapshots_ts ON capital_snapshots(source, ts DESC);

-- Backtest run results
CREATE TABLE backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    initial_capital NUMERIC(20,4) NOT NULL,
    commission_rate NUMERIC(10,6) DEFAULT 0.001,

    -- Results
    total_trades INTEGER DEFAULT 0,
    win_rate NUMERIC(5,4) DEFAULT 0,
    profit_factor NUMERIC(10,4) DEFAULT 0,
    sharpe_ratio NUMERIC(10,4) DEFAULT 0,
    max_drawdown NUMERIC(10,4) DEFAULT 0,
    total_return NUMERIC(10,4) DEFAULT 0,
    calmar_ratio NUMERIC(10,4) DEFAULT 0,
    avg_r_multiple NUMERIC(10,4) DEFAULT 0,
    avg_trade_duration_bars NUMERIC(10,2) DEFAULT 0,

    -- Data range
    data_start TIMESTAMPTZ,
    data_end TIMESTAMPTZ,
    total_bars INTEGER DEFAULT 0,

    -- Full result as JSON (equity_curve, trades, walk_forward_folds)
    result_json JSONB DEFAULT '{}',

    -- Walk-forward
    is_walk_forward BOOLEAN DEFAULT false,
    walk_forward_summary JSONB,

    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_backtest_runs_strategy ON backtest_runs(strategy, symbol, created_at DESC);

-- ============================================================
-- RLS Policies for new tables
-- ============================================================

-- paper_trades: public read (single user system for now)
ALTER TABLE paper_trades ENABLE ROW LEVEL SECURITY;
CREATE POLICY "paper_trades_select" ON paper_trades FOR SELECT USING (true);
CREATE POLICY "paper_trades_insert" ON paper_trades FOR INSERT WITH CHECK (true);
CREATE POLICY "paper_trades_update" ON paper_trades FOR UPDATE USING (true);

-- capital_snapshots: public read
ALTER TABLE capital_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "capital_snapshots_select" ON capital_snapshots FOR SELECT USING (true);
CREATE POLICY "capital_snapshots_insert" ON capital_snapshots FOR INSERT WITH CHECK (true);

-- backtest_runs: public read
ALTER TABLE backtest_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "backtest_runs_select" ON backtest_runs FOR SELECT USING (true);
CREATE POLICY "backtest_runs_insert" ON backtest_runs FOR INSERT WITH CHECK (true);
