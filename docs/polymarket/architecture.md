# Polymarket 交易與情報系統 — 架構定稿

> 本文件是 Polymarket 模組的架構憲法。修改前必須評估是否違反第一章的不變原則。
> 文件最後修訂：2026-04-21

---

## 第一章 不變的原則

這些原則在未來至少三年內不應被修改。任何與其衝突的實作決策都應被駁回。

### 原則 1：情報先於交易

系統的主要產出順序是「數據 → 情報 → 手動驗證 → 紙上自動化 → 真實自動化」。任何階段不得跳級。具體含義：

- Phase 1 的唯一交付是 Telegram 推播的情報訊號，連紙上交易都不做
- Phase 2 的紙上交易在 Phase 1 運行至少 2 個月、你手動記錄的驗證表顯示情報有正期望值後才開始
- Phase 3 的真實交易在 Phase 2 至少一個策略通過晉升門檻後才開始，且永遠經過資本階梯

違反此原則的常見誘惑：「紙上交易看起來很好，直接跳到真實交易加大資金」。這是已知的系統性失敗模式，拒絕。

### 原則 2：不引入新基礎設施直到觸發條件滿足

MVP 使用 repo 現有的技術棧：Python 3.11、httpx/websockets、Supabase PostgreSQL、JSON state store、Telegram Bot API。不新增 Redis、Kafka、ClickHouse、TimescaleDB、Airflow。

每一個基礎設施升級都必須有對應的「觸發信號」（量化的、可觀測的條件），未達成前不升級。觸發條件記錄在第二章。

違反此原則的常見誘惑：「業界標準就是這樣」。業界標準是為多人團隊設計的，不是單人運維系統的最佳解。

### 原則 3：Pre-Registration（預先登記）

**任何需要統計判斷的決策，其門檻值與評估指標都必須先於觀察結果確定，且記錄在版控中。**

這防止了金融系統中最常見的自欺：策略跑出來不好看就換一個有利的指標、換一個更寬鬆的門檻。所有門檻值寫在 `polymarket/config/pre_registered.yaml`，每個值帶有：

- `set_at`：設定日期
- `rationale`：為什麼是這個數字
- `next_review`：下次可以主動評估的日期

修改值必須透過 git commit 進行，commit message 必須說明變更理由與新舊值差異。禁止在策略代碼中 inline hardcode 門檻。

**Pre-Registration 延伸到指標本身**：新增一個評估指標（例如從 Brier 改用 LogLoss）也必須先寫進 yaml，說明它評估什麼、門檻是多少、為什麼選這個數字。

### 原則 4：資本階梯永不跳級

從紙上交易到真實交易的過渡永遠走「$50 壓力測試 → $200 漂移驗證 → $500 信號驗證」的三階段。每階的目的不同、成功標準不同、失敗後的降階規則明確。

禁止以任何理由合併階段或跳過某一階。即便手動驗證時你賺了 $5000，進入 Phase 3 仍然從 $50 開始。這不是為了謙遜，是為了累積真實執行環境下的系統行為數據。

### 原則 5：策略版本雙軌制

每個策略有兩個版本並行：

- **`live` 版本**：參數完全凍結，Phase 3 所有真實交易用此版本。凍結的目的是讓真實 PnL 可以被明確歸因到「晉升時評估的那個策略」。
- **`experimental` 版本**：持續在紙上交易運行，可以更新參數、吸收新數據。

每 90 天做一次版本檢視：如果 experimental 在同期間的 Brier Score 與存活性顯著優於 live，可以將 experimental 提升為新的 live 版本。但**升版後必須重新走完資本階梯**（從 $50 開始），因為新版本在真實環境下仍是未驗證的。

### 原則 6：數據不可變性（Immutability）

所有進入系統的原始數據帶時間戳與來源標記，寫入後不得修改。重複抓到的同一事件以 idempotency key 去重，但保留所有來源記錄。這讓未來的回測與歸因分析可以完全復現當時的決策環境。

修改已落庫數據的唯一合法情境是「修正明確的 schema 錯誤」，且必須透過 migration 完成、保留原始值的備份欄位。

### 原則 7：刻意不做的事

為防止 scope creep，以下事項在 Phase 4 結束前不做，即便看似容易：

- 跨平台套利（Kalshi、Myriad、Manifold）
- 新聞情緒 / 社群媒體訊號
- LLM 自主 Agent 下單
- Rust 客戶端
- 自建訂單簿（market making）

未來要做其中任一項，必須先用一份獨立的設計文件論證「為什麼現在該做、為什麼之前不該做」。

---

## 第二章 當前的具體規格

本章所有數字都是 pre-registered 值的**文件快照**。真實來源是 `polymarket/config/pre_registered.yaml`。兩者衝突時以 yaml 為準。

### 2.1 鯨魚分層門檻

| 層 | 90 天交易數 | 勝率 | 累積 PnL | 單筆平均 | 推播策略 |
|---|---|---|---|---|---|
| A | ≥ 20 | ≥ 60% | ≥ 10k USDC | ≥ 500 | 即時單筆推播，高優先 |
| B | ≥ 15 | ≥ 55% | ≥ 5k | ≥ 250 | 即時單筆推播，低優先 |
| C | ≥ 10 | ≥ 50% | ≥ 2k | ≥ 100 | 每日彙整推播 |

**穩定性後過濾（post-filter）**：將 90 天切成 3 段 30 天，每段勝率必須 ≥ 該層勝率門檻 × 0.85。否則該錢包歸入「波動型」標籤，不納入任何層級。

**跨層移動**：
- 升級（C→B、B→A）推播，標籤 `promoted`
- 降級不推播，僅記錄於 `whale_tier_history` 表
- 單筆交易的 idempotency key 為 `(wallet_address, tx_hash)`，跨層不重複推播

### 2.2 策略晉升指標

紙上交易晉升到 Phase 3 資本階梯月 1 的條件（所有條件必須同時滿足）：

**主要指標 — Brier Score**
```
BS = (1/N) × Σ (p_i - o_i)²
```
其中 p_i 是進場時隱含機率，o_i 是 0 或 1 的實際結果。只計入已結算交易。

門檻：**BS ≤ 0.22**

參考值：完全隨機策略 ≈ 0.25，Polymarket 市場本身 ≈ 0.18-0.20，極優策略 ≈ 0.15。

**輔助指標 — 校準誤差（Calibration Error）**
將隱含機率分桶（0-20%、20-40%、40-60%、60-80%、80-100%），計算每桶「實際成真率 vs 隱含機率」的加權絕對差。

門檻：**CE ≤ 0.08**（用於視覺化與解釋，不作為晉升硬門檻）

**桶內最小樣本門檻**：任一桶樣本 < 10 時，該桶不納入 CE 計算，標記為「數據不足」。

**存活性**
紙上資金曲線的最大回撤 ≤ 25%（對應真實交易熔斷 10%，留 2.5x 安全邊際）。觸發即重置觀察期從 0 開始。

**樣本量**
累積已結算交易 ≥ 50 筆 **AND** 觀察時間 ≥ 60 天。兩個都要滿足，不是取寬鬆者。

**策略間獨立性 — 信號方向一致率**

比較範圍：兩策略在同一市場都有**實際進場訊號**（不含未進場的觀察訊號）。

時間窗口：同一市場、72 小時內的同方向進場算一致。超過 72 小時視為獨立決策。

門檻判斷：
- 一致率 < 50%：獨立訊號源，可並存
- 50% ≤ 一致率 ≤ 70%：部分重疊，允許並存但新策略信號權重 × 0.5
- 一致率 > 70%：實質重複，新策略不得晉升（除非舊策略先降級）

### 2.3 資本階梯

| 階段 | 資金 | 主要目的 | 進下一階的條件 |
|---|---|---|---|
| 月 1 | $50 | 壓力測試系統對接 | ≥ 10 真實成交、0 idempotency 違規、至少一次熔斷測試、實際滑點 ≤ 預期 × 1.5 |
| 月 2 | $200 | 偵測模型漂移 | 見 §2.4 的兩層漂移檢查皆通過 |
| 月 3 | $500 | 驗證信號在真實環境的期望值 | 累積 PnL 為正（三個月合計）|
| 月 4+ | 最多 2× 前月 | 規模化 | 同月 3 條件，且總資金 ≤ 單市場 30 日均量 × 1% |

**降階觸發（自動執行）**：
- 單月實現 PnL ≤ -15% → 下月降回前一階
- 策略 Brier Score 惡化 > 前期 × 1.5 → 下月降回前一階
- 任何 idempotency 違規 → 立即降回月 1 並暫停 7 天

### 2.4 漂移警報（兩層）

月 2 開始每週執行，任何一層警報觸發即暫停加碼。

**第一層 — 執行層一致性**

不比較 PnL。逐筆比較：
- 實際成交價 vs 紙上假設的中間價 → 每筆滑點
- 全期平均滑點、滑點分布（p50、p90、p99）

警報：平均滑點 > 紙上假設 × 2，或 p90 滑點 > 紙上假設 × 3。

修正路徑：調整紙上交易的滑點模型，不是暫停策略。

**第二層 — 訊號層一致性**

比較：系統在 X 時刻發出的訊號 vs 實際執行的訊號。成因包含 rate limit、WS 斷線、order book 變動太快。

警報：訊號與執行的方向不一致率 > 5%，或訊號生成到下單的延遲 p90 > 2 秒。

修正路徑：檢查 `decision/executor.py` 與 rate limit 邏輯。

**PnL 層（第三層，參考用）**

兩層執行/訊號對齊後，再比較實際 PnL 與紙上 PnL。若差異 > ±30% 且樣本量 ≥ 20，表示策略在某類市場的 edge 比紙上估計小，觸發策略重新評估。

### 2.5 風控熔斷參數

真實交易階段的多層風控：

| 層級 | 門檻 | 觸發後動作 |
|---|---|---|
| 單筆倉位 | ≤ 當前資金 2% | 拒絕下單 |
| 單一市場總曝險 | ≤ 5% | 拒絕新進場 |
| 單一類別（政治/體育/加密/其他） | ≤ 20% | 拒絕新進場 |
| 日內虧損 | ≥ 5% | 暫停下單，僅允許減倉 |
| 週度回撤 | ≥ 10% | 暫停下單，資金降回前一階 |
| 月度回撤 | ≥ 15% | 暫停策略 30 天，進入歸因分析 |

### 2.6 基礎設施升級觸發條件

| 組件 | 當前選擇 | 升級到 | 觸發信號 |
|---|---|---|---|
| 訊息隊列 | asyncio.Queue | Redis Stream | 每秒 > 100 訊息持續 > 5 分鐘 |
| 時序 DB | Supabase PostgreSQL | + TimescaleDB extension | 主要查詢 > 1 秒持續 1 週 |
| CLOB 客戶端 | py-clob-client | rs-clob-client | 單 WS 延遲 p90 > 500ms 或監控市場 > 50 個 |
| 分析引擎 | pandas + SQL | DuckDB | 單次回測 > 10 分鐘 |
| 排程 | cron | Airflow | 同時管理 > 20 個排程任務 |

### 2.7 部署與運維

- 所有對外 API 呼叫走共用的 `httpx.AsyncClient`，帶 retry（最多 3 次，exponential backoff）與 rate limit（遵守各家 API 規範）
- WebSocket 斷線自動重連，重連後回補遺漏時段（用 REST 補洞）
- Telegram 通知復用 `market_monitor/telegram_zh.py`，訊息前綴 `[POLY]`
- 日誌統一 JSON 格式，levels: DEBUG/INFO/WARNING/ERROR

---

## 第三章 已知的未解問題

這些問題在現階段沒有答案。記錄在此是為了未來遇到時不需要從零開始思考。

### 3.1 如何區分鯨魚的「方向性押注」與「對沖部位」

問題：一個鯨魚在 Polymarket 買 YES，他可能是：
(a) 真的認為 YES 會發生
(b) 他在 Kalshi 有相反的 NO 部位，這是套利而非信念
(c) 他持有某個現實資產，Polymarket 倉位是對沖其風險

只觀察 Polymarket 單側錢包無法區分這三種情境。跟單 (a) 可能賺錢，跟單 (b) 與 (c) 是純粹的噪音。

暫定處理：Phase 1-2 忽略此問題，所有鯨魚都當作 (a) 處理。Phase 3 若觀察到某些鯨魚的可跟單價值顯著低於其歷史勝率暗示的水準，再研究如何識別對沖型錢包。

可能的未來方向：交叉比對 Kalshi 的公開數據、追蹤鯨魚在 DeFi 協議的其他部位、時間序列分析（對沖倉位通常建倉時間分散）。

### 3.2 如何快速確認策略失效的原因是「市場變了」還是「我錯了」

問題：策略 Brier Score 從 0.19 惡化到 0.26。這可能是：
(a) 市場結構變了（例如流動性增加使 edge 被抹平）
(b) 我的特徵計算有 bug
(c) 策略從一開始就是過擬合，只是現在現形

區分這三種情境需要不同的應對（a 是退場、b 是修 bug、c 是重新設計）。

暫定處理：每次 Brier 惡化時啟動「歸因檢查清單」，手動走一遍。Phase 4 再考慮自動化。

### 3.3 Polymarket 的流動性天花板

問題：單一市場的可吸納資金量有限。當我的策略規模接近這個天花板時，我自己的進場就會推動價格，我的訊號（原本基於別人的行為）變成自我實現預言。

暫定處理：規格中的「總資金 ≤ 單市場 30 日均量 × 1%」是保守估計。真實的天花板需要 Phase 3 實測。若發現自己的進出場對價格造成 > 0.5% 的衝擊，立即降階並重估此參數。

### 3.4 解算爭議（resolution dispute）的處理

問題：Polymarket 使用 UMA Oracle 解算，但少數市場的結果有爭議（例如模糊事件、oracle 被提交錯誤答案、被挑戰後重新投票）。這些期間倉位會被凍結，PnL 無法計算。

暫定處理：Phase 1-2 忽略。Phase 3 遇到第一次爭議事件時專門設計處理邏輯（至少要正確標記、不計入 Brier 計算、不觸發熔斷）。

### 3.5 稅務與合規

問題：依使用者所在地，Polymarket 可能受限或需申報。

暫定處理：Phase 1-2 純讀取不涉及。Phase 3 啟動前必須與當地會計師確認。

---

## 第四章 Phase 執行清單

### Phase 0 — 骨架 + 最小數據管道（3-5 天）

**交付物：**
- [ ] `polymarket/config/pre_registered.yaml`（先於其他一切存在的憲法檔案）
- [ ] `polymarket/config.py`（endpoints、env vars 讀取）
- [ ] `polymarket/models.py`（Market、Book、Trade、Position Pydantic 模型）
- [ ] `polymarket/clients/clob.py`（markets、book、trades 三個方法，封裝 py-clob-client）
- [ ] `polymarket/clients/gamma.py`（list_markets、get_event）
- [ ] `polymarket/storage/schema.sql`（Supabase DDL）
- [ ] `polymarket/storage/repo.py`（薄 repository 層）
- [ ] `polymarket/cli.py`（`fetch-book`、`fetch-markets` 子命令）
- [ ] `tests/test_polymarket_clob.py`（≥ 10 個 mock-based 測試）

**驗收標準：**
```bash
python -m polymarket.cli fetch-markets --limit 20
python -m polymarket.cli fetch-book --token-id <任一市場 token id>
pytest tests/test_polymarket_*.py
```
全部綠燈，Supabase 裡能 SQL 查到寫入的數據。

---

### Phase 1 — 鯨魚情報引擎（1-2 週）

**交付物：**
- [ ] `polymarket/clients/data_api.py`（get_user_trades、get_user_positions）
- [ ] `polymarket/clients/ws.py`（market channel 訂閱 + 自動重連）
- [ ] `polymarket/features/whales.py`（錢包統計、層級歸屬、穩定性後過濾）
- [ ] `polymarket/pipeline.py`（排程任務：5 分鐘一跑）
- [ ] `polymarket/telegram.py`（格式化推播訊息）
- [ ] `whale_tier_history` 表 + migration

**驗收標準：**
- 24 小時連續運行無漏訊息、無重複推播
- 至少識別出 20+ 個符合鯨魚條件的錢包
- 推播訊息含：錢包、市場、方向、金額、層級、過去 90 天勝率

**本階段人工驗證任務（你）：**
建立 `docs/polymarket/manual_validation.md`，每次值得跟單的訊號記錄：時間、錢包、層級、隱含機率、你的決策、2 週後的結果。2 個月後此表將決定是否進 Phase 2。

---

### Phase 2 — 紙上交易 + 策略動物園（2-4 週）

**交付物：**
- [ ] `polymarket/features/registry.py`（`@feature` decorator、point-in-time 隔離）
- [ ] `polymarket/features/microstructure.py`（order imbalance、depth ratio、spread volatility）
- [ ] `polymarket/strategies/base.py`（Strategy ABC）
- [ ] `polymarket/strategies/copy_whale.py`
- [ ] `polymarket/strategies/order_flow.py`
- [ ] `polymarket/strategies/registry.py`（live/experimental 雙版本管理）
- [ ] `polymarket/paper/book.py`（紙上帳本、每策略獨立 PnL）
- [ ] `polymarket/paper/promoter.py`（晉升判斷邏輯：Brier + 存活 + 樣本 + 獨立性）
- [ ] `polymarket/learning/attribution.py`（週報告）

**驗收標準：**
- 紙上交易連續 30 天無錯誤、正確結算已解算市場
- 產出週報告顯示每個策略的 Brier、CE、最大回撤、樣本量、與其他策略的信號方向一致率
- 至少 1 個策略達到晉升門檻（若否，不進 Phase 3）

---

### Phase 3 — 真實交易（資本階梯，條件觸發）

**啟動前置條件：**
- [ ] Phase 2 至少 1 個策略通過晉升門檻
- [ ] 你的 `manual_validation.md` 表 2 個月內手動跟單為正期望值
- [ ] 確認所在地可合法訪問 Polymarket
- [ ] 與會計師確認稅務處理
- [ ] 初始資本 $50 已準備

**交付物：**
- [ ] `polymarket/decision/risk.py`（§2.5 的多層風控）
- [ ] `polymarket/decision/executor.py`（下單 + idempotency ledger，抄 unitmargaretaustin 模式）
- [ ] `polymarket/decision/aggregator.py`（策略信號加權）
- [ ] `polymarket/learning/health.py`（§2.4 的兩層漂移檢查）

**資本階梯執行：**
- 月 1 $50 → 月 2 $200 → 月 3 $500，各階驗收標準見 §2.3
- 降階規則自動生效

---

### Phase 4 — 學習與適應（上線後持續）

- [ ] `polymarket/learning/recalibrate.py`（每週 walk-forward 調 experimental 版本參數）
- [ ] 每 90 天的 live vs experimental 版本檢視流程
- [ ] 每季度的「歸零重思」架構審查（附 `docs/polymarket/quarterly_review_YYYY_QN.md`）
- [ ] 策略孵化管線（來自學術論文、公開錢包逆向工程、LLM 腦力激盪）

---

## 附錄 A — 參考的開源專案

| 專案 | 精讀重點 | 借鑒到 |
|---|---|---|
| `Polymarket/py-clob-client` | 官方 CLOB 客戶端 | 直接依賴 |
| `polymarket-apis` | Pydantic 統一客戶端結構 | `models.py` 設計 |
| `al1enjesus/polymarket-whales` | 鯨魚門檻、Telegram 格式 | Phase 1 |
| `NYTEMODEONLY/polyterm` | SQLite schema、洗盤偵測、內線評分 | Phase 1-2 特徵 |
| `unitmargaretaustin/Polymarket-copy-trading-bot` | idempotency ledger、滑點檢查 | Phase 3 執行層 |
| `samanalalokaya/polymarket-copy-trading-bot` | pricePredictor 信號評分 | Phase 2 策略 |
| `GiordanoSouza/polymarket-copy-trading-bot` | Supabase Realtime 偵測模式 | Phase 1 觸發器 |
| `MrFadiAi/Polymarket-bot` | 多層風控具體數字 | §2.5 參考 |
| `Polymarket/agents` | LLM Agent 整合 | Phase 4+ 評估 |

## 附錄 B — 版本歷史

| 日期 | 版本 | 主要變更 |
|---|---|---|
| 2026-04-21 | 1.0 | 初版定稿：五層架構、三層鯨魚、Brier 主指標、資本階梯、live/experimental 雙版本、pre-registration 擴展到指標 |
| 2026-04-22 | 1.1 | Phase 1.5a：scanner 模組重構（4 階段：discovery → coarse_filter → features → classify）；新增 `wallet_profiles` 時序表；`WalletProfileService` 統一讀取介面；`scanner_version` 版本管理 |
| 2026-04-22 | 1.2 | Phase 1.5b：新增 `category_specialization` 與 `time_slice_consistency` 兩個 feature；SCANNER_VERSION 升至 `1.5b.0`；Telegram 推播加入 specialist 標籤；pipeline 預取 market categories |

---

## 第五章 Phase 1.5：Scanner 重構（2026-04-22 新增）

Phase 1 的 `features/whales.py` 已升級為 `polymarket/scanner/` 模組，採用四階段流水線。**此重構不改變任何外部行為或推播邏輯**——目的是為 Phase 1.5b+ 的多維特徵擴充建立基礎。

### 5.1 架構層次

```
polymarket/
├── scanner/                       # NEW — 錢包畫像生成器
│   ├── __init__.py               # SCANNER_VERSION 常量
│   ├── discovery.py              # 第一階段：候選池
│   ├── coarse_filter.py          # 第二階段：粗篩淘汰
│   ├── features/
│   │   ├── base.py               # BaseFeature ABC（min_samples + unknown fallback）
│   │   ├── core.py               # CoreStatsFeature (1.5a)
│   │   └── (1.5b: category_specialization, time_slice, brier...)
│   ├── classify.py               # 第四階段：tier + archetype + risk_flags
│   ├── profile.py                # WalletProfile dataclass
│   └── scan.py                   # 主流程編排
├── services/
│   └── wallet_profile_service.py # 統一讀取介面（兩表 fallback）
└── features/whales.py            # Phase 1 邏輯保留，被 scanner 包裝
```

### 5.2 雙表並存策略

`whale_stats`（Phase 1 契約）與 `wallet_profiles`（Phase 1.5+）同時存在：

| 表 | 用途 | 寫入模式 |
|---|---|---|
| `whale_stats` | Phase 1 推播契約，UPSERT 覆蓋 | 每錢包一筆，反映「當前」 |
| `wallet_profiles` | Phase 1.5+ 時序紀錄，append-only | 每次掃描 INSERT 一筆，反映「當時」 |

下游一律透過 `WalletProfileService` 讀取，避免雙表 schema 細節外洩。

### 5.3 A/B/C vs Archetype 的明確分工

- **A/B/C（量的閘門）**：粗粒度過濾，回答「值不值得進入注意範圍」。**字母順序代表「資料樣本基礎的穩固度」，不代表跟單優先序**
- **Archetype（質的畫像）**：多標籤分類，回答「行為模式是哪一類」。1.5c 起啟用：穩健 / 選擇 / 爆發 / 領域專家 / 異常訊息

兩者並存：粗篩走 A/B/C，深度畫像走 archetype。Telegram 推播會合併兩者標籤。

### 5.4 1.5 子階段交付計畫

| 階段 | 範圍 | scanner_version |
|---|---|---|
| **1.5a** ✅ | 重構 + scanner_version + 雙表 + 服務層 | 1.5a.0 |
| **1.5b** ✅ | 領域專精、時間切片一致性 | 1.5b.0 |
| **1.5b.1** | + Brier 機率校準（樣本累積後） | 1.5b.1 |
| **1.5b.2** | + 倉位-信心一致性 | 1.5b.2 |
| **1.5c** | 風險特徵：回撤、回撤後倉位行為、連虧後頻率、集中度；archetype 啟用 | 1.5c.0 |
| **1.5d** | 高成本特徵：分批進場、試探倉、加碼減碼對稱性 | 1.5d.0 |
| **1.7** | 訊息領先性、跨平台對沖偵測（條件性開啟） | 1.7.0 |

每個子階段結束都有可運行系統 + 累積的 wallet_profiles 時序資料。

### 5.5 Pre-registration 與 scanner_version 的雙重版本管理

兩種版本機制各司其職：

- **`pre_registered.yaml` 的 `next_review`**：閾值的時間版本
- **`SCANNER_VERSION`**：計算邏輯的代碼版本

當 `enabled_in_version` 列表中新增特徵 → 必須升 `SCANNER_VERSION`。
當既有特徵的計算邏輯改變 → 必須升 `SCANNER_VERSION`。
當僅閾值調整（例如 `min_trades_total` 從 5 改 10）→ 不升 `SCANNER_VERSION`，但要在 yaml 更新 `set_at` 與 `rationale`。

歷史 `wallet_profiles` 紀錄永不重算。跨版本比較需明確標示。詳見 `docs/polymarket/known_issues.md`。

