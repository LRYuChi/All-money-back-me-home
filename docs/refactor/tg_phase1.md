# TG Refactor Phase 1 — Feature-Flag Isolation of Deprecated Buttons (2026-04-26)

## 背景

`market_monitor/telegram_bot.py`（1653 行、25 按鈕）長期混雜兩個世代的功能：
- 跟目前實際在 trading 的系統有關（SUPERTREND + Polymarket + AI）
- CLAUDE.md 已標記為 deprecated 的子系統（confidence_engine、tw_predictor、market_monitor.ml）

deprecated 按鈕還掛在 TG 介面上會誤導操作員 — 點下去會跑舊路徑、看到跟現在
策略無關的資料，混淆「目前到底是什麼在運作」。

## 範圍

### 保留為 ACTIVE_BUTTONS（11 顆）

| 按鈕 | command | 對應系統 |
|------|---------|----------|
| 📊 全覽 | overview | SUPERTREND 全覽 |
| 📋 持倉 | positions | freqtrade /status |
| 📋 分析 | analysis | 完整儀表板 |
| 💰 交易 | trades | freqtrade /trades |
| 📊 統計 | trade_stats | journal 績效 |
| 📓 日誌 | journal | TradeJournal |
| 🛡 風控 | guards | R97-R104 guards 狀態 |
| 🤖 AI回顧 | ai_review | Claude API（吃當前資料） |
| 🔮 AI預測 | ai_forecast | Claude API |
| ⚠️ AI風控 | ai_risk | Claude API |
| 📰 投顧報告 | advisor_report | 通用 PDF/文字上傳分析 |

### 隔離為 LEGACY_BUTTONS（10 顆，env 預設關閉）

| 按鈕 | command | 對應 deprecated 模組 |
|------|---------|----------------------|
| 🎯 信心 | confidence | `market_monitor.confidence_engine` |
| 🔗 加密環境 | crypto | `market_monitor.crypto_environment` |
| 📈 機制 | regime | `market_monitor.market_regime` |
| 🌍 宏觀 | macro | `market_monitor.macro` |
| 🧠 決策 | decisions | `market_monitor.decisions` |
| 🇹🇼 台股預測 | tw_predict | `market_monitor.tw_predictor` |
| 🏦 台股籌碼 | tw_chips | `market_monitor.tw_predictor` |
| 📉 台股技術 | tw_tech | `market_monitor.tw_predictor` |
| 🧠 台股ML | tw_ml | `market_monitor.ml.predict` |
| 🧠 BTC ML | btc_ml | `market_monitor.ml.predict` |

## 行為

`TELEGRAM_LEGACY_MENU=0`（預設）：

- 鍵盤上**只看得到 ACTIVE 按鈕**（11 顆，分 5 列）
- `/` 自動完成清單**只列 ACTIVE 指令**
- 萬一操作員手打 `/tw_predict` 或截圖貼舊按鈕文字，handler 會回應：
  ```
  ⚠️ 此功能（tw_predict）已停用

  對應的 confidence_engine / tw_predictor / market_monitor.ml 屬於
  Smart Money 跟單系統遷移前的舊架構，已標記 deprecated。

  若臨時需要復活：在 VPS .env 加上
  TELEGRAM_LEGACY_MENU=1
  並 docker compose up -d --force-recreate telegram-bot。
  ```

`TELEGRAM_LEGACY_MENU=1`：所有按鈕全部恢復顯示，handler 照常呼叫舊邏輯。

## 部署 / 回退

**部署**：
```bash
ssh root@VPS "cd /opt/ambmh && git pull && docker compose -f docker-compose.prod.yml up -d --force-recreate telegram-bot"
```

**回退**（讓 deprecated 按鈕重新出現）：
```bash
echo "TELEGRAM_LEGACY_MENU=1" >> /opt/ambmh/.env
docker compose -f docker-compose.prod.yml up -d --force-recreate telegram-bot
```

完全回退到 Phase 1 前的版本：
```bash
git revert <phase1-commit>
```

## Phase 2 / Phase 3（待辦）

### Phase 2（觀察期 1-2 週後）— 清掉 cron 推播裡的 deprecated 引用

- `market_monitor/pipeline.py` 的 `generate_morning_report` / `generate_evening_report` 拆掉 confidence_engine + tw_advisor 部分
- `market_monitor/telegram_zh.py` 把 confidence/macro 相關 helper 標記 `@deprecated`

### Phase 3（Smart Money 上線後）— 物理刪除

- `market_monitor/ml/`、`market_monitor/tw_advisor.py`、`market_monitor/tw_predictor.py`
- `market_monitor/confidence_engine.py`
- 對應的 telegram_bot.py handlers (cmd_tw_*, cmd_*_ml, cmd_confidence, cmd_crypto, cmd_regime, cmd_macro, cmd_decisions)
- deprecated strategies (`bb_squeeze.py`, `smc_scalp.py`, `supertrend_scout.py`) 整批移到 `archive/`

## 測試

`tests/test_telegram_bot_menu_phase1.py`（15 個 case）：

- ACTIVE / LEGACY 不重疊
- BUTTON_MAP 是兩者聯集
- LEGACY_COMMANDS 對應到 LEGACY_BUTTONS values
- ACTIVE 必須包含 trading-essentials（trades, positions, guards, …）
- LEGACY 必須包含 deprecated 子系統（confidence, tw_*, *_ml）
- env 關閉時鍵盤排除 legacy 按鈕；env 開啟時恢復
- env 關閉時 active 按鈕仍可正常 dispatch
- env 關閉時 legacy 按鈕 / `/` 指令回 disabled stub；env 開啟時正常呼叫
- `setup_bot_commands` 註冊 `/` 自動完成清單尊重 env

## 預期使用者體驗

部署後第一次打開 TG bot：

```
[📊 全覽]   [📋 持倉]   [📋 分析]
[💰 交易]   [📊 統計]   [📓 日誌]
[🛡 風控]
[🤖 AI回顧] [🔮 AI預測] [⚠️ AI風控]
[📰 投顧報告]
```

不再有「🇹🇼 台股預測」混在底部讓操作員迷惑「這個現在還在跑嗎？」
