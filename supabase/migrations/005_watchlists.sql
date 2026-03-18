CREATE TABLE watchlists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '預設觀察清單',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE watchlist_items (
    watchlist_id UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    instrument_id UUID NOT NULL REFERENCES instruments(id),
    added_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (watchlist_id, instrument_id)
);
