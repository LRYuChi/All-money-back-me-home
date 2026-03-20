# Emergency v1.0

## 緊急觸發條件
- BTC 24h 跌幅 > 10%
- VIX > 35
- API 錯誤 >= 3次/小時
- Bot 靜默 > 30分鐘

## 應對流程
1. 立即暫停所有新進場 (pause_entries=true)
2. 發送 Telegram 告警
3. 等待人工確認後恢復
4. 不得自動恢復（requires_human=true）

## 絕對禁止
- 緊急狀態下增加槓桿
- 緊急狀態下開新倉
- 跳過人工確認自動恢復
