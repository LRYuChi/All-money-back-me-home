-- Trade Log — 持久化交易日誌（所有策略統一寫入）
-- 用於回測分析、績效追蹤、AI 學習

CREATE TABLE IF NOT EXISTS trade_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    event TEXT NOT NULL,               -- 'ENTRY' / 'EXIT'
    strategy TEXT NOT NULL,            -- 'SMCTrend' / 'SupertrendStrategy' / 'BBSqueeze'
    pair TEXT NOT NULL,                -- 'BTC/USDT:USDT'
    side TEXT NOT NULL,                -- 'long' / 'short'

    -- Entry fields
    entry_price NUMERIC,
    stake_usd NUMERIC,
    leverage NUMERIC,
    confidence NUMERIC,
    regime TEXT,                        -- 'TRENDING_BULL' / 'RANGING' / etc.
    entry_reasons JSONB DEFAULT '{}',  -- {"htf_trend":1, "in_ob":true, "grade":"A", ...}
    indicators JSONB DEFAULT '{}',     -- {"atr":150, "adx":22, "funding_rate":0.001, ...}

    -- Exit fields (NULL for ENTRY records)
    exit_price NUMERIC,
    exit_reason TEXT,                   -- 'stoploss' / 'take_profit_3R' / 'choch_reversal' / ...
    pnl_pct NUMERIC,
    pnl_usd NUMERIC,
    duration_min NUMERIC,
    r_multiple NUMERIC,

    ts TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indices for common queries
CREATE INDEX IF NOT EXISTS idx_trade_log_strategy ON trade_log(strategy);
CREATE INDEX IF NOT EXISTS idx_trade_log_pair ON trade_log(pair);
CREATE INDEX IF NOT EXISTS idx_trade_log_ts ON trade_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trade_log_event ON trade_log(event);
CREATE INDEX IF NOT EXISTS idx_trade_log_side ON trade_log(side);

-- RLS
ALTER TABLE trade_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON trade_log
    FOR ALL USING (true) WITH CHECK (true);
