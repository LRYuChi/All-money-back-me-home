# Smart Money 跟單系統 — 架構重構遷移計畫

**起草日期**: 2026-04-19
**狀態**: 🟡 Draft — 等使用者逐行審核
**資金規模**: $1,000 USDT(上線後 2-4 週內逐步到位)
**執行版本**: v1.0

---

## 0. TL;DR

把現有 TA/ML 交易系統(SMCTrend、Supertrend、tw_futures_*、ML 預測)**整組退役**,改為:

> **Hyperliquid 掃鯨魚錢包 → 演算法排名(量化為主 + AI 質性輔助)→ 訊號轉譯 → OKX 永續合約下單**

執行分為 7 個 Phase,每 Phase 獨立可驗收、可回滾。**Phase 3(歷史回測 gate)未通過不得進入 Phase 4。**

---

## 1. 架構總覽

```
┌─────────────────────────────────────────────────────────────────┐
│                      Smart Money System                         │
│                                                                 │
│  [Hyperliquid]      [Ranking Engine]       [Execution]          │
│                                                                 │
│  ws + REST  ──►  硬門檻 filter    ──►   symbol mapper  ──►      │
│  scan 活躍      ──► 量化分數           size 換算                 │
│  錢包           ──► AI 質性 (Phase 6)  guards pipeline           │
│                 ──► 綜合排名           ccxt → OKX                │
│                                                                 │
│  [Shadow Mode] 與 [Live Mode] 可切換 (env: SM_MODE=shadow/live) │
│                                                                 │
│  [Telegram Bot] 每日推送白名單變動 + 訊號 + 執行結果             │
│  [Supabase]     儲存錢包快照、排名歷史、紙上 & 實際 PnL          │
└─────────────────────────────────────────────────────────────────┘
```

### 模組結構(新增)

```
smart_money/
├── __init__.py
├── config.py                 # 設定中心(env var + yaml)
├── scanner/
│   ├── hl_client.py          # Hyperliquid SDK 包裝
│   ├── historical.py         # 歷史資料拉取(背景 job)
│   └── realtime.py           # ws 訂閱(live 階段用)
├── ranking/
│   ├── metrics.py            # Sortino / PF / MDD / martingale 偵測
│   ├── filters.py            # 硬門檻(sample size / 活躍度 / 集中度)
│   ├── scorer.py             # 綜合分數
│   └── ai_layer.py           # Phase 6:AI 質性評估
├── backtest/
│   ├── validator.py          # 防線 A 自我回測
│   └── reporter.py           # 回測結果視覺化
├── shadow/
│   └── simulator.py          # 紙上跟單
├── execution/
│   ├── mapper.py             # HL symbol ↔ OKX symbol + size 換算
│   ├── aggregator.py         # 多錢包訊號合併(加權)
│   ├── order.py              # ccxt OKX 下單
│   └── guard_bridge.py       # 接現有 guards/pipeline.py
├── store/
│   ├── schema.sql            # Supabase migration
│   ├── wallets.py            # 錢包資料 DAO
│   ├── rankings.py           # 排名快照 DAO
│   └── trades.py             # 紙上 + 實際交易 DAO
└── cli/
    ├── scan.py               # python -m smart_money.cli.scan
    ├── rank.py               # python -m smart_money.cli.rank
    ├── backtest.py           # python -m smart_money.cli.backtest
    ├── shadow.py             # python -m smart_money.cli.shadow
    └── trade.py              # python -m smart_money.cli.trade
```

---

## 2. 檔案砍/留/新增矩陣

### 🔴 砍(**延到 Phase 5 cutover 日**才實際刪,避免破壞運行中的 telegram-bot / agent / mcp_server)

| 檔案/目錄 | 原因 |
|---|---|
| `strategies/smc_trend.py`, `smc_trend.json` | TA 主策略,已 3 週無訊號 |
| `strategies/smc_scalp.py` | TA 衍生 |
| `strategies/supertrend.py`, `supertrend_scout.py`, `supertrend_v3.py` | TA 策略 |
| `strategies/bb_squeeze.py`, `volty_expan.py`, `meta_strategy.py` | TA 策略 |
| `strategies/base_mixin.py` | 依附於 Freqtrade strategy 框架 |
| `market_monitor/ml/` 整目錄 | h5 Accuracy 40%,方向錯誤,不值得修 |
| `market_monitor/tw_futures_*.py` × 8 個散檔 | 台指期研究腳本,與新方向無關 |
| `market_monitor/confidence_engine.py` | 原本為 TA/macro 設計,重寫為跟單信心 |
| `market_monitor/tw_advisor.py`, `tw_predictor.py` | 台股顧問,不在新範圍 |
| `market_monitor/crypto_environment.py` | 與跟單無關,可留但低優先 |
| `market_monitor/signals/` | TA 訊號產生 |
| Freqtrade 整個執行層 | 策略框架,與「鏡像跟單」模型不符,改用 ccxt 直連 |
| `apps/web/src/components/dashboard/TwFuturesPanel.tsx`(未 commit) | 台指期 UI |
| `docker-compose.prod.yml` 的 `freqtrade` service | 下架 |

### 🟢 保留/改造

| 檔案/目錄 | 動作 |
|---|---|
| `guards/base.py`, `guards/guards.py`, `guards/pipeline.py` | **保留 + 新增跟單專用 guard**(單錢包最大曝險、訊號延遲門檻、相關性檢查) |
| `trading_log/` | 保留,改造 commit message 格式(加入 source_wallet 欄位) |
| `apps/web` | 保留,頁面改為錢包白名單、排名歷史、跟單 PnL |
| `apps/api` | 保留,router 換成 `smart_money_api` |
| `market_monitor/telegram_bot.py`, `telegram_zh.py` | 保留通知層,改餵訊號內容 |
| `market_monitor/state_store.py` | 保留,狀態管理通用 |
| `market_monitor/health_check.py` | 保留並擴充(加 HL ws 連線檢查) |
| `mcp_server/` | 保留 + 新增 `whale_scan`, `ranking_snapshot` tools |
| `agent/` | 保留架構,decision prompts 改為跟單語境 |
| `supabase/migrations/` | 保留 + 新增 smart_money schema |

### 🆕 新增

見上方「模組結構」— 整個 `smart_money/` 樹狀目錄。

---

## 3. Phase 劃分(嚴格單向,前一 Phase 未通過不得進下一 Phase)

### Phase 0 — 基礎建設 & 清理

**目標**: 為新系統鋪地基,清掉舊垃圾。**不改變 VPS 上運行中的服務**。

**任務**:
- [ ] 建立 feature branch `feat/smart-money-v1`
- [ ] 建立目錄骨架:`smart_money/` 所有空 module(含 `__init__.py`)
- [ ] `pyproject.toml` 加依賴:`hyperliquid-python-sdk`, `ccxt`(已有)、`pydantic-settings`
- [ ] `smart_money/config.py`:集中 env var(HL_API_KEY 可選、OKX keys、LLM endpoint、thresholds)
- [ ] Supabase schema:`wallets`, `wallet_snapshots`, `rankings`, `paper_trades`, `live_trades`(見 §7)
- [ ] 刪除**未提交的** `tw_futures_*.py` / `TwFuturesPanel.tsx` / `supertrend_v3.py`(使用者已批示丟棄)
- [ ] ⚠️ **不**刪除 committed 檔案(confidence_engine.py 等 15 處被 import,會破壞 production);delete 動作統一延到 Phase 5 cutover
- [ ] 更新 `CLAUDE.md`:新增 smart_money 區塊,標記舊系統為 deprecated

**交付物**:
- Git branch 可 checkout,`pip install -e .` 成功
- `python -m smart_money.cli.scan --help` 能印 usage(即使未實作)
- Supabase 新 migration 在 local DB 跑通

**驗收(min/max 標準)**:
- **min**: CI 通過、import 無 error、VPS 服務不受影響(舊系統續跑)
- **max**: 使用者逐行審目錄結構與刪除清單,批准

**時程指引**: 一個工作日內完成(此為地基,不含邏輯)

**回滾**: `git reset --hard` feature branch,VPS 無影響

---

### Phase 1 — Hyperliquid 資料層

**目標**: 能自動拉 Hyperliquid 任意錢包 ≥ 90 天完整交易歷史,入 DB。

**任務**:
- [ ] `smart_money/scanner/hl_client.py`:包 `hyperliquid-python-sdk`,提供:
  - `get_active_wallets(lookback_days=30)` — 列出近 30 天有交易的錢包
  - `get_wallet_trades(address, since, until)` — 單錢包完整交易
  - `get_wallet_state(address)` — 當前持倉
- [ ] `smart_money/scanner/historical.py`:批次抓取 + 分頁 + rate limit + idempotent
- [ ] `smart_money/store/wallets.py`, `store/trades.py`:DAO,upsert by `(address, trade_id)`
- [ ] `cli/scan.py`:
  - `--seed-leaderboard`:從 HL leaderboard 拉 Top 500 錢包當 seed set
  - `--backfill-days N`:每個 seed 回補 N 天
  - `--resume`:斷線續傳

**交付物**:
- DB 內至少 **500 錢包 × ≥ 90 天** 完整歷史
- Query `SELECT count(*) FROM wallet_trades WHERE wallet_id = X` 對得上 HL 官網

**驗收(min/max)**:
- **min**: 資料量達標、抽樣 5 個錢包人工比對 HL 網頁一致
- **max**: 可以任意重跑不產生重複資料(idempotency verified by re-running scanner twice, row count identical)

**依賴**: Phase 0 的 DB schema

**風險**: HL API rate limit(需 backoff);部分錢包歷史 > 1 年資料量爆(需 capped)

**回滾**: DB 可 drop schema 後重建

**時程指引**: 一週工作量(含資料驗證)

---

### Phase 2 — 排名演算法(確定性核心)

**目標**: 給定一個錢包,輸出可解釋、可重現的排名分數。**此階段完全不碰 AI**。

**任務**:
- [ ] `ranking/filters.py`(硬門檻,不通過直接淘汰):
  - `sample_size >= 50` 筆已平倉交易
  - `active_days >= 30`
  - 單幣種集中度 ≤ 80%(避免只賭一個幣)
  - 平均持倉時間 ≥ 5 分鐘(過濾 HFT/bot)
- [ ] `ranking/metrics.py`(6 個量化特徵,單元測試覆蓋):
  - `sortino_ratio(trades)` — 下行風險調整報酬
  - `profit_factor(trades)` — Σwins / |Σlosses|
  - `max_drawdown_recovery(trades)` — 最大 DD 後回補天數
  - `holding_time_cv(trades)` — 持倉時間變異係數(過度一致 = bot)
  - `martingale_penalty(trades)` — 連虧後加倉 pattern 偵測,回傳 0~1 扣分
  - `regime_stability(trades, market_data)` — 多/空/盤整三種 regime 各自 PnL 是否 > 0
- [ ] `ranking/scorer.py`:加權組合,權重在 `config.py` 可調
  ```
  score = 0.25*sortino_norm + 0.20*pf_norm + 0.15*dd_recovery_norm
        + 0.10*(1 - ht_cv_norm) + 0.15*regime_stability
        - 0.20*martingale_penalty
  ```
- [ ] `cli/rank.py`:
  - `--snapshot-date YYYY-MM-DD`:以某日為切點產生排名快照(可用於回測)
  - `--top N`:輸出 Top N
  - 結果寫入 `rankings` table

**交付物**:
- 500+ 錢包的即時排名快照
- 每個指標 100% 單元測試覆蓋(用人工構造的 trades 驗證數值)
- Top 10 錢包的 human-readable 分數拆解報告

**驗收(min/max)**:
- **min**: metrics.py 全部 pytest 通過;同一份 trades 輸入兩次分數完全一致(determinism)
- **max**: 人工抽審 Top 5 / Bottom 5,使用者認為排序「看起來合理」(主觀但必要)

**依賴**: Phase 1 的資料

**風險**: 指標 normalization 的 scaling(Sortino 可能到幾百,要 winsorize);martingale 偵測容易 false positive

**回滾**: 不影響上游資料,重調權重即可

**時程指引**: 兩週(含測試)

---

### Phase 3 — 歷史回測 Gate(防線 A,**Go/No-Go**)

**目標**: 驗證 Phase 2 的排名演算法**真的能選出未來賺錢的錢包**。

**此 Phase 失敗 → 回 Phase 2 調整 metrics/權重,不得強行進 Phase 4。**

**任務**:
- [ ] `backtest/validator.py`:
  - 切點 `t0 = 2025-04-30`(12 個月前),**只用 t0 以前的資料**產生 Top 20 排名
  - 計算這 20 個錢包從 `t0` 到 `t0 + 12 個月` 的實際 PnL
  - 對照組 A:同期 BTC buy-hold
  - 對照組 B:同期 HL 排行榜 Top 20(常被認為是 naive baseline)
- [ ] `backtest/reporter.py`:
  - Equity curve、中位數 PnL、爆倉率、對照組超額報酬
  - 多個 t0 切點 rolling(2025-01 / 2025-04 / 2025-07),避免單一切點 overfit

**驗收(Go/No-Go gate,必須全部達成)**:
- ✅ Top 20 中位數年化 PnL > 0
- ✅ Top 20 中位數 > BTC buy-hold − 5pp(至少不輸太多)
- ✅ Top 20 中位數 ≥ HL naive leaderboard Top 20 + 10pp(證明你的演算法有 edge)
- ✅ Top 20 爆倉率(最終 drawdown > 80%)< 20%
- ✅ 上述在 ≥ 2 個 rolling 切點都成立(避免單一時點 overfit)

**失敗處置**:
- 記錄失敗原因(權重問題?特徵缺失?資料量不足?)
- 回 Phase 2 修改 → 重跑 Phase 3
- 連續 3 次失敗 → 重審「走 Hyperliquid 跟單」路線是否可行

**時程指引**: 一週跑 + 根據結果迭代

**風險**: 這是最容易欺騙自己的階段。**所有回測必須嚴格 walk-forward,禁止任何形式的 lookahead**(含 normalization scaling、threshold tuning 都只能用 t0 之前的資料)

---

### Phase 4 — Shadow Mode(紙上跟單 2 週)

**目標**: 在真實市場條件下驗證「從偵測訊號到產生 OKX 訂單」整條路,但不下實單。

**任務**:
- [ ] `scanner/realtime.py`:HL websocket 訂閱白名單錢包的即時 fills
- [ ] `execution/mapper.py`:
  - HL symbol → OKX symbol mapping table(yaml 手寫,初期覆蓋 BTC/ETH/SOL/BNB/DOGE 等 Top 20)
  - size 換算:`okx_notional = user_capital * (whale_position_pct_of_their_equity)`
  - 最小單量檢查,過小跳過
- [ ] `execution/aggregator.py`:
  - 白名單有 N 個錢包,同幣種訊號加權合併
  - 衝突解決:3 long vs 2 short 且金額接近 → 跳過
- [ ] `shadow/simulator.py`:
  - 每個訊號記錄「假設執行價 = 訊號到達時 OKX ask/bid」
  - 追蹤紙上倉位、紙上 PnL,寫入 `paper_trades`
- [ ] `cli/shadow.py`:常駐 daemon

**交付物**:
- 2 週連續運行無 crash
- `paper_trades` 有 ≥ 50 筆紙上交易
- 紙上 PnL 報告 vs Phase 3 回測預期區間

**驗收(min/max)**:
- **min**: 系統 uptime ≥ 95%、訊號延遲(HL fill ts → 紙上單成立 ts)中位數 < 10 秒
- **max**: 紙上 PnL 為正,且與 Phase 3 回測預期中位數偏差 ≤ 30%(驗證回測可泛化)

**依賴**: Phase 3 通過

**風險**: 訊號延遲超出 budget(鯨魚已佔好位,我們接到的是餘波);symbol mapping 覆蓋不足導致大量訊號被跳過

**失敗處置**:
- 延遲問題 → 優化 ws 訂閱結構、DB write 改 async
- 紙上 PnL 為負且偏差 > 30% → 回 Phase 3 重新驗證(可能 overfit 或 regime shift)

**時程指引**: 2 週 calendar time(不能壓縮,需要真實市場週期)

---

### Phase 5 — 實盤上線(逐步加碼)

**目標**: 用真錢驗證,但以「活下來」為優先。

**任務**:
- [ ] `execution/order.py`:ccxt OKX 下單,帶冪等 key(避免重試下兩單)
- [ ] `execution/guard_bridge.py`:接現有 `guards/pipeline.py`,新增跟單專用 guard:
  - `MaxExposurePerWalletGuard` — 單錢包貢獻倉位 ≤ 20% 總資金
  - `SignalLatencyGuard` — 訊號延遲 > 15 秒直接拒絕下單
  - `CorrelationCapGuard` — 同時持有 > 3 個高相關資產(e.g., 多個 L1 long)拒絕新增
  - `DailyLossCircuitBreaker` — 日虧 > 5% 暫停至隔日 00:00 UTC
  - `ConsecutiveLossGuard` — 連 3 日虧損進入 shadow mode
- [ ] `cli/trade.py`:live mode daemon(env var `SM_MODE=live` 才下真單)
- [ ] `docker-compose.prod.yml`:
  - 移除 `freqtrade` service
  - 新增 `smart-money-scanner` service(每小時跑 scan)
  - 新增 `smart-money-ranking` service(每週日跑 rank refresh)
  - 新增 `smart-money-trader` service(常駐 ws + trade)
- [ ] Telegram bot 訊息:白名單變動通知、下單通知、guard 拒單通知、日結報告

**資金加碼時程**(嚴格遵守):
| 期間 | 資金 | Gate 條件 |
|---|---|---|
| 第 1 週 | $100 | 零 crash、guards 正常觸發、無嚴重 bug |
| 第 2-3 週 | $300 | 第 1 週週結 PnL > -15% |
| 第 4-6 週 | $600 | 第 2-3 週累計 PnL > -10% |
| 第 7 週起 | $1,000 | 累計 PnL > -5% |

**任一階段觸發「累計 PnL < 下一門檻」 → 縮回上一級資金,不得跳級加碼。**

**交付物**:
- VPS 上 smart-money-* 容器全部 healthy
- 第一筆真實成交 + Telegram 通知
- 每週自動 PnL 報告

**驗收(min/max)**:
- **min**: 容器 uptime ≥ 99%、guards 在測試場景下 100% 正確觸發(整合測試)
- **max**: 第 4 週累計 PnL 為正 + 無任何 Kill switch 觸發

**依賴**: Phase 4 通過

**風險**: OKX API 限流、訂單 fill 滑價大、市場 regime shift 導致白名單全軍覆沒

**回滾**: `SM_MODE=shadow` 立即切回紙上;極端情況 `docker compose stop smart-money-trader`,持倉用 OKX 網頁手動平

**時程指引**: 資金加碼路徑鎖定 6-7 週,不要搶快

---

### Phase 6 — AI 質性層(Phase 3 之後再決定是否啟用)

**觸發條件**: Phase 3 通過 + Phase 4 shadow mode 產出基準 → 此時才評估 AI 是否能**補強**排名。

> 使用者明確指示:「回測後慢慢確定」。因此 Phase 6 **預設關閉**,實作後以 A/B 對比決定是否開啟。

**任務**(條件啟用):
- [ ] `ranking/ai_layer.py`:
  - input: 單錢包最近 100 筆交易摘要 + 市場新聞快照
  - 呼叫 `api.acetoken.ai /v1/messages` (VPS 已接)
  - output JSON schema:
    ```json
    {
      "trader_type": "discretionary_trend | grid_bot | martingale | insider | unknown",
      "suspicion_score": 0.0-1.0,
      "alpha_hypothesis": "short text",
      "confidence": 0.0-1.0,
      "red_flags": ["string"]
    }
    ```
- [ ] 混合分數:`final = 0.7*quantitative + 0.3*ai_qualitative`(權重待 A/B 測試定)
- [ ] 成本控制:每個錢包 cache 結果 72h;僅對 Top 50 跑 AI;預估日成本 < $2
- [ ] A/B 對比:同時跑「純量化」vs「量化 + AI」兩套排名,30 天觀察哪個紙上 PnL 較好

**決策點**: 30 天 A/B 後,若 AI 版本紙上 PnL 超出純量化 > 10% → 採用;否則下架。

**驗收**: A/B 結果有統計意義(每組訊號 ≥ 30 筆)

**回滾**: 單一 env var `SM_AI_LAYER_ENABLED=false` 關閉,不影響主線

---

## 4. 風險登記

| 風險 | 等級 | 緩解 |
|---|---|---|
| 排名演算法 overfit 到歷史資料 | 🔴 高 | Phase 3 rolling walk-forward;Phase 4 shadow mode 實戰驗證 |
| Hyperliquid API 改版/中斷 | 🟡 中 | 封裝在 `hl_client.py` 單一入口;健康檢查告警 |
| OKX 訂單滑價吃光 alpha | 🟡 中 | `SignalLatencyGuard` 卡 15 秒;限價單優先,超時才轉市價 |
| 鯨魚錢包 rug 或被駭 | 🟡 中 | 每週重算排名;單錢包曝險 ≤ 20% |
| LLM cost 爆炸 | 🟢 低 | 72h cache + Top 50 限制 + daily budget cap |
| VPS 單點故障 | 🟡 中 | 持倉上限卡死;disaster_recovery.sh 已有骨架 |
| $1k 被一次 outlier 虧 50% | 🔴 高 | Daily loss circuit breaker + 資金加碼路徑(不直上 $1k) |

---

## 5. 待確認的關鍵決策(進 Phase 0 前必須答覆)

1. **是否完全下架 Freqtrade?**
   - 我的建議:是。Freqtrade 是策略框架,跟單不需要它
   - 但你如果想保留 SMCTrend 當「備用策略」(例如跟單訊號不足時跑 TA)也行,只是複雜度 +30%
   - 👉 **預設:下架**,除非你反對

2. **是否保留未提交的 tw_futures_*.py?**
   - 目前是 untracked files,我不會主動刪
   - 你自己決定要 commit 到別的 branch 保留、還是丟棄

3. **資金來源確認**
   - $1k 已在 OKX?還是需要先入金?
   - 加碼路徑 $100 → $300 → $600 → $1k 你同意嗎?

4. **上線時間目標**
   - 我的估算:**P0-P5 整條走完約 10-12 週**(含回測迭代可能)
   - 你有更緊的時程需求嗎?(注意:壓 Phase 3/4 = 等於沒做)

5. **Supabase 連線**
   - 現有 Supabase project 有 capacity 加新 schema 嗎?還是另開?

---

## 6. 與現有系統的共存策略

Phase 0-4 期間,VPS 上**現有 Freqtrade/market_monitor 繼續跑**(Phase 0 已承諾不動 production)。

切換點:**Phase 5 上線日**
- 當天 T-1:freqtrade 改 `--dry-run`
- 當天 T=0:停用 freqtrade、啟用 smart-money-*
- 當天 T+1:觀察無異常後 `docker rm ambmh-freqtrade-1`

舊系統資料(trading_log/)繼續保留作為歷史對照。

---

## 7. Supabase Schema 草稿

```sql
-- 錢包基本資料
create table wallets (
  id uuid primary key default gen_random_uuid(),
  address text unique not null,             -- HL wallet address
  first_seen_at timestamptz not null,
  last_active_at timestamptz not null,
  tags text[] default '{}',                 -- 人工標籤: "whitelisted", "watchlist"
  created_at timestamptz default now()
);

-- 每筆交易(從 HL 拉)
create table wallet_trades (
  id bigserial primary key,
  wallet_id uuid references wallets(id),
  hl_trade_id text not null,
  symbol text not null,                     -- HL native symbol, e.g. "BTC"
  side text not null,                       -- "long" | "short"
  action text not null,                     -- "open" | "close" | "increase" | "decrease"
  size numeric not null,
  price numeric not null,
  pnl numeric,                              -- 僅 close 有值
  fee numeric not null default 0,
  ts timestamptz not null,
  unique (wallet_id, hl_trade_id)
);
create index idx_wallet_trades_wallet_ts on wallet_trades (wallet_id, ts desc);

-- 每週排名快照
create table rankings (
  id bigserial primary key,
  snapshot_date date not null,
  wallet_id uuid references wallets(id),
  rank int not null,
  score numeric not null,
  metrics jsonb not null,                   -- 所有指標細項
  ai_analysis jsonb,                        -- Phase 6 才填
  unique (snapshot_date, wallet_id)
);
create index idx_rankings_date_rank on rankings (snapshot_date, rank);

-- 紙上交易(shadow mode)
create table paper_trades (
  id bigserial primary key,
  source_wallet_id uuid references wallets(id),
  symbol text not null,                     -- OKX symbol e.g. "BTC/USDT:USDT"
  side text not null,
  size numeric not null,
  entry_price numeric not null,
  exit_price numeric,
  pnl numeric,
  signal_latency_ms int,                    -- HL fill → 本端偵測
  opened_at timestamptz not null,
  closed_at timestamptz
);

-- 實際交易
create table live_trades (
  id bigserial primary key,
  source_wallet_id uuid references wallets(id),
  okx_order_id text unique,
  symbol text not null,
  side text not null,
  size numeric not null,
  entry_price numeric,
  exit_price numeric,
  pnl numeric,
  signal_latency_ms int,
  guard_decisions jsonb,                    -- 所有 guards 的判斷紀錄
  opened_at timestamptz not null,
  closed_at timestamptz
);
```

---

## 8. 下一步(等你確認)

使用者逐行審完此文件後:
1. 回答 §5 的 5 個關鍵決策
2. 指出任何不同意的驗收條件、權重、或階段劃分
3. 批准後 → 我建立 `feat/smart-money-v1` branch 並開始 Phase 0

**不批准不動手。**
