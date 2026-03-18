CREATE TABLE analysis_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    analysis_type TEXT NOT NULL CHECK (analysis_type IN ('technical', 'pattern', 'signal')),
    interval TEXT NOT NULL DEFAULT '1d',
    generated_at TIMESTAMPTZ DEFAULT now(),
    result JSONB NOT NULL,
    summary_zh TEXT
);

CREATE INDEX idx_snapshots_instrument ON analysis_snapshots(instrument_id, analysis_type, generated_at DESC);
