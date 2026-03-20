# Skill Selector v1.0

## 任務：選擇需要載入的 Skills

### 輸入
trigger: {觸發原因} | priority: {優先級} | regime: {市場機制}

### 選擇規則

#### 必載（每次）
- core/SAFETY
- core/OUTPUT_FORMAT

#### 按優先級
- critical → + decision/EMERGENCY + perception/MARKET_READ
- high → + decision/RISK_DECISION + perception/PERFORMANCE_READ
- medium → + decision/PARAM_ADJUST
- routine → + analysis/DAILY_ANALYSIS + perception/全部

#### 按觸發原因
- 連虧 → + decision/RISK_DECISION
- 機制切換 → + perception/REGIME_READ + decision/STRATEGY_SWITCH
- 知識提煉 → + analysis/KNOWLEDGE_EXTRACT
- 系統異常 → + decision/EMERGENCY
- 每週回顧 → + analysis/RETROSPECTIVE

### 輸出（純 JSON）
{"skills_to_load":["core/SAFETY","core/OUTPUT_FORMAT","..."]}
