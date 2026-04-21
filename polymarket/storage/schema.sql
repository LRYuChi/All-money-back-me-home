-- Polymarket storage schema
--
-- 相容性：SQLite（Phase 0 開發）與 PostgreSQL/Supabase（Phase 1+）。
-- 時間欄位統一用 TEXT ISO-8601；主鍵採 natural key（condition_id、token_id）。
-- 原則：寫入不可變（immutability），同一條 natural key 的新資料以 UPSERT 保留最新快照，
-- 但 order_book_snapshot 與 trade 都帶 fetched_at 允許時間序列保存。

-- 市場元資料（最新快照；以 condition_id 為主鍵）
CREATE TABLE IF NOT EXISTS markets (
    condition_id   TEXT PRIMARY KEY,
    question       TEXT NOT NULL,
    market_slug    TEXT,
    category       TEXT,
    end_date_iso   TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    closed         INTEGER NOT NULL DEFAULT 0,
    minimum_order_size REAL,
    minimum_tick_size  REAL,
    maker_base_fee REAL,
    taker_base_fee REAL,
    raw_json       TEXT,
    fetched_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active, closed);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_end_date ON markets(end_date_iso);

-- Tokens（二元市場為 Yes/No，多選項市場為候選人/州名等任意字串）
CREATE TABLE IF NOT EXISTS tokens (
    token_id      TEXT PRIMARY KEY,
    condition_id  TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    price         REAL,
    winner        INTEGER,
    fetched_at    TEXT NOT NULL,
    FOREIGN KEY (condition_id) REFERENCES markets(condition_id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_condition ON tokens(condition_id);

-- Order book 快照（時間序列，不覆蓋）
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id  TEXT NOT NULL,
    token_id      TEXT NOT NULL,
    hash          TEXT,
    best_bid      REAL,
    best_ask      REAL,
    mid_price     REAL,
    spread        REAL,
    bid_depth_top10 REAL,  -- top 10 檔位的總 size
    ask_depth_top10 REAL,
    raw_json      TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_book_token_time ON order_book_snapshots(token_id, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_book_market_time ON order_book_snapshots(condition_id, snapshot_at);

-- 成交紀錄（以 trade id 為去重 key）
CREATE TABLE IF NOT EXISTS trades (
    id            TEXT PRIMARY KEY,
    condition_id  TEXT NOT NULL,
    token_id      TEXT,
    price         REAL NOT NULL,
    size          REAL NOT NULL,
    notional      REAL NOT NULL,
    side          TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    status        TEXT,
    maker_address TEXT,
    taker_address TEXT,
    match_time    TEXT NOT NULL,
    raw_json      TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_market_time ON trades(condition_id, match_time);
CREATE INDEX IF NOT EXISTS idx_trades_maker ON trades(maker_address);
CREATE INDEX IF NOT EXISTS idx_trades_taker ON trades(taker_address);
CREATE INDEX IF NOT EXISTS idx_trades_match_time ON trades(match_time);

-- ============================================================================
-- Phase 1: 鯨魚情報引擎
-- ============================================================================

-- 鯨魚錢包統計快照（每次 pipeline 更新覆蓋）
CREATE TABLE IF NOT EXISTS whale_stats (
    wallet_address      TEXT PRIMARY KEY,
    tier                TEXT NOT NULL,   -- 'A' | 'B' | 'C' | 'volatile' | 'excluded'
    trade_count_90d     INTEGER NOT NULL DEFAULT 0,
    win_rate            REAL NOT NULL DEFAULT 0,
    cumulative_pnl      REAL NOT NULL DEFAULT 0,
    avg_trade_size      REAL NOT NULL DEFAULT 0,
    segment_win_rates   TEXT,           -- JSON: [0.6, 0.55, 0.62] 三段 30 天
    stability_pass      INTEGER NOT NULL DEFAULT 0,
    resolved_count      INTEGER NOT NULL DEFAULT 0,  -- 已結算倉位數（用於 win_rate 分母）
    last_trade_at       TEXT,
    last_computed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_whale_tier ON whale_stats(tier);
CREATE INDEX IF NOT EXISTS idx_whale_computed ON whale_stats(last_computed_at);

-- 鯨魚層級變動歷史（append-only）
CREATE TABLE IF NOT EXISTS whale_tier_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address      TEXT NOT NULL,
    from_tier           TEXT,            -- NULL = 首次登錄
    to_tier             TEXT NOT NULL,
    changed_at          TEXT NOT NULL,
    reason              TEXT             -- 'initial' | 'promoted' | 'demoted' | 'stability_fail'
);

CREATE INDEX IF NOT EXISTS idx_tier_history_wallet ON whale_tier_history(wallet_address);
CREATE INDEX IF NOT EXISTS idx_tier_history_time ON whale_tier_history(changed_at);

-- 鯨魚交易推播記錄（idempotency on wallet + tx_hash + event_index）
CREATE TABLE IF NOT EXISTS whale_trade_alerts (
    wallet_address      TEXT NOT NULL,
    tx_hash             TEXT NOT NULL,
    event_index         INTEGER NOT NULL,
    tier                TEXT NOT NULL,
    condition_id        TEXT,
    market_question     TEXT,
    side                TEXT,
    outcome             TEXT,
    size                REAL,
    price               REAL,
    notional            REAL,
    match_time          TEXT,
    alerted_at          TEXT NOT NULL,
    telegram_sent       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (wallet_address, tx_hash, event_index)
);

CREATE INDEX IF NOT EXISTS idx_alerts_wallet_time ON whale_trade_alerts(wallet_address, match_time);
CREATE INDEX IF NOT EXISTS idx_alerts_tier_time ON whale_trade_alerts(tier, match_time);
