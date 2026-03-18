CREATE TABLE indicators (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    indicator_type TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(instrument_id, ts, interval, indicator_type)
);

CREATE INDEX idx_indicators_lookup ON indicators(instrument_id, interval, indicator_type, ts DESC);
