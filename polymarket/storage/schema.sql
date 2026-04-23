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

-- ============================================================================
-- Phase 1.5+: 錢包畫像（Wallet Profile）— 多維度行為特徵掃描器輸出
-- ============================================================================
-- 此表與 whale_stats 的關係：
--   - whale_stats 是 Phase 1 的當前快照（每錢包一筆，UPSERT 覆蓋）
--   - wallet_profiles 是 Phase 1.5+ 的時序紀錄（每次掃描 append 一筆）
--   - 兩者並存，由 services.wallet_profile_service 統一對外提供讀取介面
--
-- 設計原則：
--   1. Append-only：永不覆蓋歷史紀錄，scanner 每跑一次就 INSERT 新列
--   2. Versioned：scanner_version 記錄當時的計算邏輯版本，禁止 cross-version 直接比較
--   3. JSON-extensible：features/archetypes/risk_flags 用 JSON 儲存，schema 演進無需 migration
--   4. Time-indexable：scanned_at 為主要時間索引，方便未來按月分區或冷資料歸檔
-- ============================================================================

CREATE TABLE IF NOT EXISTS wallet_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address      TEXT NOT NULL,
    scanner_version     TEXT NOT NULL,              -- e.g. "1.5a.0"
    scanned_at          TEXT NOT NULL,              -- ISO 8601 UTC
    -- 第二階段：Coarse filter result
    passed_coarse_filter INTEGER NOT NULL DEFAULT 1,
    coarse_filter_reasons TEXT,                     -- JSON array of failure reasons
    -- 第三階段：Core stats（這幾欄為了快速查詢拆出來，不放 JSON）
    trade_count_90d     INTEGER,
    resolved_count      INTEGER,
    cumulative_pnl      REAL,
    avg_trade_size      REAL,
    win_rate            REAL,
    -- 第三階段：Features（多家族特徵全部塞 JSON，schema 自由演進）
    -- 結構: { "feature_name": { "value": ..., "confidence": "ok|low_samples|unknown", ... }, ... }
    features_json       TEXT,
    -- 第四階段：分類
    tier                TEXT,                       -- A | B | C | volatile | excluded
    archetypes_json     TEXT,                       -- JSON array, multi-label, e.g. ["selective", "political_expert"]
    risk_flags_json     TEXT,                       -- JSON array, e.g. ["concentration_high", "loss_loading"]
    -- 元資料
    sample_size_warning INTEGER NOT NULL DEFAULT 0, -- 1 = 整體樣本不足，需在 UI 上明示
    raw_features_json   TEXT                        -- 完整中間計算結果，供未來歸因 / 重新評估
);

-- 複合索引：查詢「某錢包的歷史 profile」是常見模式
CREATE INDEX IF NOT EXISTS idx_wp_wallet_time ON wallet_profiles(wallet_address, scanned_at DESC);
-- 時間索引：未來分區與冷資料歸檔
CREATE INDEX IF NOT EXISTS idx_wp_scanned ON wallet_profiles(scanned_at);
-- 版本索引：方便比較不同 scanner 版本的差異
CREATE INDEX IF NOT EXISTS idx_wp_version_wallet ON wallet_profiles(scanner_version, wallet_address);
-- Tier 索引：A/B/C 篩選查詢加速
CREATE INDEX IF NOT EXISTS idx_wp_tier ON wallet_profiles(tier);

-- ============================================================================
-- Phase 1.5b+: 跟單業務邏輯 (Followers)
-- ============================================================================
-- 所有 follower 的決策完整紀錄（含 skip/veto，供未來歸因）
CREATE TABLE IF NOT EXISTS follower_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_name       TEXT NOT NULL,
    follower_version    TEXT NOT NULL,
    decided_at          TEXT NOT NULL,
    -- 來源 alert (FK 形式但 SQLite 不強制)
    source_wallet       TEXT NOT NULL,
    source_tx_hash      TEXT NOT NULL,
    source_event_index  INTEGER NOT NULL,
    source_tier         TEXT,
    -- 決策
    decision            TEXT NOT NULL,   -- 'follow' | 'skip' | 'veto'
    reason              TEXT,
    -- if decision='follow'
    proposed_stake_pct  REAL,
    proposed_size_usdc  REAL,
    -- 若 follow 成功寫入 paper_trades，對應 id
    paper_trade_id      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fd_decided_at ON follower_decisions(decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_fd_follower_decision ON follower_decisions(follower_name, decision);
CREATE INDEX IF NOT EXISTS idx_fd_source ON follower_decisions(source_wallet, source_tx_hash);

-- 紙上跟單帳本 (純模擬, 絕不執行真實下單)
CREATE TABLE IF NOT EXISTS paper_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_name   TEXT NOT NULL,
    -- 來源鯨魚
    source_wallet   TEXT NOT NULL,
    source_tier     TEXT,
    -- 市場
    condition_id    TEXT NOT NULL,
    token_id        TEXT,
    market_question TEXT,
    market_category TEXT,
    outcome         TEXT,
    side            TEXT NOT NULL,       -- 'BUY' | 'SELL'
    -- 進場
    entry_price     REAL NOT NULL,
    entry_size      REAL NOT NULL,       -- 在 outcome 代幣的數量
    entry_notional  REAL NOT NULL,       -- USDC
    entry_time      TEXT NOT NULL,
    -- 退場 (NULL until 結算)
    exit_price      REAL,
    exit_size       REAL,
    exit_notional   REAL,
    exit_time       TEXT,
    exit_reason     TEXT,                 -- 'market_resolved_win' | 'market_resolved_loss' | 'timeout_90d'
    -- 結果
    realized_pnl        REAL,
    realized_pnl_pct    REAL,
    status          TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'closed'
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_pt_follower_status ON paper_trades(follower_name, status);
CREATE INDEX IF NOT EXISTS idx_pt_source_wallet ON paper_trades(source_wallet);
CREATE INDEX IF NOT EXISTS idx_pt_condition ON paper_trades(condition_id);
