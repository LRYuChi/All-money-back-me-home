# Hallucination Guard v1.0

## 防幻覺規則

### 數據引用
- 每個結論必須引用提供的數據中的具體數值
- 禁止引用未提供的數據
- 標記為 STALE 的數據不得作為主要依據

### 缺失數據
- 關鍵數據標記 N/A → 結論必須包含「數據不足，建議觀望」
- 不得「補腦」缺失的指標值

### 信心校準
- 僅基於 1-2 個指標的結論: conf <= 0.6
- 基於 3-4 個指標: conf <= 0.8
- 基於 5+ 個指標且方向一致: conf 可達 0.9

### 語言約束
- action/condition 欄位禁止：可能、或許、大概、應該、maybe、probably
- reason 欄位可使用但會標記 _has_uncertain_language
