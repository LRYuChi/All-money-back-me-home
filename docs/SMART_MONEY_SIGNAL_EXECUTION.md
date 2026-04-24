# Smart Money — 訊號捕捉、辨識與跟單執行架構

**起草日期**: 2026-04-24
**範圍**: P4（Shadow 訊號捕捉）+ P5（Live 跟單執行）的完整細節設計
**前置條件**: P0-P3 已完成（scanner / ranking / backtest 全綠）
**與現有文件關係**: 補充 [`SMART_MONEY_MIGRATION.md`](SMART_MONEY_MIGRATION.md) 中只給到輪廓的 Phase 4/5；本文件是該兩 phase 的 detailed spec

---

## 0. TL;DR

P4 和 P5 的差別不在「多寫幾個檔案」，而在**訊號能不能在 15s 內穩定轉成部位**。現有 P0-P3 都是 offline batch；P4/P5 是**第一個需要 SLA 的即時系統**。

設計原則：
1. **訊號層與執行層解耦** — 中間用 `Signal` 資料結構 + 訊息佇列，方便 shadow → live 零代碼切換
2. **狀態機而非 if-else** — 原始 fill 要先規格化成 `Signal`，再進入 open/close/scale 狀態流，避免散亂處理
3. **先 shadow 測延遲、再 live 冒險** — P4 收集 ≥ 14 天真實 HL fills 的 latency 分佈後，才能回答「15s budget 夠不夠」
4. **平倉邏輯三擇一**（鏡像 / 智能 / 混合）需明示選擇，不可隱性 default

---

## 1. 端到端訊號生命週期

```
                    ┌──────────────────────────────────────┐
                    │         Whitelist（來源：最新                  │
                    │        sm_rankings top 10，可手動覆寫）     │
                    └────────────────┬─────────────────────┘
                                     │ addresses
                                     ▼
┌────────────────────────┐    ┌──────────────────┐    ┌────────────────┐
│  HL WebSocket          │───►│ realtime/        │───►│ Signal Queue    │
│  userFills stream      │    │ fill_dispatcher  │    │ (asyncio.Queue)│
│  + REST fallback       │    │ (latency_ms tag) │    └────────┬───────┘
└────────────────────────┘    └──────────────────┘             │
                                                               ▼
                                              ┌──────────────────────────┐
                                              │ signals/classifier       │
                                              │  raw fill → Signal 狀態機 │
                                              │  {OPEN, CLOSE, SCALE_UP, │
                                              │   SCALE_DOWN, REVERSE}    │
                                              └────────────┬─────────────┘
                                                           │ Signal
                                                           ▼
                                              ┌──────────────────────────┐
                                              │ signals/aggregator       │
                                              │  多錢包同向 merge + 加權   │
                                              └────────────┬─────────────┘
                                                           │ FollowOrder
                                                           ▼
                                         ┌────────────────┴────────────────┐
                                         │                                 │
                                         ▼                                 ▼
                              ┌────────────────────┐          ┌────────────────────┐
                              │ execution/mapper   │          │ shadow/simulator   │
                              │ HL→OKX symbol/size │          │ (paper trade only) │
                              └──────────┬─────────┘          └─────────┬──────────┘
                                         ▼                              │
                              ┌────────────────────┐                    │
                              │ execution/guards   │                    │
                              │ 7 道守門            │                    │
                              └──────────┬─────────┘                    │
                                         ▼                              │
                              ┌────────────────────┐                    │
                              │ execution/order    │  ccxt OKX          │
                              │ (idempotent)       │                    │
                              └──────────┬─────────┘                    │
                                         ▼                              ▼
                              ┌────────────────────┐          ┌────────────────────┐
                              │ sm_live_trades     │          │ sm_paper_trades    │
                              └────────────────────┘          └────────────────────┘
```

SM_MODE=shadow ↔ live 的切點在最後 fork：mapper 之後 shadow 走 simulator、live 走 guards→order。訊號上游（capture / classifier / aggregator）兩種模式**共用同一條管線**，shadow 是 live 的完全子集。

---

## 2. 訊號捕捉層（P4a）

### 2.1 資料源

HL 官方有 WebSocket `userFills` subscription：訂閱特定 address → 即時推送該地址的 fill events。

**兩條路**：

| 路徑 | 優勢 | 劣勢 | 判斷 |
|---|---|---|---|
| WS 長連線 | 低延遲（< 1s）、即時 | 需維護連線、斷線重連、ordering | ✅ 主路徑 |
| REST 輪詢 `userFillsByTime` | 簡單、幂等、容錯 | 延遲由 poll interval 決定（≥ 3s） | ✅ fallback 保命 |

**合併策略**：WS 為主、REST 為副。兩者皆寫入同一 ingestion queue，**以 `hl_trade_id` 去重**（P1 scanner 已用的 unique key）。REST 做 reconciler：每 60s 跑一次、拉過去 5 分鐘 fills，補上 WS 可能漏掉的（WS 斷線期間或訊息丟失）。

### 2.2 模組佈局

```
smart_money/scanner/
├── realtime.py              # NEW — WebSocket 訂閱 daemon
├── reconciler.py            # NEW — REST 補單對帳器
└── hl_client.py             # EXISTING — REST API 包裝，reconciler 會用

smart_money/signals/         # NEW 整個子套件
├── __init__.py
├── types.py                 # Signal / FollowOrder dataclass
├── dispatcher.py            # 把 raw fill 轉成內部事件、打 timestamp
├── classifier.py            # 狀態機：fill → Signal
├── aggregator.py            # 多錢包合流
└── whitelist.py             # 動態白名單供應器
```

### 2.3 延遲度量

這是 P4 **最重要的產出之一** — 所有 P5 的 latency budget 決策都靠這份資料。

每個 fill 事件記錄三個時間戳：

- `ts_hl_fill` — HL 回傳的 fill timestamp（on-chain 成交時）
- `ts_ws_received` — 我方 WebSocket 收到 message 時（`datetime.utcnow()`）
- `ts_queue_processed` — dispatcher 把訊號推進 queue 時

三段 latency：
- `network_latency_ms = ts_ws_received - ts_hl_fill`
- `processing_latency_ms = ts_queue_processed - ts_ws_received`
- `total_latency_ms = ts_queue_processed - ts_hl_fill`

寫入 `sm_paper_trades.signal_latency_ms` 與新欄位 `network_latency_ms`。**P4 驗收門檻**：14 天真實運行後，`total_latency_ms` 的 p95 < 10s、p99 < 20s。

若 p95 > 10s，代表 WS 不夠可靠 → P5 的 `signal_latency_budget_sec=15` 會讓 5% 的訊號被丟，需調整策略或換地端部署。

---

## 3. 訊號辨識層（P4b）— 狀態機

### 3.1 問題

HL 的 fill event 只是原始成交資料（address / coin / side / sz / px / dir），**沒有「這是開倉還是加倉」**的語意。錯判會造成錯誤跟單（例：把鯨魚的減倉當成開空）。

### 3.2 狀態機定義

對每個 `(wallet, symbol)` 維護當前 position state：

```
{
  wallet_id: int,
  symbol: str,
  side: "long" | "short" | "flat",
  size: float,             # 絕對數量（HL 單位）
  avg_entry_px: float,
  last_updated_ts: int,
}
```

收到 fill 時，比對新舊 state 算出 `SignalType`：

| 舊 state | 新 state | SignalType | 說明 |
|---|---|---|---|
| flat | long N | `OPEN_LONG` | 全新開倉 |
| flat | short N | `OPEN_SHORT` | 全新開倉 |
| long N | long N+K | `SCALE_UP_LONG` | 加多 |
| long N | long N-K (K < N) | `SCALE_DOWN_LONG` | 減多但未平 |
| long N | flat | `CLOSE_LONG` | 完全平多 |
| long N | short K | `REVERSE_TO_SHORT` | 反手做空（兩筆 fill 或一筆大單） |
| short → 對稱 | 同上 | ... | |

**落檔**：`smart_money/signals/classifier.py`
**輔助表**：新增 `sm_wallet_positions`（wallet + symbol 維度的 current state snapshot），作為狀態機的持久層。斷線重啟後從 DB 復原。

### 3.3 狀態機邊界條件

- **冷啟動**：若 `sm_wallet_positions` 沒有某 wallet 的該 symbol 記錄，**不能**直接把首次看到的 fill 當 `OPEN`；應先用 REST 拉 `clearinghouseState` 取得當前持倉，回填初始 state，才開始偵測訊號。
- **反手單**：HL 可能一筆 fill 就完成反手（direction 欄位會是 `Open Long -> Close Short` 這種）；classifier 要能解析此欄位，避免誤判為兩個獨立訊號。
- **非白名單 symbol**：若鯨魚開了我們 OKX 沒有的幣（如 HL 特有的 small cap），classifier 仍要更新 wallet_position state（為了下次對帳），但**不產出 Signal**。記錄到 `sm_skipped_signals` 追蹤「我們漏掉了多少單」。

---

## 4. 訊號聚合層（P4c）

### 4.1 為何需要

Top 10 白名單中可能同時有 3 個鯨魚在 10 分鐘內都做多 BTC。若每個都獨立跟單 → 同向過度曝險。

### 4.2 聚合策略

**時間窗聚合 + 分數加權**：

```python
# 在 aggregator 中：
#   window_seconds = 300
#   threshold = 2  # 至少 2 個 wallet 同向才觸發
#
# 收到 Signal 後：
#   key = (symbol, side)
#   pending[key].append((signal, wallet_score, ts))
#
# 每 10 秒檢查 pending：
#   for key, signals in pending.items():
#       recent = [s for s in signals if ts - s.ts < window_seconds]
#       if len(recent) >= threshold:
#           emit FollowOrder(
#               symbol, side,
#               size_mult = sum(s.wallet_score for s in recent) / baseline_score,
#               sources = [s.wallet_id for s in recent],
#           )
```

**size_mult 語義**：若多個高分鯨魚同時進場、size 按分數加權放大（上限由 `max_exposure_per_wallet` 和總倉位 cap 夾緊）。單一鯨魚進場 → size_mult = 1。

### 4.3 兩個模式可切換

config 新增：
```python
class SignalSettings(BaseSettings):
    mode: Literal["independent", "aggregated"] = "aggregated"
    aggregation_window_sec: int = 300
    min_wallets_for_signal: int = 2          # aggregated 模式才生效
    wallet_score_baseline: float = 0.6       # size_mult 的分母基準
```

`independent` 模式下每個鯨魚訊號獨立成單，簡單但可能過度曝險；`aggregated` 降低白雜訊單量但可能漏掉孤狼鯨魚的先知先覺。**P4 shadow 階段同時跑兩模式**（各自寫進 paper_trades 不同 tag），14 天後比較 PnL 決定 P5 用哪個。

---

## 5. Symbol / Size 映射（P5a）

### 5.1 Symbol 映射

HL 的 coin 命名與 OKX 不同（HL 用 `BTC`、OKX 用 `BTC-USDT-SWAP` 或 ccxt 的 `BTC/USDT:USDT`）。需維護一張對照表。

**落檔**：`smart_money/execution/mapper.py` + `config/smart_money/symbol_map.yaml`

```yaml
# symbol_map.yaml — 手動 curated，每週檢查 HL 有沒有新上幣
BTC:
  okx: BTC/USDT:USDT
  min_size_usdt: 10       # OKX 最小下單金額（保險邊際）
  price_precision: 1       # USDT
  size_precision: 0.001    # BTC
ETH:
  okx: ETH/USDT:USDT
  ...
HYPE:                      # HL 原生幣，OKX 沒上
  okx: null                # null 代表跳過
```

**啟動檢查**：daemon 啟動時對 OKX 呼叫 `fetch_markets()` 驗證每個 mapped symbol 真的存在；缺失則啟動失敗（fail fast，不是 skip）。

### 5.2 Size 換算

鯨魚部位是他帳戶 equity 的 X%；我方以自己資本的同比例開倉。

```python
def compute_okx_size(
    whale_position_usd: float,
    whale_equity_usd: float,
    my_capital_usd: float,
    signal_size_mult: float,  # aggregator 加權
    max_exposure_per_wallet: float = 0.20,
) -> float:
    pos_pct = whale_position_usd / whale_equity_usd      # 鯨魚曝險比
    base_size = my_capital_usd * pos_pct * signal_size_mult
    # 夾緊在單錢包曝險上限
    cap = my_capital_usd * max_exposure_per_wallet
    return min(base_size, cap)
```

**邊界**：
- `whale_equity_usd` 須從 `clearinghouseState` 拉（不能只看單筆 fill size，否則無法算比例）
- 若計算出來的 size 小於該 symbol 的 `min_size_usdt` → 不下單，記入 `sm_skipped_signals` 並附 reason=`below_min_size`
- `max_exposure_per_wallet=0.20` 是 per-wallet；**另有全域曝險上限**：所有未平倉名目總額 ≤ `1.5 × my_capital_usd`（槓桿上限由此隱含）

---

## 6. 守門管線（P5b）

### 6.1 Guard 執行順序（短路：任一 deny 即 abort）

```
FollowOrder → [G1 LatencyBudget] → [G2 SymbolSupported] →
              [G3 MinSizeCheck] → [G4 PerWalletExposureCap] →
              [G5 GlobalExposureCap] → [G6 CorrelationCap] →
              [G7 DailyLossCB] → [G8 ConsecutiveLossCB] →
              OKX order
```

| Guard | 規則 | 違規動作 |
|---|---|---|
| G1 `LatencyBudget` | `total_latency_ms > signal_latency_budget_sec × 1000` | deny + log `latency_exceeded` |
| G2 `SymbolSupported` | symbol_map.yaml 沒對照或 OKX 無此 market | deny + log `symbol_unsupported` |
| G3 `MinSizeCheck` | okx_size < symbol_map 的 `min_size_usdt` | deny + log `below_min_size` |
| G4 `PerWalletExposureCap` | 單錢包未平倉名目 + 本單 > `max_exposure_per_wallet × capital` | **scale down** size 到剛好填滿，仍下單 |
| G5 `GlobalExposureCap` | 總未平倉名目 + 本單 > `1.5 × capital` | deny + log `global_cap_hit` |
| G6 `CorrelationCap` | 已同向持有 `max_concurrent_correlated=3` 個高相關資產 | deny（下一期再進） |
| G7 `DailyLossCB` | 當日 realized + unrealized < `-daily_loss_circuit_breaker × capital`（-5%） | deny + 立即平倉所有、切 shadow mode |
| G8 `ConsecutiveLossCB` | 連續 `consecutive_loss_days_to_shadow=3` 天收黑 | 自動切 shadow 24h，隔日自動恢復 live |

每個 guard 決策寫進 `sm_live_trades.guard_decisions` JSONB，便於事後 audit：
```json
{
  "G1_latency": {"pass": true, "latency_ms": 2300},
  "G4_wallet_cap": {"pass": true, "current_exposure_usd": 120},
  ...
}
```

### 6.2 Correlation 判定

初版用硬編碼分群（BTC/ETH/SOL 算一組 "L1 majors"、BNB/SOL/AVAX 另一組 "L1 alts"）。**後續**（P5 v2）可改動態：取 60 天日收益 Pearson correlation ≥ 0.7 算一組。

---

## 7. 訂單生命週期（P5c）

### 7.1 進場

```python
# execution/order.py
async def place_follow_order(follow: FollowOrder) -> OrderResult:
    client_order_id = f"sm-{follow.symbol_okx}-{follow.side}-{follow.signal_ts_ms}"
    # 幂等：重送同一 clientOrderId 時 OKX 會拒（或返回原單），避免 double-open

    try:
        order = await okx.create_order(
            symbol=follow.symbol_okx,
            type="market",                  # P5 v1 先用 market 簡化，accept slippage
            side=follow.side,
            amount=follow.size_coin,
            params={"clientOrderId": client_order_id, "posSide": follow.side},
        )
    except ccxt.RateLimitExceeded:
        # retry 1 次 @ +500ms；仍失敗 → 放棄這筆
        ...
```

**為何用 market 而非 limit**：P5 v1 要優先驗證端到端通暢；market 最簡單、成交率 100%。P5 v2 可改 IOC limit `price = mid ± 0.1%` 降低滑點。

**進場後立即下 SL/TP bracket order**（若 OKX 支援 One-Cancels-Other）：
- SL：進場後 `-2%` 保險絲（防 WS 斷線期間被單邊行情洗掉）
- TP：**不設定** — 平倉完全由訊號（鯨魚 CLOSE_LONG/SHORT）驅動，我們不自作聰明定 TP

### 7.2 出場（核心決策）

三種模式，需明示選擇：

| 模式 | 邏輯 | 適用 |
|---|---|---|
| **鏡像** | 鯨魚 CLOSE → 我方立即以 market 平倉 | 完全跟單、依賴鯨魚 timing |
| **智能** | 忽略鯨魚 CLOSE；只靠 SL + 時間停損（N 小時未獲利即平） | 不信任鯨魚離場 timing |
| **混合** | 鯨魚 CLOSE + 我方已獲利 → 跟平；未獲利 → 維持部位等 SL | 鯨魚可能早於頂點離場 |

**預設採「鏡像」**（因為回測假設就是完全跟隨；若採他模式需重新跑 P3 gate）。config 新增：
```python
exit_mode: Literal["mirror", "smart", "hybrid"] = "mirror"
smart_exit_timeout_hours: int = 48        # smart/hybrid 用
smart_exit_profit_threshold: float = 0.015  # hybrid 用：獲利 ≥ 1.5% 才跟鯨魚平
```

### 7.3 Scale 事件處理

- `SCALE_UP` 訊號：依鯨魚新加倉比例，**加碼**我方部位到 `new_target_size`（呼叫 OKX `create_order` 差額）
- `SCALE_DOWN`：**減倉**到新比例
- `REVERSE`：先平後開兩步，中間不留 flat 間隙（用 OKX 的 one-way → hedge 切換或 reduce-only 技巧）

---

## 8. 白名單動態管理（P4b 支援）

### 8.1 供應源

`smart_money/signals/whitelist.py` 提供：
```python
def get_active_whitelist(as_of: datetime) -> list[WalletEntry]:
    """Return wallets to subscribe to right now."""
```

實作規則（由高到低優先）：
1. **手動 override** — `config/smart_money/whitelist_manual.yaml`（gitignored），內含 `include: [...]` 與 `exclude: [...]`，立即生效，不須重跑 ranking
2. **最新 ranking top N** — `sm_rankings` 中 `snapshot_date = 最新`、`rank <= whitelist_size`
3. **Freshness 過濾** — 若該 wallet 最近 14 天 HL 上沒 fill（透過 `sm_wallet_trades.ts` 檢查），自動降級為 **watch only**（不跟單但仍 subscribe，觀察是否復活）

### 8.2 Refresh 頻率

- **Ranking snapshot**：每週一 01:00 UTC 跑 cli/rank（cron），覆蓋過去 180 天資料
- **Whitelist 熱載**：daemon 每 6 小時 reload 一次 `whitelist.py`；被移除的 wallet 立即停止 subscribe、但**不自動平倉**（已開部位正常依 exit_mode 收尾）
- **新進 wallet** 的 warm-up：新入 top 10 的 wallet **先 shadow 觀察 48h**（只記 paper trade），確認行為穩定後才進 live 跟單。這避免「上週剛拉高分」的偶然新貴把真錢拖下水

### 8.3 降級／復位

- 被 demoted 的 wallet（因 freshness 或手動 exclude）：既有訊號**不再生效**，但已開的 follow position 維持；完全出場後該 wallet 不再觸發
- `G7 DailyLossCB` 觸發後：整個白名單切 shadow 模式 24h；隔日自動回 live；**若連續 3 天觸發，alert + 人工介入**

---

## 9. 資料模型擴充

### 9.1 新增 Supabase 表（migration 015）

```sql
-- 每個 (wallet, symbol) 的當前持倉狀態（classifier 狀態機的持久層）
CREATE TABLE sm_wallet_positions (
    wallet_id BIGINT REFERENCES sm_wallets(id),
    symbol TEXT NOT NULL,
    side TEXT CHECK (side IN ('long', 'short', 'flat')),
    size NUMERIC NOT NULL,
    avg_entry_px NUMERIC,
    last_updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (wallet_id, symbol)
);

-- 被 guards 拒單或因 symbol 不支援而跳過的訊號（可觀測性）
CREATE TABLE sm_skipped_signals (
    id BIGSERIAL PRIMARY KEY,
    wallet_id BIGINT REFERENCES sm_wallets(id),
    symbol_hl TEXT NOT NULL,
    reason TEXT NOT NULL,        -- below_min_size / symbol_unsupported / latency_exceeded / ...
    signal_latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON sm_skipped_signals(created_at DESC);
CREATE INDEX ON sm_skipped_signals(reason);
```

### 9.2 擴充既有表

```sql
ALTER TABLE sm_paper_trades
    ADD COLUMN network_latency_ms INTEGER,
    ADD COLUMN processing_latency_ms INTEGER,
    ADD COLUMN signal_mode TEXT,        -- independent / aggregated
    ADD COLUMN exit_mode TEXT,          -- mirror / smart / hybrid
    ADD COLUMN source_wallets BIGINT[]; -- aggregated 模式下的多來源

ALTER TABLE sm_live_trades
    ADD COLUMN network_latency_ms INTEGER,
    ADD COLUMN processing_latency_ms INTEGER,
    ADD COLUMN client_order_id TEXT UNIQUE,   -- 冪等鍵
    ADD COLUMN exit_reason TEXT;              -- whale_close / sl_hit / daily_loss_cb / manual
```

---

## 10. 可觀測性

### 10.1 即時面板（補 `DataHealthPanel`）

新增 `SmartMoneySignalPanel` 顯示：
- 當前 WS 連線狀態 / 上次斷線時間
- 訂閱中的 wallet 數
- 過去 1h / 24h latency 分佈（p50 / p95 / p99）
- 今日跳過訊號數（by reason 分桶）
- 當前 mode（shadow / live）+ 觸發原因（若剛被 CB 降級）

API 端點：`/api/smart-money/signal-health` — 從 `sm_skipped_signals` + `sm_paper_trades` 即時聚合。

### 10.2 Telegram 推送（沿用現有 bot）

- **訊號**：新訊號 fire 時推（含 latency）
- **下單**：live 模式每筆下單成功推
- **守門 deny**：每次 G5/G7/G8 觸發推（高重要性）
- **日結**：每天 UTC 00:00 推當日 PnL + 錢包貢獻 breakdown

---

## 11. 分階段實作路徑

每小 phase **獨立可驗收、可回滾**。時間估算以人天（focused work）。

### P4a — 訊號捕捉（5 人天）
- `scanner/realtime.py` WS daemon（含斷線重連 exponential backoff）
- `scanner/reconciler.py` REST 對帳
- `signals/dispatcher.py` 打三個 timestamp 入 queue
- **驗收**：連 72h 不中斷；reconciler 抓到的補單 < 0.1% 總訊號

### P4b — 訊號辨識 + 白名單（4 人天）
- `signals/classifier.py` 狀態機 + `sm_wallet_positions` 持久層
- `signals/whitelist.py` + manual override yaml + freshness 降級
- **驗收**：已知 case（鯨魚反手單、scale up/down）分類正確；classifier 單元測試 20+

### P4c — 訊號聚合 + Shadow 模擬（3 人天）
- `signals/aggregator.py` 兩模式（independent / aggregated）
- `shadow/simulator.py` paper trade 落 DB
- **驗收**：兩模式並行 14 天，累積 ≥ 200 條 paper trade

### P4 Gate（硬性檢查，不過不得進 P5）
- `total_latency_ms` p95 < 10s、p99 < 20s
- WS daemon uptime ≥ 99.5%
- classifier 狀態機自動回放 14 天 fills 結果與 `sm_wallet_positions` 即時值一致
- Paper trade 14 天累積 PnL > 0（不硬性要求，但 < -5% 需人工檢討）

### P5a — Symbol/Size 映射 + Guards（4 人天）
- `execution/mapper.py` + symbol_map.yaml + 啟動驗證
- `execution/guards.py` 7 道守門 + unit tests
- **驗收**：live 模式可啟動、所有 deny reason 可在 mock fill 下觸發

### P5b — 訂單執行 + bracket SL（3 人天）
- `execution/order.py` ccxt OKX + clientOrderId 冪等
- bracket SL 下單 + WS 斷線偵測時的保護性 fallback
- **驗收**：dry_run=true 模式下 OKX testnet 打通；clientOrderId 重送不產生 double

### P5c — 出場邏輯 + CB（3 人天）
- `exit_mode=mirror` 實作
- G7/G8 daily loss CB + consecutive loss 降級
- **驗收**：模擬極端市況（-5% in 1h）CB 觸發正確、平倉完整

### P5 Gate（硬性）
- Capital ramp 第一步 `$100`：連續 7 天無意外、無守門 false positive
- Ramp 到 `$300`：連續 14 天 PnL > BTC buy-hold
- Ramp 到 `$600`：連續 21 天 max drawdown < 10%
- Ramp 到 `$1000`：滿足以上所有

### P6 — AI 質性層（預留、非本文件範圍）

---

## 12. 開放性決策（拍板定案）

**定案日期**: 2026-04-24
**方法**: 先選保守 default、14 天 shadow 期間有資料再調；每一項都寫入 config，之後改動成本低。

| # | 決策點 | **定案** | 為什麼 | 之後改動成本 |
|---|---|---|---|---|
| D1 | 訊號聚合 default | **`aggregated`**（min_wallets=2, window=300s） | 單鯨魚噪音太大；至少要 2 個同向才下注。shadow 14d 期間同步記 independent 版本做對照（雙軌 sm_paper_trades `signal_mode` 欄位）。 | 低：env `SM_SHADOW_AGGREGATION_MODE` 切換 |
| D2 | 出場模式 default | **`mirror`** | P3 backtest gate 本身就是假設「完全跟隨」，改其他模式等於回測無效。14d 期間額外 shadow 一份 `hybrid` 統計做對照，不動主路徑。 | 中：換模式要重跑 P3 gate 才能動真錢 |
| D3 | Warm-up 窗期 | **48h** | 新入 top-N 的 wallet 短期常見拉高分又回落；48h 足以驗證是否穩態。保持 config 可調。 | 低：env `SM_SHADOW_WARMUP_HOURS` |
| D4 | Correlation 判定 | **硬編碼分群**（v1） | 簡單、可審。初版只需防「BTC/ETH 三倍曝險」這類明顯過度集中；動態 Pearson 留 P5 v2 數據足夠後升級。 | 中：改演算法需 guards 單元測試重跑 |
| D5 | OKX 下單 type | **market**（v1） | 優先驗證端到端通暢；market 成交率 100%，滑點在 `$1000` 規模可接受。P5 v2 再換 IOC limit (`mid ± 0.1%`)。 | 低：order.py 單點切換 |
| D6 | 反手單處理 | **先平後開（兩步）** | OKX one-way mode 已設定；two-step 邏輯清晰、幂等鍵獨立。hedge mode 複雜度不划算於 `$1000` 規模。 | 高：要切 OKX 帳戶 position mode |
| D7 | Daily CB 恢復 | **自動 24h**，但**連續 3 天**觸發 → 切 shadow 需**人工解鎖** | 單日小意外自動復原；連續 3 天是「策略失靈」訊號，不該自動回去。 | 低：config 參數 |
| D8 | Freshness 門檻 | **7 天** 無 fill 即降級（watch-only） | 14d 太鬆、30d 根本不過濾；7d 對 top-10 鯨魚正常活躍度剛好。已在 shadow runtime 生效。 | 低：env `SM_SHADOW_FRESHNESS_DAYS` |

### 關鍵推論

- **D1 + D2 同時蒐集雙軌資料** — shadow 14 天結束後可比較 `independent vs aggregated` × `mirror vs hybrid` 四種組合（實作上只需在 `sm_paper_trades.signal_mode` + 新欄位 `exit_mode` 各加標籤，simulator 一次算兩份）。
- **D6 先平後開** 要在 OKX 帳戶啟用 `one-way mode`（而非 hedge mode）；部署 P5 前先手動設定或在 daemon 啟動時 `set_position_mode('net')`。
- **D7 人工解鎖** 的訊號送 Telegram alert（`[SM] 連續 3 日虧損，已切 shadow — 人工確認 /resume_live`）。

---

## 13. 與現有系統的介接

- **`guards/pipeline.py`**（現有 Freqtrade 用）：**不共用**。smart_money 自建 guards（邏輯不同：現有 guards 是 RSI / cooldown / ATR 類；SM 的是 exposure / latency 類）。但借鑒其 `GuardPipeline` 基類設計。
- **Freqtrade 容器**：與 smart_money daemon **完全隔離**（不同 docker service、不同 DB 表、不同 OKX API key sub-account 建議隔離）。兩邊同時跑不衝突。
- **Telegram bot**：共用同一個 bot token、但 SM 訊息前綴加 `[SM]` 以便區分。
- **Dashboard `/trades`**：現顯示 Freqtrade；新增 `/smart-money/live` 獨立頁面顯示 SM 即時狀態。

---

## 14. 失敗模式與回滾

| 失敗 | 偵測 | 自動動作 | 人工介入 |
|---|---|---|---|
| WS 斷線 > 5 分鐘 | realtime heartbeat | reconnect 重試；reconciler 接手補單 | 若 > 30min 推 Telegram alert |
| OKX API 認證失敗 | first order 500 | 暫停下單 + alert | 檢查 API key |
| Latency p95 > 15s | 每 10min 檢查 | 自動切 shadow 模式 | 檢查網路 / HL 服務狀態 |
| classifier 狀態與 HL 真實持倉不一致 | 每小時對帳 | 以 HL 真實為準重建 state | 分析 drift 原因 |
| SL 未觸發但市場已跌破 | 每 15s 檢查 mid price vs SL | 市價強平 + alert | 事後檢查 OKX bracket 是否被吃掉 |

**終極 kill switch**：`smart_money/cli/kill.py` — 一鍵平倉所有未平倉、停止 daemon、切 shadow mode。使用者 Telegram 指令可觸發。

---

## 附錄 A：關鍵資料結構

```python
# smart_money/signals/types.py

@dataclass(frozen=True)
class Signal:
    """Classifier 輸出，描述白名單 wallet 發生了什麼。"""
    wallet_id: int
    wallet_address: str
    wallet_score: float              # 來自 sm_rankings
    symbol_hl: str
    signal_type: SignalType          # OPEN_LONG / CLOSE_LONG / SCALE_UP_LONG / ...
    size_delta: float                # 鯨魚持倉變化量（絕對值）
    px: float                        # fill 價
    whale_equity_usd: float          # 觸發時鯨魚帳戶 equity
    whale_position_usd: float        # 觸發後鯨魚在此 symbol 的部位 USD 值
    ts_hl_fill: int                  # epoch ms
    ts_ws_received: int
    ts_queue_processed: int

    @property
    def total_latency_ms(self) -> int:
        return self.ts_queue_processed - self.ts_hl_fill


@dataclass(frozen=True)
class FollowOrder:
    """Aggregator 輸出，準備送進 execution 層的指令。"""
    symbol_okx: str
    side: Literal["buy", "sell"]
    action: Literal["open", "close", "scale"]
    size_coin: float                 # 已完成 OKX 單位轉換
    size_notional_usd: float
    source_signals: list[Signal]     # 用於守門 + audit
    client_order_id: str             # 冪等鍵
    created_ts_ms: int
```

---

## 附錄 B：設定新增

```python
# smart_money/config.py 新增

class SignalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SM_SIGNAL_", extra="ignore")

    aggregation_mode: Literal["independent", "aggregated"] = "aggregated"
    aggregation_window_sec: int = 300
    min_wallets_for_signal: int = 2
    wallet_score_baseline: float = 0.6

    ws_reconnect_backoff_max_sec: int = 60
    reconciler_interval_sec: int = 60
    reconciler_lookback_sec: int = 300


class ExecutionSettings(BaseSettings):
    # ... 原有欄位 ...

    # 新增
    exit_mode: Literal["mirror", "smart", "hybrid"] = "mirror"
    smart_exit_timeout_hours: int = 48
    smart_exit_profit_threshold: float = 0.015

    global_exposure_cap_mult: float = 1.5       # 全域曝險上限 = mult × capital
    sl_distance_pct: float = 0.02               # 進場後 bracket SL 的百分比
    warmup_hours_for_new_wallet: int = 48       # 新白名單 warm-up 期間

    order_type: Literal["market", "limit_ioc"] = "market"
    limit_ioc_slippage_pct: float = 0.001       # order_type=limit_ioc 時才用
```
