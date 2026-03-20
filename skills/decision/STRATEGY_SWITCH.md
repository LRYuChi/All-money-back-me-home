# Strategy Switch v1.0

## 可用策略
- SMCTrend: 1H趨勢+4H確認, 適合明確趨勢
- SMCScalp: 15m執行+1H確認, 適合短線

## 切換條件
- Regime 從 TRENDING → RANGING: 考慮降低倉位或暫停
- 連續3天 PF<1.0: 考慮切換策略
- 信心持續<0.2 超過48h: 啟動反轉做空模式

## 切換流程
1. 記錄切換理由
2. 設定 agent_risk_level=conservative（過渡期）
3. 48h 後評估新策略績效
4. 績效惡化 → 回滾
