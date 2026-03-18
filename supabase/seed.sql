-- ============================================================
-- Seed: Popular Instruments
-- ============================================================

INSERT INTO instruments (market, symbol, name_zh, name_en, exchange, asset_type) VALUES
    -- Taiwan Stocks
    ('TW', '2330', '台積電',   'TSMC',                    'TWSE', 'stock'),
    ('TW', '2317', '鴻海',     'Hon Hai Precision',       'TWSE', 'stock'),
    ('TW', '2454', '聯發科',   'MediaTek',                'TWSE', 'stock'),
    ('TW', '2881', '富邦金',   'Fubon Financial',         'TWSE', 'stock'),
    ('TW', '2882', '國泰金',   'Cathay Financial',        'TWSE', 'stock'),
    ('TW', '2303', '聯電',     'UMC',                     'TWSE', 'stock'),
    ('TW', '3711', '日月光',   'ASE Technology',          'TWSE', 'stock'),
    ('TW', '2412', '中華電',   'Chunghwa Telecom',        'TWSE', 'stock'),
    ('TW', '1301', '台塑',     'Formosa Plastics',        'TWSE', 'stock'),
    ('TW', '2308', '台達電',   'Delta Electronics',       'TWSE', 'stock'),

    -- US Stocks
    ('US', 'AAPL',  '蘋果',     'Apple',                  'NASDAQ', 'stock'),
    ('US', 'MSFT',  '微軟',     'Microsoft',              'NASDAQ', 'stock'),
    ('US', 'GOOGL', 'Google',   'Alphabet',               'NASDAQ', 'stock'),
    ('US', 'AMZN',  '亞馬遜',   'Amazon',                 'NASDAQ', 'stock'),
    ('US', 'NVDA',  '輝達',     'NVIDIA',                 'NASDAQ', 'stock'),
    ('US', 'TSLA',  '特斯拉',   'Tesla',                  'NASDAQ', 'stock'),
    ('US', 'META',  'Meta',     'Meta Platforms',          'NASDAQ', 'stock'),
    ('US', 'TSM',   '台積電ADR', 'Taiwan Semiconductor ADR','NYSE',  'stock'),
    ('US', 'AMD',   '超微',     'Advanced Micro Devices',  'NASDAQ', 'stock'),
    ('US', 'NFLX',  'Netflix',  'Netflix',                 'NASDAQ', 'stock'),

    -- Crypto
    ('CRYPTO', 'BTC/USDT', '比特幣',   'Bitcoin',      NULL, 'crypto'),
    ('CRYPTO', 'ETH/USDT', '以太幣',   'Ethereum',     NULL, 'crypto'),
    ('CRYPTO', 'SOL/USDT', 'Solana',   'Solana',       NULL, 'crypto'),
    ('CRYPTO', 'BNB/USDT', '幣安幣',   'BNB',          NULL, 'crypto'),
    ('CRYPTO', 'XRP/USDT', '瑞波幣',   'XRP',          NULL, 'crypto');
