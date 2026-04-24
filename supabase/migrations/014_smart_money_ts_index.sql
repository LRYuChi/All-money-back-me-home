-- 014_smart_money_ts_index.sql
--
-- 加 sm_wallet_trades.ts 獨立 B-tree index。
--
-- Why:
--   013 建立的 (wallet_id, ts desc) / (symbol, ts desc) 是複合 index，
--   query 若沒帶 wallet_id 或 symbol filter（例如「近 24h 全部交易」
--   或「最新 ts 是哪一筆」的 freshness probe）就會 seq-scan 整張
--   3M+ row table，觸發 Supabase statement timeout (57014).
--
--   實際案例：
--     SELECT * FROM sm_wallet_trades ORDER BY ts DESC LIMIT 1;
--       -- 3.2M rows seq-scan → 57014 timeout
--     SELECT COUNT(*) FROM sm_wallet_trades WHERE ts > now() - '1 day';
--       -- 同上
--
-- 補這個 index 後，data-health liveness probe 會是 index-only scan，<50ms。
--
-- Lock cost: CREATE INDEX on 3.2M rows 估需 30-60s 持 ACCESS EXCLUSIVE。
-- 對現行系統而言可接受（Smart Money scanner 非 real-time）。
-- 若未來流量更大，改用 CREATE INDEX CONCURRENTLY（不能在交易中跑，
-- 需拆獨立 migration）。

create index if not exists idx_sm_wallet_trades_ts
    on sm_wallet_trades (ts desc);
