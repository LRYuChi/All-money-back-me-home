# Retrospective v1.0

## 週回顧框架

### 分析維度
1. **績效趨勢**：本週 vs 上週（WR/PF/DD）
2. **Regime 適應性**：策略在不同 regime 下的表現差異
3. **決策品質**：Agent 決策的準確率（has outcome 的比例）
4. **數據品質**：STALE 數據源比例、API 失敗率

### 評估標準
- 改善：PF 提升 + WR 穩定/上升
- 穩定：PF ±5% + WR ±3pp
- 惡化：PF 下降 >10% 或 WR 下降 >5pp

### 輸出
{"performance_trend":"improving|stable|declining","regime_fit":{},"decision_accuracy":0.0,"recommendations":[]}
