# Regime Read v1.0

## 市場機制判讀
**TRENDING_BULL**: HH+HL結構, 信心>0.6, Fear&Greed>50
**TRENDING_BEAR**: LH+LL結構, 信心<0.3, 資金費率異常
**HIGH_VOLATILITY**: ATR>1.5x均值, VIX>30
**ACCUMULATION**: BB擠壓, ATR<0.6x均值, 低波動整理
**RANGING**: 無明確方向

## Regime 對應策略權重
BULL: trend_follow 70% | BEAR: short/cash 70% | HIGH_VOL: cash 50% | ACCUM: breakout 60%

## 輸出
regime_assessment: current_regime, confidence, dominant_factor
