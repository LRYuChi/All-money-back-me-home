# Risk Decision v1.0

## 決策樹（按順序檢查）
1. 連虧>=4? → pause_bot, requires_human=true
2. 日虧>=5%? → adjust_risk(minimal), conf=0.95
3. PF 7日降>=20%? → 分析原因後 adjust_params/switch_strategy
4. 波動HIGH+倉位非minimal? → adjust_risk(conservative)
5. 無以上 → no_action

## 參數調整指引
- ATR_multiplier: 高波動+0.3~0.5, 低波動-0.2
- risk_per_trade: 連虧後降至0.5%, 穩定後逐步恢復
- max_positions: 不確定市場1-2, 明確趨勢3-5

## 信心計算
- 基準: 0.6
- 有歷史相似案例: +0.1
- 多指標同向: +0.1
- 機制明確: +0.1
