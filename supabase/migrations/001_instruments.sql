CREATE TABLE instruments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    market TEXT NOT NULL CHECK (market IN ('TW', 'US', 'CRYPTO')),
    symbol TEXT NOT NULL,
    name_zh TEXT NOT NULL,
    name_en TEXT,
    exchange TEXT,
    asset_type TEXT CHECK (asset_type IN ('stock', 'etf', 'crypto')),
    is_active BOOLEAN DEFAULT true,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(market, symbol)
);

CREATE INDEX idx_instruments_market ON instruments(market);
CREATE INDEX idx_instruments_symbol ON instruments(symbol);
