# LIVE Switchover Runbook

從 dry-run 切換到 LIVE 真實交易的完整流程。**每一步都要照做，不要跳過**。

R104 事故證明 silent failure 是真實風險 — LIVE 模式下任何
「以為 guards 在運作但其實沒有」會直接燒錢。本流程的目的是把所有
類似風險壓成 0。

---

## Pre-flight checklist（人工確認）

- [ ] 已連續 dry-run 至少 **2 週**，且過去 7 天有 **≥3 筆**真實 entries（驗證策略真的會 fire）
- [ ] R89 backtest 對應的 6-month 期間還在策略適用範圍內（market regime 沒劇變）
- [ ] OKX 帳戶資金 **≥ $200** USDC
- [ ] OKX API key 設了 trade 權限（**沒**勾 withdraw）
- [ ] Telegram bot 跟你連得通（測試 `/overview` 有回應）
- [ ] 你**接下來 24 小時可以隨時看手機** — LIVE 第一次出問題的處理時間很關鍵
- [ ] 你準備好心理上接受 **「可能立刻虧 5%」** 的可能性
- [ ] 你 README 過 `docs/reports/incident_2026-04-26_silent_guards_failure.md` 知道風險史

如果有任何一項打 ✗ → **不要切 LIVE**。

---

## 自動化 pre-flight（必跑）

```bash
ssh root@VPS
cd /opt/ambmh

# Check 1: deploy-level 5 個檢查 + LIVE-specific 8 個檢查
bash scripts/live_preflight.sh
```

預期輸出最後一行：`✅ LIVE pre-flight PASSED`

如果出現 `❌ LIVE pre-flight FAILED (N blocker(s))`：
- 看每個 ✗ 訊息底下的 fix 指令
- 修完**重跑 preflight**
- 直到全綠才能進入下一步

---

## 切換步驟

### Step 1: 必須先設 fail-closed
```bash
cd /opt/ambmh
# 確保 .env 有這兩行（如果還沒有）：
grep -q "^SUPERTREND_GUARDS_REQUIRE_LOAD=1" .env || echo "SUPERTREND_GUARDS_REQUIRE_LOAD=1" >> .env
grep -q "^SUPERTREND_GUARDS_ENABLED=1" .env || echo "SUPERTREND_GUARDS_ENABLED=1" >> .env

# Recreate freqtrade + api 讓兩邊都吃到新 env
docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade api
sleep 30

# 重跑 preflight 驗證 fail-closed 已生效
bash scripts/live_preflight.sh
```

預期 check 1 顯示：`✓ fail-closed mode active — guards import failure will block entries`

### Step 2: 翻轉 LIVE flag

```bash
# 加上 LIVE flag
echo "SUPERTREND_LIVE=1" >> /opt/ambmh/.env

# Recreate 讓 .env 變動生效
docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade

# 等 30 秒讓 freqtrade 啟動
sleep 30
```

### Step 3: 立即驗證 LIVE 模式真的生效

```bash
# 你應該在 freqtrade logs 看到這行：
docker logs ambmh-freqtrade-1 2>&1 | tail -20 | grep "SUPERTREND_LIVE=1"
# 預期: ⚠️  SUPERTREND_LIVE=1 → LIVE TRADING (real money)

# 確認 freqtrade 內部 dry_run = false
docker exec ambmh-api-1 python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://freqtrade:8080/api/v1/show_config', timeout=5)
d = json.loads(r.read())
print('dry_run:', d.get('dry_run'))
print('exchange:', d.get('exchange'))
print('trading_mode:', d.get('trading_mode'))
"
# 預期: dry_run: False, exchange: okx, trading_mode: futures
```

### Step 4: 監控第一次真實交易

```bash
# 開兩個終端：
# A. tail freqtrade logs
docker logs -f ambmh-freqtrade-1

# B. tail api alerts (每分鐘 refresh)
watch -n 60 'curl -s http://localhost/api/supertrend/operations | python3 -m json.tool | head -40'
```

第一次成功 entry 應該會：
1. TG 推送 🟢 LONG 或 🔴 SHORT 訊息（**不是** 🛡️ Guard 攔截）
2. freqtrade /trades endpoint 出現新 trade
3. 網頁 `/trades` 顯示 1 個 open position

第一次成功 exit 後：
4. TG 推送 💰 或 💸
5. journal 會新增 ExitEvent
6. guards state 的 `daily_loss` / `consecutive_losses` 開始累積

---

## 異常情況 — 立即回滾

任何下列情況**立刻回滾**：

- TG 持續推 🛡️ Guard 攔截但**從沒**出現 🟢/🔴 → guards 設太嚴或 R104 silent failure 變種
- TG 出現 ⛔ 斷路器啟動 → ConsecutiveLossGuard 觸發
- 30 分鐘內出現 ≥3 個 🛡️ + 0 個成交
- Account balance 在 **第一筆 trade 之後 5 分鐘內** 跌超過 5%

### 回滾指令

```bash
# 1. 立刻關 LIVE
sed -i '/^SUPERTREND_LIVE=/d' /opt/ambmh/.env
echo "SUPERTREND_LIVE=0" >> /opt/ambmh/.env

# 2. Recreate freqtrade（會中斷 30s）
cd /opt/ambmh && docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade

# 3. 確認回到 dry_run
docker exec ambmh-api-1 python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://freqtrade:8080/api/v1/show_config', timeout=5)
print('dry_run:', json.loads(r.read()).get('dry_run'))
"
# 預期: dry_run: True

# 4. 如果有 open position，手動到 OKX 平倉（freqtrade 重啟時會放生它們）
```

---

## 上線後 7 天觀察清單

- [ ] 每天看 `/api/supertrend/operations.alerts` — 看有沒有新 alert
- [ ] 每週末看 weekly_review TG 推播
- [ ] 每天看 `guards.daily_loss`，超過 80% 限額會 GUARD_NEAR_DAILY_LIMIT
- [ ] Drawdown peak vs current — `guards.drawdown_peak_equity` 是否正常更新
- [ ] OKX 對帳：journal 的 ExitEvent count 應該 = freqtrade /trades 的 closed count = OKX 顯示的成交筆數

---

## 文件版本

- 2026-04-26 v1 — 隨 R106 + live_preflight.sh 一起 ship
- 對應 commit: 看本文件 git log

如果有更新或踩到本 runbook 沒覆蓋的坑，請更新本文件 + 在 commit message 註明
「runbook update」讓未來操作員看得到。
