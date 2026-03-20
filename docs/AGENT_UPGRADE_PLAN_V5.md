# 計劃：AI Agent 深度修復 + 記憶重構 + Skills 架構（v5 最終版）

## 背景

系統審計 → 用戶 review × 4 輪（9.5/10）。整合：Bug 修復、記憶重構、Token 優化、Skills 架構、降級機制、遷移策略、回滾管理。

---

## P0：CRITICAL Bug 修復

### 1. OTE Short/Long 數值修正 — `strategies/smc_trend.py`

**原始 Bug**：Short OTE 無上界；數值不符合 Fibonacci 61.8%~79% 定義。

```python
FIB_OTE_LOW  = 0.618
FIB_OTE_HIGH = 0.79

# Short OTE（Premium 區 61.8%~79% 回撤）
dataframe["in_ote_short"] = (
    (dataframe["close"] >= range_high - range_size * FIB_OTE_HIGH)
    & (dataframe["close"] <= range_high - range_size * FIB_OTE_LOW)
    & (dataframe["in_premium"])
    & (dataframe["open"] >= range_high - range_size * FIB_OTE_HIGH)  # K線實體確認
)

# Long OTE（Discount 區，同步修正一致性）
FIB_OTE_LONG_LOW  = 0.21
FIB_OTE_LONG_HIGH = 0.382
dataframe["in_ote_long"] = (
    (dataframe["close"] >= range_low + range_size * FIB_OTE_LONG_LOW)
    & (dataframe["close"] <= range_low + range_size * FIB_OTE_LONG_HIGH)
    & (dataframe["in_discount"])
    & (dataframe["open"] <= range_low + range_size * FIB_OTE_LONG_HIGH)
)
```

**防禦測試**：
```python
def test_ote_short_boundaries():
    # close 在 61.8%~79% → True
    # close > 79% 或 < 61.8% → False
    # 影線觸碰但實體不在區間 → False
```

### 2. BotStateStore 加鎖+快取+原子寫 — `market_monitor/state_store.py`（修改）

現有 `state_store.py` 已有 `_FileLock`，但缺少：
- **讀取快取**（每根 K 線都讀檔太頻繁）
- **原子寫入**（已有 tmpfile+rename，OK）
- **失敗安全預設值**（讀取失敗 → 保守模式）

修改：
```python
class BotStateStore:
    _cache: dict = {}
    _cache_ts: float = 0
    _CACHE_TTL: int = 30  # 30 秒快取

    @classmethod
    def read(cls) -> dict:
        now = time.time()
        if now - cls._cache_ts < cls._CACHE_TTL and cls._cache:
            return cls._cache.copy()
        # ... 原有讀取邏輯 ...
        # 失敗時返回安全預設：
        except (json.JSONDecodeError, OSError):
            return {
                "agent_pause_entries": False,
                "agent_leverage_cap": 2.0,
                "agent_risk_level": "conservative",
            }
```

### 3. Agent 旗標接入策略 — `strategies/smc_trend.py` + `smc_scalp.py`

在 `bot_loop_start()` 中讀取（使用快取版 BotStateStore）：
```python
state = BotStateStore.read()
self._agent_pause = state.get("agent_pause_entries", False)
self._agent_lev_cap = state.get("agent_leverage_cap")
self._agent_risk = state.get("agent_risk_level")
```

在 `confirm_trade_entry()` 中阻擋：
```python
if getattr(self, "_agent_pause", False):
    logger.warning("AGENT PAUSE: 進場已被 Agent 暫停")
    if _STATE_AVAILABLE: BotStateStore.increment("guard_rejections_today")
    return False
```

在 `leverage()` 中限制：
```python
cap = getattr(self, "_agent_lev_cap", None)
if cap is not None:
    lev = min(lev, cap)
```

在 `custom_stake_amount()` 中調整：
```python
risk = getattr(self, "_agent_risk", None)
risk_scale = {"aggressive": 1.2, "normal": 1.0, "conservative": 0.6, "minimal": 0.3}
if risk in risk_scale:
    adjusted *= risk_scale[risk]
```

### 4. 反轉槓桿分段邏輯

```python
def _calc_reversal_leverage(self, confidence: float, max_lev: float) -> float:
    if confidence < 0.1:   return 1.0
    elif confidence < 0.2: return 1.5
    elif confidence < 0.3: return 2.0
    else:
        logger.warning("反轉模式但信心=%.2f，邏輯異常", confidence)
        return 1.5
```

反轉模式倉位上限 = 總資金 30%。

### 5. import os 修復 — `agent/brain.py` + `agent/tools.py`

### 6. Anti-fragile 計數器重置 — `strategies/smc_trend.py`

找到信號後 `self._no_signal_count = 0`。

### 7. Crypto Env 分層 TTL — `strategies/smc_trend.py`

```python
TTL_CONFIG = {
    "funding_rate":    3600,    # 1h
    "open_interest":   7200,    # 2h
    "news_sentiment":  14400,   # 4h
    "market_structure": 86400,  # 24h
    "btc_dominance":   21600,   # 6h
}
# 統一檢查：超過 TTL → 恢復 NEUTRAL_VALUES
```

### 8. memory.py DATA_DIR 統一

```python
DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"
```

---

## P1：記憶系統重構

### 9. 分層記憶架構 — `agent/memory.py`（重構）

**Schema**：decisions + knowledge + links 三表（見下文）。

新增欄位：`tags`, `domain`, `access_count`, `last_accessed`, `archived`。

**遺忘曲線檢索**（Python 端計算，避免 SQLite 無 LOG 問題）：
```python
def retrieve_relevant(self, regime, domain=None, limit=10):
    rows = self.db.execute("... WHERE archived=0 ORDER BY timestamp DESC LIMIT 200")
    scored = []
    for row in rows:
        score = (
            (3.0 if row["regime"] == regime else 1.0) * 3 +
            math.log(row["access_count"] + 1) * 2 +
            (1.0 / (row["days_since_access"] + 1)) * 1 +
            (1.5 if row["was_successful"] else 0.5 if row["was_successful"] == 0 else 1.0) * 2
        )
        scored.append((score, row))
    scored.sort(reverse=True)
    # 更新 access_count（越用越容易被找到）
    for _, row in scored[:limit]:
        self._touch(row["id"])
    return [r for _, r in scored[:limit]]
```

**雙向連結**：Obsidian 式 `links` 表，支援 `supports/contradicts/caused_by/similar_to` 關係。

### 10. 知識提煉引擎 — `agent/knowledge_extractor.py`（新增）

強化版 Prompt（用戶修正版）：
- 要求引用具體 `evidence_ids`
- 要求對比正反案例
- 信心 > 0.8 需 evidence_count >= 10
- 禁止無引用的規則
- 輸出含 `counter_evidence_ids` 和 `insufficient_data_areas`

### 11. 記憶整理排程 — `scripts/memory_consolidation.py`（新增）

加入冪等性保護：
- 同一週只執行一次（檢查 `last_consolidation_time`）
- 執行鎖（`consolidation.lock`，1 小時有效）
- 歸檔 > 90 天且 access_count < 3 的決策
- 刪除 > 180 天的歸檔記錄
- 知識信心衰減：30 天無新證據 → confidence *= 0.8，< 0.2 → 歸檔

---

## P1：幻覺防護三層機制

### 12. `agent/hallucination_guard.py`（新增）

**層一：輸入層 — 數據新鮮度校驗**
- 每個數據源獨立 TTL（不是統一 30 分鐘）
- 過期數據標記 ⚠️，不得作為決策主要依據
- 缺失數據 → 強制輸出「觀望」

**層二：輸出層 — 決策結構校驗**
- 必填欄位：`action, reason, confidence, data_citations`
- 引用校驗：`data_citations` 中的數值必須能在原始數據中找到
- 高信心校驗：confidence > 0.85 需 >= 3 個 data_citations
- 語言校驗：action 欄位禁止不確定用語

**層三：驗證層 — 決策後事實核對**（用戶新增）
```python
def schedule_verification(self, decision_id, decision):
    # 24h / 48h / 168h 三個時間點自動驗證
    # 比較 baseline_metrics vs current_metrics
    # PF 下降 + DD 擴大 → was_effective = False
    # 回填到 decisions 表 + 更新 knowledge 信心
```

`scripts/run_verifications.py`（新增）— 每小時執行，處理到期驗證。

---

## P1：可觀測性系統（用戶新增）

### 13. `agent/observability.py`（新增）

**決策追蹤**：每次決策記錄完整鏈路：
```python
trace = {
    "inputs":    {"regime", "market_data", "data_freshness", "retrieved_memories"},
    "reasoning": {"prompt_tokens", "model", "raw_response"},
    "output":    {"decision", "validation_result", "was_approved", "reject_reason"},
}
```

**系統健康儀表板**（每 15 分鐘更新）：
- `agent_health`: 錯誤率、決策延遲、幻覺率
- `data_health`: 過期數據源、API 失敗率
- `memory_health`: 決策總數、知識規則數、檢索分數、最後整理時間
- `trading_health`: Bot 狀態、持倉數、日 PnL、熔斷狀態

---

## P2：其他修復

### 14. Telegram Bot — `market_monitor/telegram_bot.py`
- 日期計算：`timedelta(days=1)` 取代 `day+1`
- 移除硬編碼用戶 ID，未設定時 fail fast
- AI prompt 加入數據時間戳

### 15. Regime Detector — `agent/regime_detector.py`
- 修復 `_crypto_env` dict vs list 不匹配
- 加入因子權重日誌
- 信心分數改連續值

### 16. Report Collector — `market_monitor/report_collector.py`
- Claude API 錯誤 → fallback digest
- 新聞加入時間戳

---

## 實施順序

| 優先級 | 步驟 | 檔案 | 說明 |
|--------|------|------|------|
| P0 | 1 | `market_monitor/state_store.py` | 加快取+失敗安全預設 |
| P0 | 2 | `strategies/smc_trend.py` | OTE 61.8%~79% + K線實體確認 |
| P0 | 3 | `strategies/smc_trend.py` | Agent 旗標接入 |
| P0 | 4 | `strategies/smc_scalp.py` | 同上 |
| P0 | 5 | `strategies/smc_trend.py` | 反轉槓桿分段 + 倉位 30% 上限 |
| P0 | 6 | `agent/brain.py` + `tools.py` | import os + 幻覺 prompt |
| P0 | 7 | `strategies/smc_trend.py` | anti-fragile 重置 + crypto TTL |
| P0 | 8 | `agent/memory.py` | DATA_DIR 統一 |
| P1 | 9 | `agent/memory.py` | 記憶重構（分層+遺忘曲線+雙向連結） |
| P1 | 10 | `agent/knowledge_extractor.py`（新增） | 強化版提煉引擎 |
| P1 | 11 | `scripts/memory_consolidation.py`（新增） | 冪等性整理排程 |
| P1 | 12 | `agent/hallucination_guard.py`（新增） | 三層防護+決策後驗證 |
| P1 | 13 | `agent/observability.py`（新增） | 追蹤鏈+健康儀表板 |
| P2 | 14 | `market_monitor/telegram_bot.py` | 日期+安全+時間戳 |
| P2 | 15 | `agent/regime_detector.py` | 數據結構+因子日誌 |
| P2 | 16 | `market_monitor/report_collector.py` | 錯誤處理+時間戳 |

## 防禦測試清單

```python
# P0 修復的防禦測試
test_ote_short_boundaries()           # OTE 61.8%~79% 邊界
test_ote_long_boundaries()            # OTE long 一致性
test_agent_pause_blocks_entry()       # 旗標阻擋進場
test_agent_leverage_cap()             # 槓桿上限生效
test_reversal_leverage_steps()        # 分段槓桿正確
test_concurrent_state_read()          # 10 執行緒同時讀取
test_state_read_failure_defaults()    # 讀取失敗 → 保守值

# P1 記憶測試
test_retrieve_relevant_ranking()      # 遺忘曲線排序
test_access_count_increment()         # 越用越靠前
test_consolidation_idempotent()       # 冪等性
test_knowledge_confidence_decay()     # 無新證據 → 衰減
test_bidirectional_links()            # 雙向連結查詢

# P1 幻覺防護測試
test_stale_data_warning()             # 過期數據標記
test_missing_data_forces_watch()      # 缺失 → 觀望
test_high_confidence_needs_citations()# 高信心需多引用
test_verification_scheduled()         # 決策後 3 筆驗證
test_trace_completeness()             # 追蹤鏈完整
```

---

## P0：Skills 架構 + Token 優化（$54/月 → $3/月）

### 核心改變：Skills 取代靜態 System Prompt

```
靜態 Prompt（每次 ~1500 tokens）  →  Skills 動態載入（按需 ~300-800 tokens）
```

### 17. Skills 目錄結構 — `skills/`（新增整個目錄）

```
skills/
├── core/                    # 每次必載
│   ├── SAFETY.md            # 硬性安全規則（~150 tokens）
│   └── OUTPUT_FORMAT.md     # 輸出格式規範（~80 tokens）
├── perception/              # 感知層 Skills
│   ├── MARKET_READ.md       # 解讀市場數據（~200 tokens）
│   ├── PERFORMANCE_READ.md  # 解讀績效（~180 tokens）
│   └── REGIME_READ.md       # 判讀市場機制（~160 tokens）
├── decision/                # 決策層 Skills
│   ├── RISK_DECISION.md     # 風控決策樹（~220 tokens）
│   ├── PARAM_ADJUST.md      # 參數調整指引（~190 tokens）
│   ├── STRATEGY_SWITCH.md   # 策略切換邏輯（~170 tokens）
│   └── EMERGENCY.md         # 緊急應對（~140 tokens）
├── analysis/                # 分析層 Skills
│   ├── DAILY_ANALYSIS.md    # 每日分析框架（~300 tokens）
│   ├── KNOWLEDGE_EXTRACT.md # 知識提煉框架（~280 tokens）
│   └── RETROSPECTIVE.md     # 回顧分析（~250 tokens）
├── meta/                    # Meta Skills
│   ├── SKILL_SELECTOR.md    # 決定載入哪些 Skills
│   └── HALLUCINATION_GUARD.md # 幻覺防護
└── SKILL_REGISTRY.json      # 版本+Token 預算+績效追蹤
```

每個 Skill 是獨立的 .md 文件，可版本控制、獨立測試、獨立演化。

### 18. Skill 載入引擎 — `agent/skill_loader.py`（新增）

載入流程（三層降級）：
1. **正常**：SKILL_SELECTOR → 選擇 → 載入 → 快取成功組合
2. **降級 1**：正常失敗 → 使用上次成功的快取 Skills
3. **降級 2**：快取也沒有 → 使用硬編碼 FALLBACK_SKILL（最保守規則，不依賴任何文件）

| 優先級 | 必載 | 動態載入 | 預估總 tokens |
|--------|------|---------|--------------|
| critical | SAFETY + OUTPUT | + EMERGENCY + MARKET_READ | ~570 |
| high | SAFETY + OUTPUT | + RISK_DECISION + PERF_READ | ~630 |
| medium | SAFETY + OUTPUT | + PARAM_ADJUST | ~420 |
| routine | SAFETY + OUTPUT | + DAILY_ANALYSIS + 全部 perception | ~970 |

### 19. 觸發式呼叫引擎 — `agent/trigger_engine.py`（新增）

**最大節省（-60~70%）**：替代每 15 分鐘無條件輪詢。

規則引擎先判斷（0 token），只在異常時才呼叫 Claude：
- **Critical**：API 錯誤 ≥ 3/h、Bot 靜默 > 30min
- **High**：連虧 ≥ 3、日虧 ≥ 3%、Regime 切換
- **Medium**：ATR 飆升 1.8x、資金費率極端
- **Routine**：每 24h 一次

### 20. Prompt 壓縮器 — `agent/prompt_builder.py`（新增）

- 市場數據：JSON → 單行（150 → 40 tokens）
- 績效：完整 JSON → 關鍵指標（200 → 60 tokens）
- 記憶：只給標題+信心索引（100/條 → 15/條）
- 輸出：最小 JSON `{"a":"...","c":{},"r":"...","conf":0.0,"h":false}`

### 21. 模型路由 + 快取 + 消耗追蹤 — `agent/model_router.py` + `agent/cache_layer.py` + `agent/token_metrics.py`（新增）

- Haiku 用於監控/告警/選擇器，Sonnet 用於 critical/知識提煉
- 快取 no_action 決策（TTL 15min），重大事件清空
- 每日 Telegram 報告：token 消耗、費用、效率指標

### 22. Skill 演化器 — `agent/skill_evolver.py`（新增）

每週知識提煉後自動更新對應 Skills：
- 新知識 → 合併到現有 Skill
- 移除被否定的舊規則
- 保持 Token 預算內
- 版本備份 + Registry 更新

Skills 會隨系統學習而自動進化。

### Token 節省對比

```
場景：連虧3筆觸發，medium 優先級

靜態 Prompt：
  System Prompt 全部規則  ~1500 tokens
  + 市場數據未壓縮       ~400 tokens
  + 績效完整 JSON        ~300 tokens
  + 記憶全部載入          ~500 tokens
  = 合計                  ~2700 input + 150 output = 2850 tokens

Skills 架構：
  Skill 選擇（Haiku）    ~200 tokens
  + SAFETY + PARAM_ADJUST ~420 tokens
  + 壓縮數據             ~150 tokens
  = 合計                 ~200 + (420+150+150) = 920 tokens

節省：~68%
月費：$54 → $3
```

---

## 完整實施順序（含依賴關係）

```
依賴圖：
步驟 1-7：互相獨立（可並行）
步驟 8 → 9
步驟 10, 11：獨立
步驟 7 → 11.5 → 12 → 13（記憶鏈）
步驟 9 + 10 + 11 → 17.5（brain.py 整合最後做）
步驟 12 + 9 → 17（Skill Evolver 依賴記憶和載入器）
```

| 優先級 | 步驟 | 檔案 | 說明 | 依賴 |
|--------|------|------|------|------|
| **P0: Bug** | | | | |
| P0 | 1 | `market_monitor/state_store.py` | 快取+失敗安全預設 | 無 |
| P0 | 2 | `strategies/smc_trend.py` | OTE Fib 61.8%~79% + K線實體 | 無 |
| P0 | 3 | `strategies/smc_trend.py` + `smc_scalp.py` | Agent 旗標接入 | →1 |
| P0 | 4 | `strategies/smc_trend.py` | 反轉槓桿分段+倉位30%上限 | 無 |
| P0 | 5 | `agent/brain.py` + `tools.py` | import os + 幻覺 prompt | 無 |
| P0 | 6 | `strategies/smc_trend.py` | anti-fragile 重置+crypto 分層 TTL | 無 |
| P0 | 7 | `agent/memory.py` | DATA_DIR 統一 | 無 |
| **P0: Skills** | | | | |
| P0 | 8 | `skills/`（新增目錄） | 12 個 Skill .md + Registry | 無 |
| P0 | 9 | `agent/skill_loader.py`（新增） | 含三層降級 Fallback | →8 |
| P0 | 10 | `agent/trigger_engine.py`（新增） | 含例行保護+冷卻+全觸發記錄 | 無 |
| P0 | 11 | `agent/prompt_builder.py`（新增） | Prompt 壓縮 | 無 |
| **P1: 遷移+記憶** | | | | |
| P1 | 11.5 | `scripts/migrate_memory_v2.py`（新增） | Schema 遷移（備份+ALTER+推斷 domain） | →7 |
| P1 | 12 | `agent/memory.py` | 記憶重構（遺忘曲線+雙向連結） | →11.5 |
| P1 | 13 | `agent/knowledge_extractor.py`（新增） | 強化版提煉（引用+正反案例） | →12 |
| P1 | 14 | `scripts/memory_consolidation.py`（新增） | 冪等性整理排程 | →12 |
| **P1: 防護+觀測** | | | | |
| P1 | 15 | `agent/hallucination_guard.py`（新增） | 三層防護+決策後驗證 | 無 |
| P1 | 16 | `agent/observability.py`（新增） | 追蹤鏈+健康儀表板 | 無 |
| P1 | 17 | `agent/skill_evolver.py`（新增） | 含安全閥：diff>30%需人工+7天回滾 | →9,12 |
| P1 | 17.5 | `agent/brain.py`（整合） | 接入 SkillLoader+TriggerEngine | →9,10,11 |
| **P1: 成本控制** | | | | |
| P1 | 18 | `agent/model_router.py`（新增） | 模型分級路由 | 無 |
| P1 | 19 | `agent/cache_layer.py`（新增） | 快取層 | 無 |
| P1 | 20 | `agent/token_metrics.py`（新增） | Token 消耗追蹤+日報 | 無 |
| P1 | 20.5 | `rollback/rollback_manager.py`（新增） | 回滾策略管理（每小時檢查） | →17 |
| **P2: 修補** | | | | |
| P2 | 21 | `market_monitor/telegram_bot.py` | 日期+安全+時間戳 | 無 |
| P2 | 22 | `agent/regime_detector.py` | 數據結構+因子日誌 | 無 |
| P2 | 23 | `market_monitor/report_collector.py` | 錯誤處理+時間戳 | 無 |

## 防禦測試清單

```python
# P0 Bug 修復
test_ote_short_boundaries()               # OTE 61.8%~79% 邊界
test_ote_long_boundaries()                # OTE long 一致性
test_ote_body_confirmation()              # 影線觸碰但實體不在 → False
test_agent_pause_blocks_entry()           # 旗標阻擋進場
test_agent_leverage_cap()                 # 槓桿上限生效
test_reversal_leverage_steps()            # 分段槓桿正確
test_concurrent_state_read()              # 10 執行緒同時讀取
test_state_read_failure_defaults()        # 讀取失敗 → 保守值

# Skills 降級
test_skill_load_failure_uses_fallback()   # SELECTOR 不存在 → FALLBACK_SKILL
test_corrupted_skill_file_graceful()      # 損壞 → 跳過繼續
test_cached_skills_used_on_api_timeout()  # API 超時 → 快取

# 觸發引擎
test_routine_not_starved_by_high()        # 36h 後例行強制執行
test_all_triggers_recorded()              # 多條件全記錄
test_cooldown_prevents_duplicates()       # 冷卻期不重複

# 記憶遷移
test_migration_idempotent()               # 兩次執行不報錯
test_migration_backup_created()           # 備份存在
test_domain_inference_accuracy()          # 推斷正確率 > 70%

# 記憶檢索
test_retrieve_relevant_ranking()          # 遺忘曲線排序
test_access_count_increment()             # 越用越靠前

# Skill Evolver
test_large_diff_requires_review()         # > 30% 需人工
test_low_confidence_skipped()             # 信心 < 0.55 不演化
test_rollback_check_scheduled()           # 部署後有回滾檢查

# 回滾
test_zero_calls_24h_triggers_alert()      # 過度抑制告警
test_skill_auto_rollback_on_pf_drop()     # PF -20% 自動回滾

# 幻覺防護
test_stale_data_warning()                 # 過期標記
test_missing_data_forces_watch()          # 缺失 → 觀望
test_high_confidence_needs_citations()    # 高信心需 ≥ 3 引用
test_verification_scheduled()             # 決策後 3 筆驗證
test_trace_completeness()                 # 追蹤鏈完整
```

## 新增依賴

無新依賴（全部使用標準庫 + 現有 anthropic SDK）。
