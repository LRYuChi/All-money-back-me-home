# AI + 多市場複合式交易系統 — 完整路線圖

**起草日期**: 2026-04-25
**版本**: v1.0
**狀態**: 規劃（未確認）
**目標**: 從「鯨魚跟單 + Supertrend niche」演進為「AI 輔助 + 多市場 + 複合式自動交易」完整系統

**參考專案**:
- [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) (1835★) — 架構模式參考（Pending Orders / Reflection / Multi-exchange / Notifier）
- [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) (21K★, AAAI 2026) — K 線預測 foundation model（4M-499M 參數、HuggingFace hosted）

**前置文件**:
- [`SMART_MONEY_MIGRATION.md`](SMART_MONEY_MIGRATION.md) — 現行 SM 7 階段
- [`SMART_MONEY_SIGNAL_EXECUTION.md`](SMART_MONEY_SIGNAL_EXECUTION.md) — P4/P5 訊號細節
- [`QUANTDINGER_REFERENCE_PLAN.md`](QUANTDINGER_REFERENCE_PLAN.md) — QD 借鑒 10 項

---

## 📊 執行進度 Tracker

> `/loop 30m` 自動推進中（cron `7,37 * * * *`，job `07147c80`）。每輪完成一個 sub-task、更新此表。

### 狀態圖例

- ⬜ Not started
- 🟡 In progress
- ✅ Done
- ⏸ Blocked / 等待手動介入（標註原因）
- 🔁 Deferred（時機未到）

### Phase 進度

| Phase | 狀態 | 當前 sub-task | 備註 |
|---|---|---|---|
| **A. SM P4/P5 收斂** | 🟡 | P4a-c ✅；**Notifier 抽象 ✅ (QD P0-3)**；P4 Gate 🔁 等 14d shadow；P5a-c ⬜ | migration 015 ⏸ 手動 |
| **B. 基礎設施** | ✅ (基本完成) | UniversalSignal ✅；signal_history migration ✅；SM adapter ✅；dual-write ✅；persistence helper ✅；Notifier 抽象 ✅；Reflection validator core ✅；Supabase/PG IO + CLI ✅；Strategy Snapshot ✅；Credential 加密 ✅；Redis ⬜（暫緩，動 prod 部署） | 144 新 tests 全綠 |
| **C. Kronos 整合** | 🟡 | **HL PriceFetcher ✅（reflection 真能用）**；Kronos predictor / signal converter / dashboard ⬜ | 需 R1 拍板才能進 Kronos 預測 |
| **D. AI + 融合** | ✅ basic | Regime ✅；SignalFuser ✅；**MarketContext provider (HL daily 200d + MA200/slope/vol/DD + 可選 VIX + TTL cache) ✅**；AI LLM 整合 ⬜ | R2 (LLM 供應) 仍待拍板 |
| **E. 策略 DSL** | ✅ basic | DSL ✅；evaluator ✅；registry ✅；首個 prod 策略 ✅；e2e 整合 ✅；StrategyRuntime + daemon wiring ✅；**daemon 接真實 regime ✅**；dashboard / 多策略 ⬜ | rule-only 鏈路 daemon 內全程可跑 |
| **F. 跨市場** | 🟡 | Pending Orders middleware ✅；**Worker (claim → dispatch → mark + LogOnlyDispatcher + CLI + async run_forever) ✅**；OKX live adapter / IBKR / TW broker ⬜ | QD P0-1+pending_order_worker 借鑒；shadow/notify mode 全循環可跑 |
| **G. 風險統一** | ✅ basic | GuardPipeline + 7 guards ✅；Worker 接 pipeline ✅；PnL aggregator × 4 ✅；ExposureProvider × 4 + cli `--with-guards` ✅；daily_pnl_history + G9 ConsecutiveLossDays ✅；**SignalAgeProvider × 4 (lookups fused_signals.ts，per-id cache，fail-open) → G1 真實啟用 ✅**；G2/G7/G10 ⬜ | 7/10 guards 完整 + production wiring；G1 從 stub 升級為真實 latency check |
| **H. Live ramp** | ⬜ | — | — |

### ⏸ 待手動介入清單（loop 自動跳過）

1. **Migration 015 apply** — Supabase Dashboard SQL Editor 手動貼 `supabase/migrations/015_smart_money_positions.sql`
2. **R1-R10 開放性決策** — §15 的 10 個選項未拍板；loop 用建議預設值繼續、拍板後回頭校正
3. **P4 Gate 驗收** — 需 14 天 shadow 真實資料，loop 定期檢查 `signal-health`，足量後跑驗收

### 歷史 log（本輪起）

| 時間 | 輪次 | 任務 | 狀態 |
|---|---|---|---|
| 2026-04-25 00:20 UTC | #1 | Phase B — UniversalSignal types + adapters + signal_history migration + 33 tests | ✅ 完成 |
| 2026-04-25 00:50 UTC | #2 | Phase B — history writer 4 實作 + SM daemon dual-write + 12 tests | ✅ 完成 |
| 2026-04-25 01:37 UTC | #3 | QD P0-3 — Notifier 抽象 (Telegram + MultiChannel + factory + 15 tests) | ✅ 完成 |
| 2026-04-25 02:07 UTC | #4 | Phase B — Reflection validator core (verdict 矩陣 + InMemoryPriceFetcher + 32 tests) | ✅ 完成 |
| 2026-04-25 02:37 UTC | #5 | Phase B — Supabase/Postgres reader+updater + CLI (cron-ready) + 11 tests | ✅ 完成 |
| 2026-04-25 03:07 UTC | #6 | QD P1-4 — Strategy Snapshot (migration 017 + writer × 4 + git provenance + SM backtest CLI 接入 + 16 tests) | ✅ 完成 |
| 2026-04-25 03:37 UTC | #7 | QD P2-8 — Credential 加密 (Fernet + 4 stores + key rotation grace + migration 018 + gen_key CLI + 25 tests) | ✅ 完成 |
| 2026-04-25 04:07 UTC | #8 | Phase C 起步 — HLPriceFetcher (HL candles_snapshot + interval picker + cache + symbol parser + 20 tests) + validate CLI 接入 | ✅ 完成 |
| 2026-04-25 04:37 UTC | #9 | Phase E 起步 — Strategy DSL (YAML schema + minimal predicate parser + evaluator + 51 tests) | ✅ 完成 |
| 2026-04-25 05:07 UTC | #10 | Phase E 續 — evaluator (long/short/conflict/3 sizing 方法/Kelly/exit) + registry × 4 + migration 019 + 29 tests | ✅ 完成 |
| 2026-04-25 05:37 UTC | #11 | Phase D 起點 — Regime detector (CRISIS / BULL × 2 / BEAR × 2 / SIDEWAYS × 2 + UNKNOWN, pure rules, 24 tests) | ✅ 完成 |
| 2026-04-25 06:07 UTC | #12 | Phase D 續 — SignalFuser (regime × source 加權 + 衝突偵測 + 過期降權 + weights yaml loader + 21 tests) | ✅ 完成 |
| 2026-04-25 06:37 UTC | #13 | E2E 整合 — 首個 prod strategy YAML (crypto_btc_smart_money_v1) + 端到端整合測試 (SM→universal→regime→fuse→strategy→intent, 9 cases) | ✅ 完成 |
| 2026-04-25 07:07 UTC | #14 | Daemon wiring — StrategyRuntime (thread-safe ingest + tick eval + stats) + shadow daemon hook (--strategies flag) + 16 tests | ✅ 完成 |
| 2026-04-25 07:37 UTC | #15 | Phase D 收尾 — MarketContextProvider (Static + Cached + HLBTC + yfinance VIX) + shadow daemon `--real-market-context` + 18 tests | ✅ 完成 |
| 2026-04-25 08:07 UTC | #16 | Phase F 起點 — Pending Orders queue × 4 + dispatcher + idempotency + state machine + migration 020 + StrategyRuntime 接 + 35 tests | ✅ 完成 |
| 2026-04-25 08:37 UTC | #17 | Phase F 續 — Pending Orders Worker (claim/dispatch/terminal + LogOnly dispatcher + async run_forever + CLI + 14 tests) | ✅ 完成 |
| 2026-04-25 09:07 UTC | #18 | Phase G 起點 — Risk Guard pipeline + 5 builtin guards (latency / min_size / strategy_exposure / market_exposure / global_exposure) + scale-mutate semantics + 24 tests | ✅ 完成 |
| 2026-04-25 09:37 UTC | #19 | Phase G 續 — Worker 接 GuardPipeline (DENY 短路 / SCALE mutate / context_provider / pipeline-crash fail-safe / 8 stats counters) + 10 tests | ✅ 完成 |
| (manual) | #20 | Phase G 續 — PnL aggregator × 4 + G8 DailyLossCircuitBreaker (UTC midnight boundary / fail-open on agg error / pipeline integration) + 16 tests | ✅ 完成 |
| (manual) | #21 | Phase G 續 — ExposureProvider × 4 (sm_paper_trades + live_trades 開倉聚合 by strategy/market/global) + make_context_provider + cli/work.py `--with-guards` + 18 tests | ✅ 完成 |
| (manual) | #22 | Phase G 續 — daily_pnl_history (per-day buckets across InMemory/Supabase/Postgres) + G9 ConsecutiveLossDaysGuard (3-day default, insufficient-history → ALLOW, fail-open, integrates after G8) + cli/work.py `--consecutive-loss-days` + 21 tests | ✅ 完成 |
| (manual) | #23 | Phase G 續 — SignalAgeProvider × 4 (fused_signals.ts 查表 + per-id cache + Z/+00:00 ISO parsing + fail-open) + cli/work.py 接 build_signal_age_provider → G1 LatencyBudgetGuard 真實啟用 + 30 tests | ✅ 完成 |
| — | — | **下輪待辦**：Audit log hook (round 7 leftover) OR multi-exchange dispatcher 註冊表 (F.1 框架) OR G7 CorrelationCap 雛形 OR Kronos (R1) | ⬜ |

---

## 目錄

1. [願景與最終目標](#1-願景與最終目標)
2. [當前狀態盤點](#2-當前狀態盤點)
3. [目標系統架構（7 層）](#3-目標系統架構)
4. [Layer 1 — 資料層](#4-layer-1--資料層)
5. [Layer 2 — 訊號生成層（5 源）](#5-layer-2--訊號生成層)
6. [Layer 3 — 融合與 Regime 調權](#6-layer-3--融合與-regime-調權)
7. [Layer 4 — 複合式策略層](#7-layer-4--複合式策略層)
8. [Layer 5 — 風險與部位管理](#8-layer-5--風險與部位管理)
9. [Layer 6 — 統一執行層](#9-layer-6--統一執行層)
10. [Layer 7 — 觀測與回饋迴圈](#10-layer-7--觀測與回饋迴圈)
11. [分階段路線圖（8 phase）](#11-分階段路線圖)
12. [環環相扣的依賴圖](#12-環環相扣的依賴圖)
13. [成功指標](#13-成功指標)
14. [風險與應對](#14-風險與應對)
15. [開放性決策](#15-開放性決策)

---

## 1. 願景與最終目標

### 1.1 核心命題

「讓一個人能自己操作**多市場的複合式自動交易系統**，同時擁有：
- **預測力**（Kronos 機率性路徑 + AI 質性分析）
- **情報力**（鯨魚跟單 + 宏觀/新聞 + 預測市場）
- **紀律**（Regime-aware ensemble + 多道守門 + 自動 CB）
- **可觀測**（每個決策可追溯、每個權重可校準）」

### 1.2 最終狀態（"complete form"）

12 個月後，系統能做到：

| 能力 | 描述 |
|---|---|
| 多市場覆蓋 | Crypto 永續（OKX + 可擴充 Binance/Bybit） / US 股票（盤中 ETF + 個股） / TW 股票（期貨/個股） / Polymarket（已有）|
| 多時間尺度 | 15m / 1h / 4h / 1d 四尺度並行，不同策略掛不同尺度 |
| 預測層 | Kronos finetuned 模型提供 1h / 4h / 1d 未來價格**機率分佈**（不是單點預測）|
| 訊號源 × 5 | Kronos 預測、Smart Money 跟單、TA 傳統（Supertrend）、AI 質性、宏觀/情緒 |
| 融合層 | Regime（bull/bear/range）×  confidence 動態調 5 源權重 |
| 複合策略 | 可在 dashboard 定義：「當 Kronos p95 看漲 AND SM aggregated long AND regime ≠ bear → 進場」|
| 執行層 | pending_orders → workers → exchange adapters 統一管線（shadow/paper/live 三模式）|
| AI 校準 | 每週 reflection loop 自動驗證所有訊號源歷史預測準確性，回填 weight tuning 建議 |
| 可觀測 | 每個訊號、每個決策、每筆單都在 dashboard 可視化；每個守門決策有 audit log |
| 安全 | API key 加密儲存；日虧 CB + 連虧 3 日自動切 shadow + Telegram 人工解鎖 |

### 1.3 不做的事（避免失焦）

- ❌ Multi-user / billing / OAuth（personal 工具）
- ❌ Web3 錢包整合 / DEX 直連（交易走 CEX）
- ❌ 從零訓練 ML 模型（用 Kronos pretrained + finetune）
- ❌ 超高頻（HFT）策略（最短尺度 15m）
- ❌ 選擇權 / 複雜衍生品（永續 + 現貨 + 股票）

---

## 2. 當前狀態盤點

### 2.1 已有資產（可繼續沿用）

| 模組 | 狀態 | 如何融入新架構 |
|---|---|---|
| `smart_money/scanner/` + `ranking/` + `backtest/` | ✅ P0-P3 完整 | 變成 Layer 2 的「SM 訊號源」 |
| `smart_money/signals/` (P4a-c) | ✅ 新完成 | 維持為 SM 實時訊號管線 |
| `smart_money/shadow/simulator` | ✅ 新完成 | 併入 Layer 6 統一執行層（其中一個 mode）|
| `market_monitor/confidence_engine.py` | ✅ 4 沙箱（Macro/Sentiment/Capital/Haven）| 升級為 Layer 3 融合層的 regime 判定器 |
| `strategies/supertrend.py` | ✅ Freqtrade 運行中 | 變成 Layer 2 的「TA 訊號源」|
| `polymarket/` | ✅ 獨立完整 | 變成 Layer 2 的「預測市場情報源」 |
| `apps/api/` FastAPI | ✅ | 擴充為整個系統的 API gateway |
| `apps/web/` Next.js | ✅ | 擴充為整合式 dashboard |
| `trading_log/` | ✅ git-based | 保留、擴為所有市場的 audit trail |
| Pre-push hook + migration fallback | ✅ 剛加 | 維持 CI/CD 可靠性 |

### 2.2 要補的東西（按 layer）

| Layer | 現況 | 缺口 |
|---|---|---|
| L1 資料 | yfinance + HL + OKX + TWSE + FRED | 缺統一 cache（Redis）、缺 normalize schema、缺 US 分鐘資料 |
| L2 訊號 | SM + TA + Polymarket | 缺 Kronos 預測、缺 AI 質性分析、訊號規格不統一 |
| L3 融合 | 各自獨立 | 缺 ensemble、缺 regime-aware 調權、缺 conflict resolution |
| L4 策略 | Supertrend 1 個策略寫死 | 缺 DSL / 設定化策略定義、缺多策略並行管理 |
| L5 風險 | 散在 Freqtrade guards + SM execution guards | 缺統一 risk layer、缺跨市場相關性、缺 Kelly |
| L6 執行 | Freqtrade (crypto) + SM simulator | 缺 pending_orders、缺多 exchange factory、缺股票 broker |
| L7 觀測 | 有 signal-health 基礎 | 缺 reflection loop、缺 calibration、缺跨市場 unified dashboard |

---

## 3. 目標系統架構

### 3.1 七層堆疊圖

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      L7  OBSERVABILITY & FEEDBACK                           │
│   Unified Dashboard · Reflection Loop · AI Calibration · Alerts             │
│                         (prom-style metrics + audit)                        │
└────────────────┬──────────────────────────────────────┬─────────────────────┘
                 │                                      │
                 │ metrics + validation                 │ learned weights
                 ▼                                      ▲
┌─────────────────────────────────────────────────────────────────────────────┐
│                      L6  UNIFIED EXECUTION                                  │
│   pending_orders table ─► workers ─► exchange adapters (OKX/Binance/IBKR)   │
│                          ├─► shadow mode (paper trades)                     │
│                          ├─► live mode (real orders)                        │
│                          └─► notify-only mode                               │
└────────────────▲──────────────────────────────────────▲─────────────────────┘
                 │                                      │
                 │ FollowOrder (unified)                │
┌────────────────┴──────────────────────────────────────┴─────────────────────┐
│                      L5  RISK & POSITION SIZING                             │
│   Exposure caps · Correlation matrix · Kelly criterion · Capital ramp       │
│   Daily-loss CB · Consecutive-loss CB · Circuit breakers                    │
└────────────────▲────────────────────────────────────────────────────────────┘
                 │ sized intents
┌────────────────┴────────────────────────────────────────────────────────────┐
│                      L4  COMPOSITE STRATEGY                                 │
│   strategy DSL · per-market deck · horizon routing · gating rules           │
│   (e.g. "if regime=bull & kronos_p95_up & sm_aggregated_long then enter")   │
└────────────────▲────────────────────────────────────────────────────────────┘
                 │ ensemble_score + direction
┌────────────────┴────────────────────────────────────────────────────────────┐
│                      L3  FUSION & REGIME                                    │
│   regime detector (bull/bear/range) · confidence engine (upgraded)          │
│   weight matrix (per regime × per horizon) · conflict resolver              │
└─┬──────┬──────┬──────┬──────┬──────────────────────────────────────────────┘
  │      │      │      │      │   5 signal streams (standardized Signal schema)
  │      │      │      │      │
┌─▼──┐ ┌─▼──┐ ┌─▼──┐ ┌─▼──┐ ┌─▼──┐
│ KRO│ │ SM │ │ TA │ │ AI │ │MAC │              L2  SIGNAL GENERATION
│NOS │ │    │ │    │ │LLM │ │ RO │
└─▲──┘ └─▲──┘ └─▲──┘ └─▲──┘ └─▲──┘
  │      │      │      │      │
┌─┴──────┴──────┴──────┴──────┴──────────────────────────────────────────────┐
│                      L1  DATA PLANE                                        │
│   OHLCV (multi-market) · Order flow · News · Macro · On-chain · Polymarket │
│   normalized schema · Redis cache · Supabase persistence                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 核心原則（每一層都遵守）

1. **訊號不是決策**：L2 輸出 `Signal(source, symbol, direction, strength, horizon, confidence)`，不直接下單
2. **融合不是平均**：L3 根據 regime + historical accuracy 動態加權，不等權平均
3. **策略是配置化**：L4 用 YAML / DB 存策略定義，修策略不改 code
4. **風險是 veto 權**：L5 能否決任何 L4 決策（不管多強的 signal），回寫 audit
5. **執行是異步的**：L6 所有操作過 pending_orders，可 retry / pause / cancel
6. **回饋是閉環的**：L7 每週驗證 L2-L5 的預測 vs 實際，產出 calibration 建議

---

## 4. Layer 1 — 資料層

### 4.1 統一資料 schema

所有市場資料 normalize 成單一 OHLCV schema：

```python
@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str                 # canonical: "crypto:OKX:BTC/USDT:USDT" / "us:NASDAQ:AAPL" / "tw:TPE:2330"
    timeframe: str              # "1m" / "5m" / "15m" / "1h" / "4h" / "1d"
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_open: datetime           # bar 開始時間（UTC）
    ts_close: datetime
    source: str                 # 資料提供者
```

### 4.2 資料提供者 factory

```
shared/data/
├── base.py           # DataProvider Protocol
├── factory.py        # create(market, tf) → DataProvider
├── crypto_okx.py     # 現有 freqtrade 模式
├── crypto_hl.py      # 給 Kronos finetune 用 HL 資料
├── us_polygon.py     # Polygon.io (盤中) — 付費但最穩
├── us_yfinance.py    # 免費但 15min 延遲（Kronos 用可接受）
├── tw_twse.py        # 現有 market_monitor 模式
├── fx_oanda.py       # (未來)
└── macro_fred.py     # 現有
```

### 4.3 Redis cache 策略

- **hot cache** (30s TTL): 最新 bar
- **warm cache** (5min TTL): 最近 N 根 bar 供 Kronos inference
- **cold cache** (disabled): 歷史訓練資料走 Supabase/Parquet

### 4.4 Supabase 表擴充

```sql
-- 統一 bars 表（替換散落各處的 csv）
CREATE TABLE market_bars (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts_open TIMESTAMPTZ NOT NULL,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume NUMERIC,
  source TEXT,
  PRIMARY KEY (symbol, timeframe, ts_open)
);
CREATE INDEX ON market_bars (symbol, ts_open DESC);
```

---

## 5. Layer 2 — 訊號生成層

### 5.1 統一 Signal schema

```python
@dataclass(frozen=True, slots=True)
class UniversalSignal:
    source: Literal["kronos", "smart_money", "ta", "ai_llm", "macro"]
    symbol: str
    horizon: Literal["15m", "1h", "4h", "1d"]
    direction: Literal["long", "short", "neutral"]
    strength: float             # 0 ~ 1, source-internal confidence
    reason: str                 # human-readable
    details: dict               # source-specific payload
    ts: datetime
    expires_at: datetime        # 訊號過期時間（horizon 的 1-2 倍）
```

每個訊號源寫 `Signal`，不寫訂單。融合層統一消費。

### 5.2 Source A：Kronos 預測

#### 5.2.1 使用模式

Kronos 是 decoder-only autoregressive Transformer，輸入過去 N 根 K 線，**機率性取樣**未來 M 根。關鍵：同一輸入可以取樣多條路徑（`sample_count=N`），產出**未來分佈**而非單點。

#### 5.2.2 模組佈局

```
kronos_layer/
├── __init__.py
├── predictor.py          # wrapper on NeoQuasar/Kronos-small
├── tokenizer.py          # wrapper on KronosTokenizer
├── finetune/             # 各市場 finetune scripts
│   ├── crypto.py         # on HL + OKX bars
│   ├── us_stocks.py      # on SPY + individual
│   └── tw_stocks.py      # on 2330 + 0050
├── signals.py            # convert forecast → UniversalSignal
├── cache.py              # cache forecasts (expensive, 10-30s per call)
└── cli/
    └── forecast.py       # on-demand 預測 CLI
```

#### 5.2.3 訊號轉換邏輯

```python
def forecast_to_signal(
    symbol: str,
    horizon: str,
    pred_df: pd.DataFrame,      # 來自 KronosPredictor.predict
    sample_count: int = 30,     # 取樣 30 條路徑
) -> UniversalSignal:
    """從 Kronos 機率分佈萃取方向 + 強度。"""
    # 每條路徑最終 close 相對當前的漲跌幅
    returns = (pred_df['close'].iloc[-1] / pred_df['close'].iloc[0]) - 1
    # 聚合為分佈
    p5, p50, p95 = np.percentile(returns_all_paths, [5, 50, 95])

    # direction: 看 p50 方向，但要求 p5 也同向（保守）
    if p50 > 0.002 and p5 > -0.005:
        direction = "long"
    elif p50 < -0.002 and p95 < 0.005:
        direction = "short"
    else:
        direction = "neutral"

    # strength: 用 |p50| + consistency ratio
    consistency = (np.sign(returns_all_paths) == np.sign(p50)).mean()
    strength = min(1.0, abs(p50) * 10 * consistency)

    return UniversalSignal(
        source="kronos",
        symbol=symbol,
        horizon=horizon,
        direction=direction,
        strength=strength,
        reason=f"median forecast {p50:+.2%}, consistency {consistency:.0%}",
        details={"p5": p5, "p50": p50, "p95": p95, "sample_count": sample_count},
        ts=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(minutes=_horizon_to_min(horizon)),
    )
```

#### 5.2.4 推理效能策略

- Kronos-small（24.7M 參數）走 CPU 就行，推理 ~5-10s
- Kronos-base（102M）建議 GPU；可上 runpod / modal on-demand
- 推理結果 cache 10 分鐘（同 symbol+timeframe+close_ts 重用）
- 排程：每根 15m/1h/4h bar close 後 + 30s（等 candle 確認）就觸發批次預測

### 5.3 Source B：Smart Money（現有，包裝為統一 Signal）

現有 `smart_money/signals/classifier.py` 輸出 `Signal`（P4b 的）→ 加個 adapter 轉成 `UniversalSignal`：

```python
def sm_signal_to_universal(sm: Signal) -> UniversalSignal:
    direction = "long" if sm.signal_type in (OPEN_LONG, SCALE_UP_LONG, REVERSE_TO_LONG) else "short"
    if sm.signal_type in (CLOSE_LONG, CLOSE_SHORT):
        direction = "neutral"  # close 是出場訊號
    return UniversalSignal(
        source="smart_money",
        symbol=_map_hl_to_canonical(sm.symbol_hl),
        horizon="15m",  # SM 實時 fills
        direction=direction,
        strength=sm.wallet_score,  # 鯨魚排名分數
        reason=f"whale {sm.wallet_address[:10]} {sm.signal_type.value}",
        details={"wallet_id": str(sm.wallet_id), "new_size": sm.new_size},
        ts=...,
        expires_at=...,
    )
```

### 5.4 Source C：TA 傳統（Supertrend 等）

現有 `strategies/supertrend.py` 的買賣訊號抽出成 signal publisher：

```python
# 每根 bar 計算完，發 signal 到 queue
def supertrend_on_bar(bar: Bar, state: SupertrendState):
    if crossover_up:
        emit(UniversalSignal(
            source="ta", symbol=bar.symbol, horizon=bar.timeframe,
            direction="long", strength=0.7,  # TA 不會太強
            reason="supertrend bullish crossover", ...
        ))
```

Freqtrade 容器繼續跑，但**新增**一條 signal publisher path 送訊號到 L3（Freqtrade 本身的 entry/exit 也保留做對照組）。

### 5.5 Source D：AI LLM 質性分析

借鑒 QD 的 `fast_analysis.py`：

```
ai_layer/
├── fast_analysis.py      # 單次 LLM call 結構化輸出
├── memory.py             # 歷史分析存 sm_ai_analysis_memory
├── prompts/              # multi-market prompt templates
│   ├── crypto.txt
│   ├── us_stock.txt
│   └── macro_context.txt
└── calibration.py        # (P7) 基於 reflection 校準 threshold
```

輸入：最近 N 根 bar + 相關新聞（CryptoPanic / Finnhub）+ 宏觀快照
輸出：
```json
{
  "decision": "BUY|SELL|HOLD",
  "confidence": 0.72,
  "reasoning": "......",
  "key_factors": ["..."],
  "forecast_1d": {"direction": "up", "target_pct": 1.8}
}
```

轉 `UniversalSignal(source="ai_llm")`。

### 5.6 Source E：Macro + Sentiment

現有 `confidence_engine.py` 升級：
- 不直接回傳 `confidence 0-1`，改回傳多個維度的 `UniversalSignal(source="macro")`
- 每個沙箱（Macro / Sentiment / Capital / Haven）各自成一個 signal
- 例如：`FRED 10Y-2Y yield spread` → `UniversalSignal(source="macro", symbol="*", horizon="1d", direction="short", ...)` 代表衰退訊號、影響所有 asset

---

## 6. Layer 3 — 融合與 Regime 調權

### 6.1 Regime Detector

```python
class RegimeDetector:
    """判斷當前市場狀態，影響 L3 融合權重與 L4 策略選擇。"""

    def detect(self, context: MarketContext) -> Regime:
        """
        Returns: Regime enum
          - BULL_TRENDING
          - BULL_CHOPPY
          - BEAR_TRENDING
          - BEAR_CHOPPY
          - SIDEWAYS_LOW_VOL
          - SIDEWAYS_HIGH_VOL
          - CRISIS  (VIX > 35 或 daily drawdown > 5%)
        """
```

判斷依據：BTC 200MA 斜率、VIX 絕對值、BTC 60d realized vol、SPY trend 等。

### 6.2 權重矩陣（regime × signal source）

```yaml
# config/fusion_weights.yaml
weights:
  BULL_TRENDING:
    kronos: 0.25
    smart_money: 0.30
    ta: 0.25
    ai_llm: 0.10
    macro: 0.10
  BULL_CHOPPY:
    kronos: 0.30          # 預測在 choppy 中更重要
    smart_money: 0.20
    ta: 0.15              # TA 在 choppy 最弱
    ai_llm: 0.15
    macro: 0.20
  BEAR_TRENDING:
    kronos: 0.20
    smart_money: 0.25     # 鯨魚做空時權重低（空方流動性小）
    ta: 0.30              # TA 在趨勢中強
    ai_llm: 0.15
    macro: 0.10
  CRISIS:
    kronos: 0.10          # foundation model 極端市況可能不可靠
    smart_money: 0.15
    ta: 0.20
    ai_llm: 0.20
    macro: 0.35           # 宏觀/新聞主導
```

初始值是人工設；P7 reflection loop 會定期提出調整建議。

### 6.3 融合邏輯

```python
class SignalFuser:
    def fuse(
        self,
        signals: list[UniversalSignal],  # 同一 symbol + horizon 的多源訊號
        regime: Regime,
        weights: dict[str, float],
    ) -> FusedSignal:
        """
        算每個方向的加權總和；回傳 direction + ensemble_score [0,1]
        """
        long_score = sum(w[s.source] * s.strength for s in signals if s.direction == "long")
        short_score = sum(w[s.source] * s.strength for s in signals if s.direction == "short")
        neutral_score = sum(w[s.source] * s.strength for s in signals if s.direction == "neutral")
        net = long_score - short_score

        # 衝突偵測：若 max dir 沒超過次 dir 50% → 標記 conflict，強度打折
        sorted_scores = sorted([long_score, short_score, neutral_score], reverse=True)
        if sorted_scores[0] < sorted_scores[1] * 1.5:
            conflict = True
            ensemble_strength *= 0.5

        return FusedSignal(
            symbol=..., horizon=...,
            direction=...,
            ensemble_score=ensemble_strength,
            contributions={s.source: w[s.source] * s.strength for s in signals},
            conflict=conflict,
            regime=regime,
            sources_count=len(signals),
        )
```

---

## 7. Layer 4 — 複合式策略層

### 7.1 Strategy DSL（YAML 定義）

```yaml
# strategies/crypto_btc_follow_whales_kronos.yaml
id: crypto_btc_follow_whales_kronos_v1
market: crypto
symbol: BTC/USDT:USDT
timeframe: 15m
enabled: true

entry:
  long:
    all_of:
      - fused.direction == "long"
      - fused.ensemble_score >= 0.6
      - fused.conflict == false
    any_of:
      - kronos.p50 > 0.003        # Kronos 看漲至少 0.3%
      - smart_money.count >= 2    # ≥ 2 鯨魚同向
    none_of:
      - macro.crisis == true
      - regime in ["CRISIS", "BEAR_TRENDING"]
  short:
    # 類似結構

position_sizing:
  method: kelly
  kelly_fraction: 0.25           # 1/4 Kelly 保守
  max_size_usd: 500
  max_leverage: 2

exit:
  stop_loss: 0.02                # 2% SL
  take_profit: null              # 不設 TP,由訊號驅動
  exit_on:
    - fused.direction == "short"
    - kronos.p50 < -0.002
    - smart_money.close_signal
  time_stop_hours: 48
```

### 7.2 策略管理

- DB 表 `strategies`：id / yaml_content / enabled / mode(shadow|live)
- Dashboard 可開關、編輯、複製
- 每個策略獨立 pending_orders 流水（便於 attribution）
- 多策略可同時跑、L5 風險層看總 exposure

### 7.3 時間尺度分層

| 尺度 | 用途 | 典型策略 |
|---|---|---|
| 1d | 趨勢判斷、位置倉 | Kronos 主導 + 宏觀確認 |
| 4h | 擺盪/回歸、中線 | TA + AI 質性 |
| 1h | 短線方向 | SM 跟單 + Kronos |
| 15m | 極短進出 | SM 高分鯨魚訊號 |

不同 horizon 策略走同一融合層、但各自的 signal pool 只取對應 horizon 的訊號。

---

## 8. Layer 5 — 風險與部位管理

### 8.1 幾何多道守門（基於 QD + 現有 SM guards）

```
FusedSignal + strategy intent
  → [G1 LatencyBudget]        超過 signal 過期拒
  → [G2 SymbolSupported]      exchange 對照驗證
  → [G3 MinSize]              avoid dust
  → [G4 PerStrategyExposure]  單策略曝險上限
  → [G5 PerMarketExposure]    單市場曝險上限
  → [G6 GlobalExposure]       總資本曝險上限 (1.5×)
  → [G7 CorrelationCap]       相關性分群（L1 / L2 / stable 等）
  → [G8 DailyLossCB]          日虧 5% 暫停
  → [G9 ConsecutiveLossCB]    連虧 3 日 → shadow
  → [G10 KellyPositionSize]   Kelly 倉位計算
  → SizedOrder
```

每個守門都有單元測試 + audit log 寫入 `risk_decisions` 表。

### 8.2 相關性矩陣（動態）

每週五收盤後，用過去 60 天每日收益算所有監控 symbol 的相關性 → 更新 `correlation_matrix` 表。
G7 讀此表判斷「要不要擋下新單」：若 symbol X 與已持倉 Y 相關性 > 0.75 → 視為同一 bucket，bucket 上限 3 個 symbol。

### 8.3 跨市場 Kelly

```python
def kelly_size(
    win_rate: float,          # 歷史勝率
    avg_win: float,
    avg_loss: float,
    capital: float,
    fraction: float = 0.25,   # 分數 Kelly 保守
) -> float:
    b = avg_win / avg_loss
    f = win_rate - (1 - win_rate) / b
    return max(0, capital * fraction * f)
```

`win_rate / avg_win / avg_loss` 每策略分別維護，來自 L7 reflection。

---

## 9. Layer 6 — 統一執行層

### 9.1 pending_orders 中間層（取代直連）

```
L5 outputs SizedOrder
  → INSERT INTO pending_orders (strategy_id, mode, payload, status='pending')
  → OrderDispatcher worker poll 表 → route by mode:
       - shadow   → shadow_simulator（寫 sm_paper_trades 或 market-wide 的 paper_trades）
       - live     → exchange adapter → 實單
       - notify   → notifier only（不下單只推）
  → UPDATE status=dispatched / failed / cancelled
```

### 9.2 Multi-exchange adapter factory

```
execution/
├── base.py                     # abstract ExchangeClient
├── factory.py                  # create(name) → client
├── okx/                        # OKX (已整合於 Freqtrade，這裡是統一介面)
│   ├── client.py
│   └── symbol_map.yaml
├── binance/                    # 未來
├── bybit/                      # 未來
├── ibkr/                       # US stocks，用 ib_insync
│   ├── client.py
│   └── symbol_map.yaml
├── tw_stock/                   # TW stocks，用永豐/富邦 API
└── polymarket/                 # 已有
```

每個 client 實作：`place_order` / `cancel_order` / `get_position` / `get_balance` / `fetch_markets`。

### 9.3 Shadow / Paper / Live 三模式

| 模式 | 用途 | DB 表 | 可見性 |
|---|---|---|---|
| `shadow` | 自動紙上跟單（無人工） | `sm_paper_trades` | 儀表板 |
| `paper` | 人工測試策略（dashboard 手動下單） | `paper_trades` | 儀表板 |
| `live` | 真實下單 | `live_trades` + exchange 的 account | 儀表板 + exchange |
| `notify` | 只推 Telegram 不下單 | 無 | Telegram |

Strategy 可設定 `mode: shadow` 跑一陣子再切 `live`。

### 9.4 訂單生命週期

- `pending` → `dispatching` → `submitted` → `partially_filled` / `filled` / `rejected` / `cancelled` / `expired`
- 每次轉態都寫 `order_events` 表（audit）
- Live 模式下 exchange 的 WS 推送 fills 回寫 `live_trades.status`

---

## 10. Layer 7 — 觀測與回饋迴圈

### 10.1 Reflection Loop（借 QD）

每週一 03:00 UTC 跑 `ReflectionCycle`：

1. 找出 7 天前所有 `UniversalSignal`（存在 `signal_history` 表）
2. 算每個訊號的實際 forward return
3. 判斷對錯（direction match + threshold）
4. 寫回 `signal_history.was_correct + actual_return`

### 10.2 AI Calibration（借 QD）

基於上面的對錯資料：

- 對每個 `source × regime × horizon` 算歷史準確率
- 若某源在某 regime 下準確率 < 50% 持續 4 週 → 建議降權
- 若 > 70% 持續 4 週 → 建議升權
- 建議透過 Telegram 推、人工審批後更新 `fusion_weights.yaml`

### 10.3 儀表板

Next.js dashboard 新增頁面（在 `/trades` / `/smart-money` 基礎上擴充）：

```
/markets/overview          跨市場總覽（PnL / exposure / signals today）
/markets/[market]          單市場詳情（crypto / us / tw）
/strategies                策略管理（啟用/停用/編輯 YAML）
/strategies/[id]           策略績效 + 訊號貢獻分析
/signals                   所有訊號流（可按 source 過濾）
/risk                      風險儀表（exposure / correlation / CB 狀態）
/ai-analysis               AI 分析歷史 + 準確率校準
/kronos-forecasts          Kronos 預測視覺化（機率雲）
/reflection                每週校準報告
```

### 10.4 Alerts（經過 Notifier 抽象）

| 事件 | 通道 | 頻率 |
|---|---|---|
| 任何策略進/出場 | Telegram (INFO) | 即時 |
| G6/G7/G8/G9 守門觸發 | Telegram (WARN) | 即時 |
| Daily CB 觸發 | Telegram + Email | 即時 |
| 連 3 日虧損 | Telegram + Email + 需人工解鎖 | 即時 |
| Kronos 模型推理失敗 | Telegram (ERROR) | 即時 |
| 週校準完成 | Telegram (report) | 每週一 |
| 月度 PnL 回顧 | Email (report) | 每月 |

---

## 11. 分階段路線圖

每階段獨立驗收、獨立可回滾。時間估以個人 focused 3-4h/day 計。

### Phase A — Smart Money P4/P5 收斂（1 個月，目前進行中）
**目標**：現有 SM 系統跑完 P4 Gate + P5 ramp 到 $300
- ✅ P4a-c 完成（已推）
- ⏸ migration 015 apply（blocked，待 supabase dashboard 手動）
- [ ] P4 Gate 驗收（14 天 shadow 累積）
- [ ] P5a-c 實作（symbol mapper 擴完整 + guards + order)
- [ ] 借鑒 QD：pending_orders 中間層 + Notifier 抽象（**同步做**，P0-1 + P0-3）

**里程碑 A**：$300 live ramp 連 7 天無意外

### Phase B — 基礎設施強化（1 個月）
**目標**：為後續 AI/Kronos 接入鋪路
- [ ] Redis 加入 compose（queue + cache）
- [ ] Reflection Loop + Calibration schema（借 QD P0-2）
- [ ] Strategy Snapshot 表（借 QD P1-4）
- [ ] Credential 加密儲存（借 QD P2-8）
- [ ] `UniversalSignal` schema + `signal_history` 表
- [ ] SM signals 寫入 `signal_history`（所有歷史訊號可追溯）
- [ ] Data provider factory 骨架 + 現有源 wrap

**里程碑 B**：所有新/舊訊號都以 `UniversalSignal` 格式寫進 `signal_history`；reflection loop 能跑（即使還沒訊號可驗證）

### Phase C — Kronos 整合（1.5 個月）
**目標**：Kronos 作為第一個新訊號源上線
- [ ] `kronos_layer/` 模組：predictor + tokenizer wrapper
- [ ] 部署：下載 Kronos-small / base；on-demand 推理 service（FastAPI 微服務）
- [ ] Crypto forecasting：對 BTC/ETH 1h/4h/1d 訊號
- [ ] Finetune pipeline（用歷史 HL/OKX 資料 finetune）
- [ ] `kronos_forecasts` Supabase 表儲存歷史預測
- [ ] `forecast_to_signal` 轉 `UniversalSignal`
- [ ] Dashboard `/kronos-forecasts` 視覺化機率雲

**里程碑 C**：Kronos BTC 1h 預測**連 30 天** p50 方向準確率 > 55%（比 coin flip 好）

### Phase D — AI 質性層 + 融合層（1.5 個月）
**目標**：5 個訊號源齊備 + Regime-aware 融合
- [ ] `ai_layer/fast_analysis.py`（借 QD）+ memory + prompt templates
- [ ] LLM 多供應商（Anthropic / OpenAI / OpenRouter fallback）
- [ ] `RegimeDetector` 實作（BULL_TRENDING / BEAR / SIDEWAYS / CRISIS）
- [ ] `SignalFuser` 融合邏輯 + `fusion_weights.yaml`
- [ ] `confidence_engine` 升級為 signal source（不再是 engine 而是 source）
- [ ] Supertrend 發 `UniversalSignal`（Freqtrade 保留，訊號雙寫）
- [ ] `fused_signals` Supabase 表（融合後結果寫入供 L4 消費）

**里程碑 D**：任意時刻 dashboard 能看到某 symbol 的 5 源 vs fused decision；融合後 signal 歷史準確率 > 各源獨立最佳者

### Phase E — 策略 DSL + 多策略管理（1 個月）
**目標**：策略變成配置，多策略並行
- [ ] Strategy DSL YAML schema + validator
- [ ] `strategies` DB 表 + CRUD API
- [ ] Strategy engine（讀 DSL → 產 SizedOrder）
- [ ] Dashboard `/strategies` 編輯介面（CodeMirror YAML editor）
- [ ] 首批 3 個策略：
  - `crypto_btc_follow_whales_kronos_v1`（主軸：SM + Kronos）
  - `crypto_alt_regime_ta_v1`（BEAR_CHOPPY 下做空 alt）
  - `macro_crisis_hedge_v1`（CRISIS regime 下買 haven）

**里程碑 E**：3 個策略並行 shadow 跑 14 天，dashboard 完整可視化每個 signal→fused→order 鏈路

### Phase F — 跨市場 + 統一執行（2 個月）
**目標**：US 股票 + TW 股票進場，執行統一
- [ ] IBKR adapter（US stocks）+ 測試帳戶
- [ ] TW 股票 API 串接（永豐或富邦 API）
- [ ] `market_bars` 統一表 + provider factory 全數實現
- [ ] `pending_orders` 表 + dispatcher worker 完整
- [ ] Multi-exchange symbol_map 合併到 execution factory
- [ ] 跨市場策略：
  - `us_stock_spy_kronos_v1`（Kronos 預測 SPY）
  - `tw_2330_ai_confirm_v1`（Supertrend + AI 質性確認）

**里程碑 F**：US/TW/Crypto 三市場能同時跑各自策略、pending_orders 流水完整

### Phase G — 風險層統一 + Kelly（1 個月）
**目標**：10 道守門 + 動態相關性 + Kelly 上線
- [ ] `risk/guards.py` 10 道守門 pipeline
- [ ] `correlation_matrix` 週算更新
- [ ] Kelly 倉位計算（per strategy 歷史績效驅動）
- [ ] `risk_decisions` audit 表
- [ ] Dashboard `/risk` 儀表

**里程碑 G**：任何單都能列出通過哪幾關、被哪關擋下、風險層 100% 覆蓋

### Phase H — Live ramp 全系統（2 個月）
**目標**：全系統進入 real-money
- [ ] Capital ramp schedule：$100 → $300 → $1000 → $3000 → $5000
- [ ] 每層 ramp 的硬性驗收指標（週 PnL / max DD / 守門誤擋率）
- [ ] Alerting pathways 完整
- [ ] 人工解鎖 pathway（連虧 3 日後）
- [ ] 壓力測試（模擬極端市況 CB）

**里程碑 H（最終）**：每月穩定正 PnL、max DD < 10%、系統自動運作零人為干預 > 72h

---

## 12. 環環相扣的依賴圖

```
           ┌──────────────────────────────────────────┐
           │  Phase A  SM P4/P5 收斂 + QD 借鑒 P0      │
           └───────┬──────────────────────────┬───────┘
                   │                          │
                   ▼                          ▼
           ┌──────────────────┐   ┌──────────────────────┐
           │  Phase B 基礎設施  │   │  Phase C Kronos 整合  │
           │  (signal_history  │   │  (單獨服務，        │
           │   + reflection)    │───│   需 B 的 schema)    │
           └───────┬──────────┘   └──────────┬───────────┘
                   │                          │
                   ▼                          ▼
           ┌──────────────────────────────────────────┐
           │  Phase D  AI + Fusion + Regime           │
           │   (需 B 的 signal_history 累積資料判 regime)│
           └───────┬──────────────────────────────────┘
                   │
                   ▼
           ┌──────────────────────────────────────────┐
           │  Phase E  策略 DSL + 多策略管理            │
           └───────┬──────────────────────────────────┘
                   │
                   ▼
           ┌──────────────────────────────────────────┐
           │  Phase F  跨市場 + 統一執行                │
           └───────┬──────────────────────────────────┘
                   │
                   ▼
           ┌──────────────────────────────────────────┐
           │  Phase G  風險層統一 + Kelly               │
           └───────┬──────────────────────────────────┘
                   │
                   ▼
           ┌──────────────────────────────────────────┐
           │  Phase H  Live ramp 全系統                 │
           └──────────────────────────────────────────┘
```

**關鍵依賴**：
- **B 必須早做**：`UniversalSignal` schema 是後面所有 source 的通用語言
- **C 和 D 可並行**：Kronos 服務是獨立微服務；D 的融合器可先寫介面、mock Kronos 資料測試
- **E 依賴 D**：策略需要融合後訊號才能 entry
- **F 依賴 E**：多市場需要策略 DSL 才不會變成 spaghetti
- **G 依賴 F**：所有市場下單都過同一 risk 層
- **H 只能最後**：前面每層都穩才能放真錢

---

## 13. 成功指標

### 13.1 技術指標

| 指標 | 目標 |
|---|---|
| `UniversalSignal` 覆蓋率 | 100% 訊號都寫入 `signal_history` |
| 訊號處理延遲 | p95 < 10s (source → fused) |
| Kronos 推理延遲 | p95 < 15s (Kronos-small CPU) |
| 系統 uptime | > 99.5% (計算為所有 daemon 加權) |
| 守門 audit 完整性 | 100% 下單決策有 `risk_decisions` 記錄 |
| Reflection 自動化 | 每週自動跑、自動產 calibration 建議 |

### 13.2 策略指標

| 指標 | 初期 | 後期（H 完成）|
|---|---|---|
| 月 PnL 平均 | > 0 | > BTC buy-hold |
| Max DD | < 15% | < 10% |
| Sharpe | > 1.0 | > 1.5 |
| 策略數 | 3 (E 里程碑) | 6-10 |
| 市場覆蓋 | 1 (A) | 3 (F) |
| 訊號源 | 3 (SM + TA + 現有 macro) | 5 (+ Kronos + AI) |

### 13.3 觀測指標

| 指標 | 目標 |
|---|---|
| Signal-fused accuracy (per source) | 可視化 + 月度報告 |
| Strategy win rate | 每策略獨立追蹤 |
| Regime 準確性 | Regime 切換後 14 天回顧預測是否對 |
| Calibration 建議採納率 | 人工審批通過 > 50% |

---

## 14. 風險與應對

| 風險 | 影響 | 應對 |
|---|---|---|
| Kronos 對加密市場外推效果差 | 預測層失效 | Finetune on HL/OKX data；不過度依賴（regime BULL_CHOPPY 最多佔 30% 權重）|
| LLM API 成本暴漲 | 月度支出 | 用 cache + batch + 小模型（Haiku）為主；AI layer 只做 key 決策 |
| 多市場資料同步時差 | Signal 不一致 | 所有 bar 統一 UTC；close-based 訊號延後 30s 確認 |
| Regime 誤判 | 全系統錯權重 | Regime 切換強制 shadow 24h 再 live；人工可手動 lock regime |
| pending_orders worker 崩潰 | 訊號堆積 | Worker 有 supervisor；pending > 1min 觸發 alert |
| Correlation 計算過時 | G7 失效 | Fallback 硬編碼分群；corr 更新失敗不 block |
| Freqtrade 與新架構雙軌衝突 | 重複下單 | Freqtrade 限 subset symbols；pending_orders 有 dedup 檢查 |
| 個人帶寬有限 | Roadmap 延遲 | 每 phase 可獨立交付；AI 訊號源可 optional 上線 |

---

## 15. 開放性決策（需拍板）

在動 Phase B 前需要確定：

| # | 決策點 | 選項 | 建議 |
|---|---|---|---|
| R1 | Kronos 部署方式 | 本地 CPU / 本地 GPU / runpod on-demand / HF inference endpoint | **本地 CPU + Kronos-small**（成本 0、延遲可接受） |
| R2 | LLM 首選供應 | Anthropic Claude / OpenAI / OpenRouter | **Anthropic Claude**（已有 API key、prompt caching 省錢）|
| R3 | US 股票 broker | IBKR / Alpaca / Polygon + 其他 | **IBKR**（覆蓋廣；有 paper trade 帳戶）|
| R4 | TW 股票 API | 永豐 Shioaji / 富邦新 API / 統一 | **永豐 Shioaji**（open 免費、社群活躍）|
| R5 | Strategy DSL 語法 | 純 YAML / YAML + CEL 表達式 / Python subset | **YAML + CEL**（可寫 `fused.ensemble_score >= 0.6` 這類表達）|
| R6 | `fused_signals` 保留期 | 30d / 90d / 無限 | **90d**（足夠 reflection，storage 可接受）|
| R7 | Redis 部署 | compose container / Supabase 托管 / Upstash | **compose container**（和其他 service 共生命週期）|
| R8 | Dashboard 新頁風格 | 沿用 institutional / Polymarket card / 混合 | **institutional**（已建好 tokens）|
| R9 | Kronos finetune 資源 | 本地 GPU / rent H100 短時 | **rent H100 4h ~$8/次** 做 finetune batch |
| R10 | `signal_history` 寫入頻率 | 每訊號即寫 / batch 5s / batch 60s | **batch 5s**（低 DB 壓力、延遲可接受）|

---

## 16. 下一步（本週 → 下週）

1. **你審這份 roadmap**，逐行 push back（使用者習慣）
2. R1-R10 開放性決策拍板
3. 解 P4 Gate 的 blocker（migration 015 apply）
4. 啟動 **Phase A 剩餘工作** + **Phase B 的 P0-3（Notifier 抽象）**（兩者獨立可同步）
5. 開 Phase B 的 `UniversalSignal` schema + `signal_history` 表

文件下次更新：每個 Phase 完成後 status 欄更新 + 回顧

---

## 附錄 A：名詞對照

| 縮寫 | 全名 | 說明 |
|---|---|---|
| SM | Smart Money | 鯨魚跟單 |
| TA | Technical Analysis | Supertrend 等傳統指標 |
| LLM | Large Language Model | Claude / GPT |
| DSL | Domain-Specific Language | 策略定義 YAML |
| CB | Circuit Breaker | 熔斷 |
| DD | Drawdown | 最大回撤 |
| QD | QuantDinger | 參考專案 |
| HL | Hyperliquid | 鯨魚 DEX |

---

## 附錄 B：檔案藍圖預覽

```
/
├── shared/
│   ├── data/              # L1 資料層 (Phase B)
│   ├── signals/           # UniversalSignal schema (Phase B)
│   ├── notifier.py        # 統一通知 (Phase A QD 借鑒)
│   └── credential_crypto.py  # (Phase B QD 借鑒)
│
├── smart_money/           # L2-B SM 源 (現有，延續)
├── kronos_layer/          # L2-A Kronos 源 (Phase C 新)
├── ai_layer/              # L2-D AI 質性 (Phase D 新)
├── market_monitor/        # L2-E Macro (現有，升級)
├── strategies/            # L2-C TA 源 (現有重整)
│
├── fusion/                # L3 融合 + Regime (Phase D 新)
│   ├── regime.py
│   ├── fuser.py
│   └── weights.yaml
│
├── strategy_engine/       # L4 策略 (Phase E 新)
│   ├── dsl.py
│   ├── evaluator.py
│   └── yamls/
│
├── risk/                  # L5 風險 (Phase G 新)
│   ├── guards.py
│   ├── correlation.py
│   └── kelly.py
│
├── execution/             # L6 執行 (Phase F 新)
│   ├── base.py
│   ├── factory.py
│   ├── dispatcher.py
│   ├── worker.py
│   ├── okx/
│   ├── ibkr/
│   └── tw_stock/
│
├── reflection/            # L7 觀測 (Phase B 新, 持續擴充)
│   ├── validator.py
│   ├── calibration.py
│   └── reporter.py
│
├── apps/                  # web + api (現有)
└── supabase/migrations/   # schema (持續擴充)
```

每個資料夾都獨立可 unit test、可單獨 deploy 當微服務。
