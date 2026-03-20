# Knowledge Extract v1.0

## 知識提煉框架

### 輸入
- 近30天有結果的決策記錄
- 按 regime 分組
- 含 evidence_ids

### 提煉步驟
1. **分組**：按「行動類型+結果」分組
2. **找共同特徵**：成功交易的共同輸入條件
3. **提煉規則**：只有滿足以下才能提煉：
   - 支持案例 >= 3 筆
   - 勝率 >= 60% 或 <= 30%
   - 有明確觸發條件和預期結果

### 輸出格式
{"rules":[{"title":"","condition":"","expected_outcome":"","evidence_ids":[],"confidence":0.0,"domain":"risk|signal|regime|execution","counter_evidence_ids":[]}],"insufficient_data_areas":[],"anomalies":[]}

### 嚴格禁止
- 無 evidence_ids 的規則
- confidence > 0.8（除非 evidence >= 10）
- 「可能」「或許」等不確定用語在 condition 中
