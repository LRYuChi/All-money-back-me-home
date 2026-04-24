# QuantDinger 借鑒方案

**起草日期**: 2026-04-25
**來源專案**: [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) v3.0.2 · 1835★ · Apache-2.0
**目的**: 基於 QD 的成熟模式，識別本專案**每一層**可借鑒的設計，按 ROI 排序、給出 phase-wise 實作路徑。

---

## 0. TL;DR — 不全盤照抄，選擇性借鑒

QuantDinger 的強項：成熟的 **AI research + multi-exchange execution + multi-user billing** 平台；弱項：沒有我們的鯨魚跟單 niche + 跟我們的 ML/quant 基底差異大。

**不要做**：整盤 migrate 到 QD。它有 Flask/Vue，我們是 FastAPI/Next.js；它是多用戶 SaaS，我們是 personal 工具。

**要做**：擷取它的 **10 個架構模式**，對我們每一層個別 graft。分 3 期、優先度排序如下：

| 優先度 | 項目 | 面向 | ROI | 實作成本 |
|---|---|---|---|---|
| P0 🔥 | Pending Orders + Worker pattern | 執行層解耦 | 高 | 3 d |
| P0 🔥 | Reflection Loop + AI Calibration | Ranking 自動校準 | 高 | 4 d |
| P0 🔥 | Notifier 抽象（multi-channel） | 通知層 | 中高 | 2 d |
| P1 | Strategy Snapshot（backtest） | 可觀測/重現性 | 中 | 2 d |
| P1 | Redis 加入（queue + rate limit） | 穩定性 | 中 | 2 d |
| P1 | Multi-exchange Adapter | 未來擴展 | 中 | 5 d |
| P2 | Regime Detection 整合 | Signal 質量 | 中低 | 3 d |
| P2 | Credential 加密儲存 | 安全 | 中 | 2 d |
| P2 | AI Analysis Memory（P6 鋪路） | AI 層 | 中 | 5 d |
| P3 | Indicator Code Sandbox | 策略生成 | 低 | — |

---

## 1. 架構對比表

| 層 | QuantDinger | 本專案 | 差距評估 |
|---|---|---|---|
| 前端 | Vue + Nginx prebuilt | Next.js 14 | **我們更現代**、不必動 |
| API | Flask + Python services | FastAPI | **我們更現代** |
| DB | PostgreSQL 16 | Supabase (Postgres 16) | 同等 |
| Queue/Cache | Redis 7 | ❌ 無 | **我們缺** |
| Multi-exchange | 20+ adapters（factory） | ❌ 只 OKX | **我們缺** |
| AI Layer | LLM/calibration/memory/reflection | `mcp_server` + 稀疏 | **我們較淺** |
| Execution | pending_orders → worker → adapter | shadow/simulator 直連 | **我們較耦合** |
| Notification | 統一抽象（TG/Email/SMS/Discord/Webhook） | 散在各處的 TG 呼叫 | **我們缺抽象** |
| Snapshots | 完整 strategy_snapshot 表 | backtest/report JSON | **我們較鬆** |
| Auth/Billing | 多用戶 + OAuth + 積分 | 單人用途 | N/A（不需要） |
| Security | credential_crypto 加密 | 明文 .env | **我們較弱** |
| Docker | PG + Redis + nginx + backend | 類似但無 Redis | 差一個 Redis |

---

## 2. 十個可借鑒項（詳細設計）

### P0-1. 🔥 Pending Orders + Worker 解耦

**現況**：`signals/aggregator.py` 產 `FollowOrder` 後**直接**呼叫 `shadow/simulator.py` 寫 `sm_paper_trades`。live 模式將會直接打 OKX。沒中間暫存層。

**QD 做法**：訊號 → 寫 `pending_orders` 表（`execution_mode=signal|live`）→ `PendingOrderWorker` poll 分派。worker 可以：
- 支援 retry / backoff
- 可以 pause / cancel 單筆
- 支援多 destination（paper / live / notify-only）
- 崩潰不丟訊號（worker 重啟從 DB 續作）

**我們的版本**：
```
smart_money/execution/dispatcher.py      # NEW, 取代 simulator 直連
smart_money/execution/worker.py          # NEW, 背景 poll 任務
supabase/migrations/016_sm_pending.sql   # NEW 表

-- sm_pending_orders schema (draft)
CREATE TABLE sm_pending_orders (
  id BIGSERIAL PK,
  follow_order JSONB NOT NULL,          -- 完整 FollowOrder 序列化
  execution_mode TEXT CHECK (mode IN ('paper','live','notify')),
  status TEXT CHECK (status IN ('pending','dispatched','failed','cancelled')),
  attempts INT DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  dispatched_at TIMESTAMPTZ,
  -- Idempotency
  client_order_id TEXT UNIQUE
);
```

**好處**：
- Live P5 時，把 daemon 和 execution 拆開 — 訊號不會因 OKX 抖動而丟失
- 對 shadow 也有意義：可以追溯 signal → pending → paper 全鏈路的時序
- 人工可以直接 `UPDATE sm_pending_orders SET status='cancelled' WHERE ...` 阻擋特定單

**成本**：3 人天（表 + worker + 改 aggregator 寫 pending 表 + 測試）
**相依**：本身無；但要在 P5 live 前完成

---

### P0-2. 🔥 Reflection Loop + AI Calibration

**現況**：P3 backtest gate 是**一次性驗收**，通過了就靠 P2 ranking 固定權重選 top N。權重 (sortino 0.22, pf 0.18, ...) 是人工 tune 出來的，沒有「實戰回饋→自動調整」的迴圈。

**QD 做法**（`reflection.py` + `ai_calibration.py`）：
- `ReflectionService.run_verification_cycle()` 每週跑：
  1. 找出 ≥ 7 天前的 AI 分析決策
  2. 算實際 forward return
  3. 用簡單規則判斷對錯（BUY ↔ return > +2%、SELL ↔ return < -2%）
  4. 寫回 `analysis_memory.was_correct + actual_return_pct`
- `AICalibrationService` 用這些已驗證資料搜尋最佳 buy/sell threshold

**我們的版本**（套用到鯨魚跟單）：
```python
# smart_money/reflection/validator.py

class RankingReflection:
    """每週驗證上週 ranking top-N 的 forward paper_pnl"""
    def run_weekly(self):
        snapshot_date = (today - 7d)
        for ranked_wallet in top_50_of(snapshot_date):
            paper_pnl_7d = paper_trades_from(ranked_wallet, since=snapshot_date)
            self.record(
                wallet_id, snapshot_rank=rank, snapshot_score=score,
                realized_pnl_7d=paper_pnl_7d,
                correct=paper_pnl_7d > 0,
            )
        # Aggregate: high-score wallets should outperform low-score
        rank_vs_pnl_correlation = self.compute_spearman()
        if correlation < 0.3:
            alert("ranking 與實際 PnL 相關性崩潰，需要 recalibrate")
```

**更進一步（calibration）**：
- 過去 N 週資料進來後、用 scipy optimize 重新搜 metric weights
- 不是盲目接受新 weights（可能 overfit）— 僅作「建議」Telegram 推，人工審批後才 merge

**好處**：
- P3 gate 從一次性 → **持續性**，系統會隨時間學習
- 知道「哪些 metric 實際上 predict power 高」

**成本**：4 人天（Reflection service + 新表 `sm_ranking_reflections` + calibration cron + Telegram alert）
**相依**：需要至少 14 天 shadow paper trades 累積才有意義

---

### P0-3. 🔥 Notifier 抽象

**現況**：Telegram token 在多處寫死（freqtrade container / telegram-bot container / market_monitor）。要換通道（Discord / Webhook）得動多處。

**QD 做法**（`signal_notifier.py`）：
- 每個「訊號觸發點」不直接呼 Telegram；而是呼 `SignalNotifier.send(channels, targets, message)`
- channels 可選：`["browser", "email", "telegram", "discord", "webhook"]`
- per-strategy 或 global config 決定要送到哪
- 失敗通道不 block 其他通道

**我們的版本**：
```python
# shared/notifier.py (or smart_money/notifier.py)

class Notifier(Protocol):
    def send(self, msg: str, level: Literal['info','warn','error']) -> None: ...

class TelegramNotifier: ...
class DiscordNotifier: ...
class WebhookNotifier: ...
class MultiChannelNotifier:
    def __init__(self, channels: list[Notifier]): ...
    def send(self, msg, level):
        for ch in self.channels:
            try: ch.send(msg, level)
            except Exception as e: logger.warning(...)  # 不中斷
```

**好處**：
- Telegram 壞掉不影響訊號觸發
- 未來加 Discord / Webhook 5 行程式碼

**成本**：2 人天
**相依**：無；獨立 refactor

---

### P1-4. Strategy Snapshot

**現況**：`backtest/reporter.py` 產 JSON report，但不含：
- 程式碼 git commit hash
- config yaml 內容
- 跑的資料時段準確範圍
- 當時 metric weights

未來再跑時不能保證 byte-for-byte reproducibility。

**QD 做法**（`strategy_snapshot.py`）：
每次 backtest 跑完，完整封存：strategy code + config + data window + results + git_hash 進 snapshot 表。

**我們的版本**：`sm_backtest_snapshots` 表儲 `git_hash + ranking_config + cutoffs + rng_seed + full_report`。

**好處**：3 個月後「咦這 report 怎麼產的」可以 100% 重現。

**成本**：2 人天
**相依**：不 block 其他，但 P0-2 reflection 如果要看歷史 P3 gate runs，需要 snapshot

---

### P1-5. Redis（queue + rate limit）

**現況**：`SignalAggregator` 的 `_pending` 是 in-memory dict；daemon 重啟 → 所有累積中的訊號窗口重置。HL SDK 的 rate limit 靠 python time.sleep 守，不跨 process。

**QD 做法**：Redis 7 當 cache + queue（不過 QD 其實沒把 aggregator 放 Redis，但架構有接口）

**我們的版本**：
```
docker-compose.prod.yml:
  redis:
    image: redis:7-alpine
    restart: unless-stopped

smart_money/signals/aggregator.py:
  # Pending buckets 改存 Redis Hash
  # Key: agg:{symbol}:{side}
  # Value: msgpack-serialised PendingBucket
  # TTL: window_sec + 60
```

**好處**：
- daemon 重啟不丟 aggregation state
- 未來 scale out 到多 worker 可以共享 queue

**成本**：2 人天（compose + aggregator 改寫 + 測試）
**相依**：無

---

### P1-6. Multi-exchange Adapter (factory pattern)

**現況**：`smart_money/execution/mapper.py` 硬編碼只 OKX。未來擴 Binance / Bybit 要重寫一大塊。

**QD 做法**（`live_trading/`）：
```
live_trading/base.py      — abstract ExchangeClient
live_trading/factory.py   — create_client(exchange_name) -> ExchangeClient
live_trading/okx.py       — OKX 實作
live_trading/binance.py   — Binance 實作
live_trading/bybit.py     — Bybit 實作
... 20+ 個適配器
```

**我們的版本**：
```
smart_money/execution/
├── base.py              # abstract Exchange (symbol_map, get_mid, place_order, cancel)
├── factory.py           # create(name) → Exchange
├── okx.py              # OKX 實作 (P5 v1 必做)
├── binance.py          # 可選
└── mapper.py            # unchanged (HL symbol → exchange-specific symbol)
```

**好處**：P5 先做 OKX、但接口留好。Binance 以後 1 天搞定。

**成本**：5 人天（P5 整體改架構；不是單獨工作）
**相依**：P5 開始前做

---

### P2-7. Regime 整合 Smart Money Ranking

**現況**：`market_monitor/confidence_engine.py` 已有 regime 概念（HIBERNATE / NORMAL / ...）但跟 `smart_money/ranking` 完全隔離。

**QD 做法**（`experiment/regime.py`）：偵測市場狀態 → 動態選策略。

**我們的借鑒**：在 Bull / Bear regime 下，不同 metric 權重可能更有用：
- Bear: 鯨魚的 Sortino 比 Profit Factor 更重要（風險控管為王）
- Bull: Profit Factor 更重要（敢 all-in 的贏）

```python
# smart_money/ranking/regime_weights.py
REGIME_WEIGHT_OVERRIDES = {
    "bull": {"w_profit_factor": 0.25, "w_sortino": 0.15},
    "bear": {"w_sortino": 0.30, "w_profit_factor": 0.12},
    "sideways": {},  # default
}
```

每次 rank 跑，先看當下 regime 再選一組 weights。

**好處**：ranking 隨市場動態調整。

**成本**：3 人天（regime 判斷邏輯 + weights 切換 + 重跑 P3 gate 驗證）
**風險**：可能 overfit；需同時跑 A/B（舊 static vs 新 regime-aware）

---

### P2-8. Credential 加密儲存

**現況**：`OKX_API_KEY` / `SUPABASE_SERVICE_KEY` 明文在 `.env`。VPS root 有權限看全部。

**QD 做法**（`credential_crypto.py`）：
- master secret key in env
- per-credential encrypt-at-rest 存 DB
- 應用層解密使用

**我們的版本**：
```python
# shared/credential_crypto.py (類似 QD)
def encrypt_credential(plaintext: str, master_key: bytes) -> str: ...
def decrypt_credential(ciphertext: str, master_key: bytes) -> str: ...
```

VPS .env 只留 `MASTER_SECRET`；OKX API key 加密後存 Supabase 的 `secrets` 表，服務啟動時拉下來解密。

**好處**：
- VPS 被人看到 .env 也只拿到 MASTER_SECRET，不會直接看到 OKX key
- Audit log 可以記「誰取了哪個 key」

**成本**：2 人天（encrypt lib + 遷移現有 secrets）
**風險**：MASTER_SECRET 洩漏還是完蛋；這不是 true secret management，只是多一道門

---

### P2-9. AI Analysis Memory（P6 鋪路）

**現況**：`docs/SMART_MONEY_MIGRATION.md §6` 預留 P6 AI 質性層但沒實作。

**QD 做法**（`analysis_memory.py`）：
- 每次 AI 分析存 `analysis_memory` 表：symbol + timestamp + decision + reasoning + predicted_return
- 後續可查歷史分析、feed 給 reflection 驗證
- 是 AI calibration 的資料基礎

**我們可借鑒**：在做 P6 前，先建好 memory 表：
```sql
CREATE TABLE sm_ai_analysis_memory (
  id BIGSERIAL PK,
  wallet_id UUID REFERENCES sm_wallets(id),
  analyzed_at TIMESTAMPTZ,
  ai_decision TEXT,  -- 'follow' | 'skip' | 'watch'
  reasoning TEXT,    -- LLM 推理
  confidence FLOAT,
  actual_return_7d FLOAT,  -- Reflection 填
  was_correct BOOLEAN      -- Reflection 填
);
```

配合 P0-2 的 Reflection，AI 分析就有閉環。

**成本**：5 人天（完整 P6 實作）；或 1 天只建 schema 先佔位
**相依**：P0-2 Reflection 完成才有意義

---

### P3-10. Indicator Code Sandbox

**現況**：我們沒 AI 輔助策略生成，不 relevant。

**QD 做法**：`indicator_code_quality.py` 對 LLM 生成的 Python 做靜態檢查 + sandbox 執行。

**建議**：**不做**。這是 QD 為「使用者用 AI 寫策略」的用途；我們不是 SaaS。

---

## 3. 不建議借鑒的（避免 scope creep）

| QD 功能 | 為何不借鑒 |
|---|---|
| Multi-user billing（會員/積分/USDT） | 我們是 personal 工具 |
| IBKR / MT5 整合 | 我們只做 crypto |
| Polymarket batch analyzer | 我們已有 polymarket 模組，設計不同 |
| Strategy compiler（AI 生策略） | 我們的 niche 是鯨魚跟單，不需要策略生成 |
| Vue + Flask stack | 我們 Next.js + FastAPI 更現代 |
| 全盤 Docker override | 我們 compose 結構已優化過 |

---

## 4. 建議實作順序

### Phase 1（本週）— P0 三件最高 ROI
1. **Notifier 抽象**（2d）— 最獨立、零相依、立即可用
2. **Pending Orders + Worker**（3d）— P5 live 前必做；做完再做 P5 不會重工
3. **Reflection 迴圈**（4d）— 要等 shadow 跑 14 天有資料，但 schema 可以先建

### Phase 2（下週 + 1）— P1 三件
4. **Strategy Snapshot**（2d）
5. **Redis queue**（2d）
6. **Multi-exchange adapter**（5d）— 和 P5 live 並行做

### Phase 3（月內）— P2 三件
7. **Regime 整合**（3d）— 需要先跑完 Phase 1/2
8. **Credential 加密**（2d）
9. **AI Memory schema**（1d 建表、5d 完整實作延後）

### 不做（跳過）
10. Indicator Sandbox
11. Multi-user
12. IBKR/MT5

**總投入估計**：Phase 1 = 9 人天；P1+P2 = 20 人天。按每天 focused 3-4h 算，個人專案 1.5~2 個月完成。

---

## 5. 給目前 shadow daemon 的立即改善（0 成本）

從 QD 看到、可以立刻改的微調（不計入 phase）：

- ✅ **shadow daemon 的 supervisor** 已用 30min timeout（我們已做）
- ⚠️ **pending_orders worker**：當前 `shadow/simulator.py` 在 async drain loop 內直接寫 DB，若 Supabase 慢會 block 訊號消費；把它變成 async task 放 queue 拉出
- ⚠️ **並行 DB 呼叫**：`inspect` 腳本 3 次 window 查詢是 serial，可並行
- ⚠️ **Signal health 可加 uptime**：目前面板只看 24h 活動；可以 expose shadow container uptime（docker inspect 或自己塞一個 `sm_daemon_heartbeat` 表）

---

## 6. 最後一個重點：別只看 QD，也看我們比它強的地方

- 我們的 **Freqtrade 整合** QD 沒有；他們是自建 engine
- 我們的 **Supertrend + Smart Money 雙軌** QD 沒有
- 我們的 **Next.js 14 dashboard** QD 不及
- 我們的 **Hyperliquid WS 原生支援** QD 不及（他們主要 crypto 傳統交易所）
- 我們的 **Pre-push hook + 三層 migration fallback** QD 沒有

結論：**我們是鯨魚跟單 + TA 混合的 niche 工具；QD 是 all-in-one quant SaaS**。我們借鑒它的**架構模式**，不抄它的**功能範圍**。
