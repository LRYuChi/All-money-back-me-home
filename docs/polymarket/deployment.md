# Polymarket Phase 1 Pipeline — 部署與運維

## 架構概覽

```
┌──────────────────┐   */5min    ┌─────────────────────────┐
│   cron (host)    │ ───────────▶│ polymarket_pipeline.sh  │
└──────────────────┘              └───────────┬─────────────┘
                                              │
                             docker compose exec
                                              │
                                              ▼
                         ┌──────────────────────────────────┐
                         │  telegram-bot container          │
                         │  (Python 3.12, has Telegram env) │
                         │                                  │
                         │  python -m polymarket.pipeline   │
                         │      ↓                            │
                         │   data/polymarket.db (SQLite)    │
                         │      ↓                            │
                         │   Telegram API → user            │
                         └──────────────────────────────────┘
```

- Pipeline 本身跑在 `telegram-bot` 容器內（因為那個容器已掛 `market_monitor/` 且有 Telegram env vars）
- 資料寫入 docker named volume `trade-data`（= `/app/data`）
- Cron 在宿主機（VPS），每 5 分鐘觸發 shell wrapper
- Shell wrapper 提供 lockfile（防止重疊）、status file、失敗告警

## 安裝步驟

### 先決條件
- Linux VPS（已有 `docker compose` 運行中的 AMBMH 環境）
- Repo 已部署到 `/opt/ambmh`
- `telegram-bot` 服務運行中
- 環境變數已設定：`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`（在 `/opt/ambmh/.env`）

### 1. 拉最新代碼並重建容器

```bash
cd /opt/ambmh
git pull
# 重建 telegram-bot 使新的 pyyaml 依賴生效（只需要首次）
docker compose -f docker-compose.prod.yml build telegram-bot
docker compose -f docker-compose.prod.yml up -d telegram-bot
```

### 2. 驗證容器內能跑

```bash
# dry-run 單次，確認 Polymarket API 可達 + DB 可寫入
docker compose -f docker-compose.prod.yml exec -T telegram-bot \
    python -m polymarket.pipeline --dry-run --markets-limit 3 --wallets-cap 5
```

預期最後一行類似：
```
[pipeline] markets=3 trades=N wallets=M recomputed=5 tier_changes=0 alerts_sent=0 errors=0
```

### 3. 驗證 wrapper 本身

```bash
# 建立 log 目錄（一次性）
sudo mkdir -p /var/log/ambmh
sudo chown $USER /var/log/ambmh

# 手動跑一次 wrapper（dry 模式一樣使用環境變數）
POLY_EXTRA_ARGS="--dry-run" /opt/ambmh/scripts/polymarket_pipeline.sh

# 查看日誌
tail -30 /var/log/ambmh/polymarket.log

# 查看 status 檔
cat /opt/ambmh/data/reports/polymarket_pipeline_status.json
```

### 4. 安裝 cron 排程

```bash
# 如第一次安裝整個 crontab
crontab /opt/ambmh/cron/crontab

# 或只加 polymarket 這一行到現有 crontab
crontab -e
# 貼入：
# */5 * * * * /opt/ambmh/scripts/polymarket_pipeline.sh
```

驗證：
```bash
crontab -l | grep polymarket
```

### 5. 第一次啟用真實推播

dry-run 穩定跑一天後（無錯誤），從 crontab 移除 `POLY_EXTRA_ARGS=--dry-run`（若有設定）或修改 wrapper。預設 wrapper 已經是真實送 Telegram 模式。

## 日常運維

### 查看運行狀態

```bash
# 最近一次運行結果
cat /opt/ambmh/data/reports/polymarket_pipeline_status.json

# 即時跟日誌
tail -f /var/log/ambmh/polymarket.log

# 最近 24 小時的錯誤
grep -i "fail\|error\|except" /var/log/ambmh/polymarket.log | tail -20
```

### 查看鯨魚資料庫

```bash
docker compose -f docker-compose.prod.yml exec -T telegram-bot \
    sqlite3 /app/data/polymarket.db <<EOF
SELECT tier, COUNT(*) FROM whale_stats GROUP BY tier;
SELECT wallet_address, tier, trade_count_90d, win_rate, cumulative_pnl
FROM whale_stats
WHERE tier IN ('A','B','C')
ORDER BY cumulative_pnl DESC LIMIT 10;
EOF
```

### 手動觸發一次

```bash
/opt/ambmh/scripts/polymarket_pipeline.sh
```

### 調整參數

三種方式改 runtime 參數（無須改代碼）：

**方式 1：修改 wrapper 的環境變數**
```bash
POLY_MARKETS_LIMIT=30 POLY_WALLETS_CAP=50 /opt/ambmh/scripts/polymarket_pipeline.sh
```

**方式 2：crontab 中寫死**
```
*/5 * * * * POLY_MARKETS_LIMIT=30 POLY_WALLETS_CAP=50 /opt/ambmh/scripts/polymarket_pipeline.sh
```

**方式 3：修改業務門檻（鯨魚分層、穩定性比例等）**
編輯 `polymarket/config/pre_registered.yaml`，更新 `set_at` 和 `rationale` 欄位，commit 並重啟容器。

### 日誌輪替

wrapper 不自動輪替。使用 logrotate（`/etc/logrotate.d/ambmh-polymarket`）：

```
/var/log/ambmh/polymarket.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

### 暫停 pipeline

```bash
# 暫時（僅這次 5 分鐘週期）
touch /tmp/polymarket_pipeline.lock

# 永久（註解 crontab 行）
crontab -e
# 在對應行前加 #
```

## 觀測

### Status JSON 欄位

```json
{
  "last_run_start": "2026-04-21 12:00:00",
  "last_run_end":   "2026-04-21 12:01:23",
  "duration_seconds": 83,
  "result": "ok",           // ok | fail
  "exit_code": 0,
  "mode": "docker",
  "markets_limit": 20,
  "wallets_cap": 30
}
```

可寫一個簡單的外部 cron / healthcheck：如果 `result==fail` 連續 3 次或 `last_run_end` 距現在 > 10 分鐘，發警報。

### 失敗告警（Telegram）

wrapper 偵測到 pipeline 非 0 退出時會發 Telegram 訊息，但限流：同一小時內最多 1 則（透過 `/tmp/polymarket_pipeline_alert.state`）。這防止連續失敗造成刷屏。

如要重置告警狀態：
```bash
rm /tmp/polymarket_pipeline_alert.state
```

## 非 Docker 模式（例如本機開發）

```bash
cd /path/to/repo
export USE_DOCKER=0
export PROJECT_ROOT="$(pwd)"
export LOG_DIR="$(pwd)/logs"
export LOCK_FILE="$(pwd)/logs/polymarket.lock"
./scripts/polymarket_pipeline.sh
```

## Windows 開發環境（Task Scheduler）

Windows 沒有 cron，可用 Task Scheduler。最簡做法：

1. 建 PowerShell 腳本 `scripts/polymarket_pipeline.ps1`（內容：`cd D:\All-money-back-me-home; python -m polymarket.pipeline`）
2. `taskschd.msc` → Create Basic Task → Trigger: 每 5 分鐘 → Action: `powershell.exe -File D:\All-money-back-me-home\scripts\polymarket_pipeline.ps1`

但 Phase 1 生產環境應該跑在 Linux VPS。Windows 僅用於本機 dry-run 驗證。

## 故障排除

| 現象 | 可能原因 | 處理 |
|---|---|---|
| status `result=fail` exit_code=1 | Python 例外 | 看 `polymarket.log` 最後 30 行 |
| status `result=fail` exit_code=2 | shell 錯誤（路徑） | 檢查 `PROJECT_ROOT` 與 `.env` |
| `previous run still holding lock` | 前一次 run 卡住 | `ps aux | grep polymarket.pipeline`，必要時 kill |
| Telegram 沒訊息但 pipeline ok | whale_stats 無 A/B/C 鯨魚 | 正常，需要數週累積資料（見 architecture §Phase 1 驗收） |
| `429 rate limited` | Data API 打太快 | 降低 `POLY_WALLETS_CAP` 或加長 cron 間隔 |
| DB lock error | 多個 pipeline 同時寫入 | flock 應防止，若出現代表 lock 路徑錯誤 |
| `pyyaml` not found in container | 容器未重建 | `docker compose build telegram-bot && up -d` |

## 資源消耗估算

- 單次執行 ~60-120 秒（視網路）
- 每小時 12 次 × 每次 ~30 API calls = 360 calls/hour（Polymarket 無官方 quota 但保守使用）
- DB 成長速率：~100 筆 trades + ~30 wallet stats 更新 / 5min ≈ 25 MB/day
- Memory：~100 MB 容器內附加
- CPU：idle > 95%，spike 僅在 API 呼叫時

## Phase 1 → 2 升級路徑

當 `manual_validation.md` 累積 50+ 筆結算紀錄且正期望值時，Phase 2 接手。Phase 2 的紙上交易引擎會**繼續共用**這個 pipeline 的 whale_stats 與 trades 資料，不需要重建資料層——只需新增 `polymarket/strategies/` 與 `polymarket/paper/` 模組。
