# Param Adjust v1.0

## 可調參數及安全範圍
| 參數 | 範圍 | 當前 | 說明 |
|------|------|------|------|
| leverage_cap | 1.0-5.0 | 3.0 | 最大槓桿 |
| risk_level | aggressive/normal/conservative/minimal | normal | 風險水位 |
| atr_sl_mult | 1.0-3.0 | 1.87 | 止損ATR倍數 |
| atr_tp_mult | 2.0-5.0 | 3.0 | 止盈ATR倍數 |

## 調整原則
- 每次只調一個參數（隔離變數）
- 調整幅度不超過 20%
- 調整後 48h 觀察期
- PF下降>20% → 自動回滾
