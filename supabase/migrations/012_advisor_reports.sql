-- Advisor Reports — 台股投顧報告持久化存儲
-- 用於歷史比對、AI 學習、信心引擎整合

CREATE TABLE IF NOT EXISTS advisor_reports (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source TEXT,                        -- 投顧/券商名稱
    report_date DATE,                   -- 報告日期
    market_view TEXT,                   -- 'bullish' / 'bearish' / 'neutral'
    summary TEXT,                       -- 核心觀點摘要
    key_points JSONB DEFAULT '[]',      -- 重點清單
    stock_picks JSONB DEFAULT '[]',     -- 推薦個股
    sector_views JSONB DEFAULT '[]',    -- 產業觀點
    risk_warnings JSONB DEFAULT '[]',   -- 風險提示
    sentiment_score NUMERIC,            -- 0-1 情緒分數
    cross_reference JSONB DEFAULT '{}', -- 與系統信號交叉比對結果
    raw_text TEXT,                      -- 原始報告文字
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_advisor_reports_date ON advisor_reports(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_advisor_reports_source ON advisor_reports(source);
CREATE INDEX IF NOT EXISTS idx_advisor_reports_view ON advisor_reports(market_view);

ALTER TABLE advisor_reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for service role" ON advisor_reports
    FOR ALL USING (true) WITH CHECK (true);
