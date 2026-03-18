CREATE TABLE ohlcv (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume NUMERIC(30,4) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(instrument_id, ts, interval)
);

CREATE INDEX idx_ohlcv_lookup ON ohlcv(instrument_id, interval, ts DESC);
