# 多 Agent 協作智能交易系統（分階段升級）

## 背景

建立可自我進化的 AI 交易控制系統。核心理念：**先跑起來累積數據，再讓學習引擎發揮作用**。

### 三階段演進路線
```
Phase 1（現在）：OpenClaw + MCP 擴展 → 先跑起來，累積數據
Phase 2（1個月後）：+ RegimeDetector + LearningEngine → 開始真正學習
Phase 3（2個月後）：三 Agent 協作 + StrategyVersionControl → 完全自主
```

### 最終架構
```
┌──────────────────────────────────────────────────────┐
│                    AGENT LAYER                        │
│  Orchestrator                                         │
│    ├── Analyst Agent   (分析市場，不做交易決策)       │
│    ├── Trader Agent    (交易決策，基於分析結果)       │
│    └── Risk Manager    (風控否決，唯一有剎車權)       │
├──────────────────────────────────────────────────────┤
│                 INTELLIGENCE LAYER                    │
│  RegimeDetector  → 客觀規則判斷市場機制（不靠 AI）   │
│  LearningEngine  → 每週提煉知識，更新 Agent Prompt   │
│  VersionControl  → 策略版本管理 + 48h 自動回滾       │
├──────────────────────────────────────────────────────┤
│                  MCP SERVER（已有）                    │
│  感知工具 + 控制工具 + 新聞工具                       │
├──────────────────────────────────────────────────────┤
│  Freqtrade (執行) │ Supabase (記憶) │ Telegram (互動) │
└──────────────────────────────────────────────────────┘
```

---

## Phase 1：OpenClaw + MCP 擴展（立即執行）

> 目標：讓系統跑起來，開始累積決策數據

### 1a. 擴展 MCP Server — `mcp_server/server.py`（修改）

現有工具保留，新增：

**感知工具：**
- `get_performance_metrics(lookback_days)` — 勝率、風報比、回撤、連勝/連敗
- `get_open_positions()` — 持倉詳情
- `get_crypto_environment(symbol)` — 加密環境分數
- `get_news_summary(hours)` — 新聞摘要 + 情緒統計
- `get_agent_state()` — BotStateStore 完整狀態
- `get_regime()` — 當前市場機制（Phase 2 升級為 RegimeDetector）

**控制工具：**
- `set_agent_flag(flag, value)` — 策略軟控制旗標
- `trigger_circuit_breaker(reason)` — 觸發熔斷
- `send_telegram_alert(message, urgency)` — Telegram 告警
- `log_agent_decision(action, reason, confidence, context)` — 記錄決策（含完整上下文）
- `get_decision_history(limit)` — 歷史決策

### 1b. 新聞取得 — `market_monitor/news_fetcher.py`（新增）

- CryptoPanic API（免費，bullish/bearish 標記）
- RSS Feeds（CoinDesk / CoinTelegraph / The Block）
- Tavily Search（免費 1000 次/月，宏觀事件）

### 1c. 策略軟控制 — `strategies/smc_trend.py` + `smc_scalp.py`（修改）

`bot_loop_start()` 讀取 BotStateStore Agent 旗標：
- `agent_pause_entries` → 阻止進場
- `agent_leverage_cap` → 限制槓桿
- `agent_risk_level` → 調整倉位

### 1d. OpenClaw 部署 — `docker-compose.prod.yml` + `openclaw/config/`（新增）

OpenClaw 容器 + MCP 連接 + Telegram 整合 + 排程任務。

System Prompt 重點：
- 每日 08:00 UTC 完整市場分析 + 3 個情境劇本
- 每 15 分鐘績效監控
- 安全規則（槓桿 ≤ 5x、風險 ≤ 2%、連虧 4 → 暫停）
- 利潤驅動解鎖分層

### 1e. 決策日誌格式（為 Phase 2 學習引擎準備）

每次決策記錄完整上下文：
```json
{
  "id": "uuid",
  "timestamp": "ISO",
  "regime": "TRENDING_BEAR",
  "confidence_score": 0.18,
  "crypto_env": {"BTC": 0.64, "ETH": 0.55},
  "news_sentiment": {"bullish": 3, "bearish": 8, "neutral": 5},
  "action": "set_agent_flag",
  "changes": {"agent_risk_level": "conservative"},
  "reason": "信心分數持續低於 0.2，資金費率異常偏多",
  "confidence": 0.82,
  "outcome_7d": null,
  "outcome_30d": null,
  "was_successful": null
}
```

**Phase 1 完成標準**：系統自動運行 > 1 週，累積 > 50 條決策記錄。

---

## Phase 2：RegimeDetector + LearningEngine（1 個月後）

> 目標：從「記錄」進化到「學習」

### 2a. 市場機制識別器 — `agent/regime_detector.py`（新增）

**客觀規則判斷，不依賴 AI（更穩定）**：

```python
REGIMES = {
    "TRENDING_BULL":    {"描述": "結構性上升", "策略": trend_follow, "槓桿上限": 3.0},
    "TRENDING_BEAR":    {"描述": "結構性下降", "策略": short_trend, "槓桿上限": 2.0},
    "HIGH_VOLATILITY":  {"描述": "ATR > 1.5x 平均", "策略": cash_first, "槓桿上限": 1.5},
    "ACCUMULATION":     {"描述": "低波動 BB 擠壓", "策略": breakout, "槓桿上限": 2.0},
    "RANGING":          {"描述": "無明確方向", "策略": scalp, "槓桿上限": 2.0},
}
```

判斷依據（純指標，不靠 LLM）：
- BTC 市場結構：HH+HL = BULL, LH+LL = BEAR
- ATR 比率 > 1.5 = HIGH_VOLATILITY
- BB 寬度 < 0.3 = ACCUMULATION

### 2b. 學習引擎 — `agent/learning_engine.py`（新增）

**每週一次回顧，真正從歷史中學習**：

```python
class LearningEngine:
    def run_weekly_retrospective(self):
        # 1. 取出有結果的決策
        decisions = self.get_decisions_with_outcomes()

        # 2. Claude 分析模式識別
        prompt = f"""
        以下是過去一週的 Agent 決策與實際結果：
        {decisions}
        請分析：
        1. 哪類決策在哪種 regime 下有效？
        2. 哪類決策持續失效？
        3. 需要修改的規則
        4. 輸出更新後的知識條目（JSON）
        """
        new_knowledge = claude.analyze(prompt)

        # 3. 更新知識庫 + 自動更新 Agent Prompt
        self.knowledge_base.update(new_knowledge)
        self.update_agent_prompts(new_knowledge)
```

### 2c. Outcome 自動回填

`scripts/outcome_tracker.py`（新增）— 每日執行：
- 遍歷 7 天前的決策記錄
- 回填 `outcome_7d`：決策後 7 天的績效變化
- 標記 `was_successful`：PF 是否改善

**Phase 2 完成標準**：學習引擎運行 > 4 週，知識庫有 > 4 個 regime 的有效知識。

---

## Phase 3：三 Agent 協作 + 策略版本控制（2 個月後）

> 目標：從「單一 Agent」進化到「專業團隊」

### 3a. Agent 分拆 — `agent/orchestrator.py`（新增）

```python
class AgentOrchestrator:
    async def run_decision_cycle(self):
        regime = self.regime_detector.detect()              # 客觀判斷
        analysis = await self.analyst.analyze(regime)        # 分析師分析
        decision = await self.trader.decide(analysis)        # 交易員決策
        verdict = await self.risk_manager.review(decision)   # 風控審核
        if verdict["approved"]:
            self.execute(decision)
        else:
            self.memory.record_rejection(decision, verdict)
```

三個 Agent 各有獨立 System Prompt：
- **Analyst**：只分析市場，不做交易決策，輸出信號清單 + 信心分數
- **Trader**：基於分析生成具體操作，進場/出場/觀望
- **Risk Manager**：硬性否決權，保護資金安全

### 3b. 策略版本控制 — `agent/version_control.py`（新增）

```python
class StrategyVersionControl:
    def deploy_new_version(self, changes, reason):
        self._snapshot_current()                    # 備份
        self._apply_changes(changes)                # 部署
        self._schedule_rollback_check(hours=48)     # 48h 自動檢查

    def check_and_rollback(self, version_id):
        if pf_after < pf_before * 0.8:             # PF 下降 20%
            self._rollback(version_id)
            self.telegram.send("⚠️ 自動回滾...")
```

### 3c. 脫離 OpenClaw 依賴

Phase 3 完成後，可以選擇：
- 繼續用 OpenClaw 作為 Telegram 介面層
- 或用原生 Python + Claude API + Telegram Bot 完全自建（消除 OpenClaw 安全風險）

---

## 安全機制（6 層防護）

| 層級 | 機制 | 說明 |
|------|------|------|
| L1 | 參數邊界 | 所有參數有硬性上下限，Agent 無法突破 |
| L2 | 行動白名單 | 只能執行預定義行動，無法執行任意代碼 |
| L3 | 信心門檻 | confidence < 0.85 → 強制人工審核 |
| L4 | 冷卻期 | 同類型調整 24h 內只能執行一次 |
| L5 | 自動回滾 | 48h 績效惡化 → 自動恢復上一版本 |
| L6 | 人工覆蓋 | Telegram /override 立即暫停 Agent |

## 利潤驅動解鎖

| Tier | 解鎖條件 | 能力 |
|------|---------|------|
| 0 | 初始 | 僅監控+告警，Haiku only |
| 1 | 累計獲利 > $50 | + 調整槓桿/風險水位 |
| 2 | 月獲利 > $100 × 2 月 | + 暫停/恢復進場 |
| 3 | 月獲利 > $300 × 3 月 | 完全自主管理 |
