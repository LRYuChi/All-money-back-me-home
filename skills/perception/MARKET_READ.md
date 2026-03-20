# Market Read v1.0

## 數據有效性（優先檢查）
1. 每個指標檢查時間戳
2. 超過 TTL 標記 STALE，不得作為主要依據
3. 關鍵數據缺失 → 結論含「數據不足」

## 解讀框架
**Funding Rate**
> +0.1%: 多方過熱→注意回調 | < -0.1%: 空方過熱→注意反彈 | 中間: 中性

**ATR Ratio（當前/30日均值）**
> 1.8: HIGH_VOL→降倉位 | < 0.6: LOW_VOL→等待突破 | 其他: NORMAL

**信心分數**
> 0.6: 可積極操作 | 0.3-0.6: 謹慎 | < 0.3: 防禦/反轉做空

## 輸出
market_summary 必須含：regime, key_risks(max3), data_quality(good|degraded|poor)
