-- ============================================================
-- Row Level Security Policies
-- ============================================================

-- -------- instruments: public read, no public write ----------
ALTER TABLE instruments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "instruments_select"
    ON instruments FOR SELECT
    USING (true);

-- -------- ohlcv: public read, no public write ----------------
ALTER TABLE ohlcv ENABLE ROW LEVEL SECURITY;

CREATE POLICY "ohlcv_select"
    ON ohlcv FOR SELECT
    USING (true);

-- -------- indicators: public read, no public write -----------
ALTER TABLE indicators ENABLE ROW LEVEL SECURITY;

CREATE POLICY "indicators_select"
    ON indicators FOR SELECT
    USING (true);

-- -------- analysis_snapshots: public read, no public write ---
ALTER TABLE analysis_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "analysis_snapshots_select"
    ON analysis_snapshots FOR SELECT
    USING (true);

-- -------- portfolios: own CRUD + public read -----------------
ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;

CREATE POLICY "portfolios_select_own"
    ON portfolios FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "portfolios_select_public"
    ON portfolios FOR SELECT
    USING (is_public = true);

CREATE POLICY "portfolios_insert"
    ON portfolios FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "portfolios_update"
    ON portfolios FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "portfolios_delete"
    ON portfolios FOR DELETE
    USING (auth.uid() = user_id);

-- -------- holdings: CRUD within own portfolios ---------------
ALTER TABLE holdings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "holdings_select"
    ON holdings FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM portfolios
            WHERE portfolios.id = holdings.portfolio_id
              AND portfolios.user_id = auth.uid()
        )
    );

CREATE POLICY "holdings_insert"
    ON holdings FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM portfolios
            WHERE portfolios.id = holdings.portfolio_id
              AND portfolios.user_id = auth.uid()
        )
    );

CREATE POLICY "holdings_update"
    ON holdings FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM portfolios
            WHERE portfolios.id = holdings.portfolio_id
              AND portfolios.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM portfolios
            WHERE portfolios.id = holdings.portfolio_id
              AND portfolios.user_id = auth.uid()
        )
    );

CREATE POLICY "holdings_delete"
    ON holdings FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM portfolios
            WHERE portfolios.id = holdings.portfolio_id
              AND portfolios.user_id = auth.uid()
        )
    );

-- -------- watchlists: own CRUD -------------------------------
ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;

CREATE POLICY "watchlists_select"
    ON watchlists FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "watchlists_insert"
    ON watchlists FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "watchlists_update"
    ON watchlists FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "watchlists_delete"
    ON watchlists FOR DELETE
    USING (auth.uid() = user_id);

-- -------- watchlist_items: CRUD within own watchlists --------
ALTER TABLE watchlist_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY "watchlist_items_select"
    ON watchlist_items FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM watchlists
            WHERE watchlists.id = watchlist_items.watchlist_id
              AND watchlists.user_id = auth.uid()
        )
    );

CREATE POLICY "watchlist_items_insert"
    ON watchlist_items FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM watchlists
            WHERE watchlists.id = watchlist_items.watchlist_id
              AND watchlists.user_id = auth.uid()
        )
    );

CREATE POLICY "watchlist_items_delete"
    ON watchlist_items FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM watchlists
            WHERE watchlists.id = watchlist_items.watchlist_id
              AND watchlists.user_id = auth.uid()
        )
    );

-- -------- alerts: own CRUD -----------------------------------
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "alerts_select"
    ON alerts FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "alerts_insert"
    ON alerts FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "alerts_update"
    ON alerts FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "alerts_delete"
    ON alerts FOR DELETE
    USING (auth.uid() = user_id);
