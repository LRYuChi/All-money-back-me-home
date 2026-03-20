# Safety Skill v1.0

## 硬性規則（永遠遵守，不得繞過）
- LEVERAGE: max=5x | reversal_max=2x | high_vol_max=1.5x
- RISK_PER_TRADE: max=2% total_capital
- DAILY_LOSS: >=5% → pause | >=10% → circuit_breaker
- CONSECUTIVE_LOSS: >=4 → pause_entries + notify_human
- REVERSAL_SHORT: max_position=30% capital, max_leverage=1.5x

## 自動否決
- 單日虧損超過 10% 的任何決策
- 槓桿超過當前 regime 允許上限
- circuit_breaker 啟動期間的任何進場

## 輸出約束
- confidence > 0.85 需 evidence >= 3 個數據引用
- requires_human: true 當 confidence < 0.7 或 action=pause_bot
