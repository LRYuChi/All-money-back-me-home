-- Market structure analysis results (Layer 1 output).
--
-- Stores the classified market state (trending/ranging), CHoCH detection,
-- confidence score, and raw swing-point data for each instrument+timeframe+ts
-- combination.

CREATE TABLE market_structure (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    timeframe TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('TRENDING_UP', 'TRENDING_DOWN', 'RANGING')),
    choch_detected BOOLEAN DEFAULT false,
    confidence NUMERIC(5,4),
    swing_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(instrument_id, timeframe, ts)
);

CREATE INDEX idx_market_structure_lookup
    ON market_structure(instrument_id, timeframe, ts DESC);
