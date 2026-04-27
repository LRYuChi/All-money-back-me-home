# Supertrend 策略運作邏輯與疑慮整理

**日期**：2026-04-27
**作者**：Claude Code review session
**範圍**：在 `feat/smart-money-v1` 上線前，對現役 dry-run 策略 SupertrendStrategy 的完整邏輯盤點 + 風險清單
**資料來源**：`/api/supertrend/operations` 即時快照（VPS：`root@187.127.100.77`）+ `strategies/supertrend.py` 程式碼 + R46–R137 incident 修復系列

---

## 1. 結論摘要（給沒時間看完的）

| 項目 | 狀態 |
|---|---|
| 系統是否在執行 | ✅ 是 — bot.state=running 已 13h，evaluations 持續寫入 |
| 是否在監控訊號 | ✅ 是 — 過去 29.25h 評估 2419 次（≈1.4/min），journal 13 分鐘前才寫過 |
| 是否有開單 | ❌ 過去 24h 0 fire（NO_FIRES_24H 警報，已 by design） |
| 7 日績效 | ⚠️ 201 筆、勝率 67.66%、淨損益 +$3.50、**max drawdown 74.83%** ← **最大紅旗** |
| 「失真感」真因 | ✅ 已釐清 — 不是 bug，是 chop regime + 過度過濾 + 17 天大 drawdown 三件事疊加 |

**P0 必看**：`max_drawdown_pct = 74.83%` 在 +$3.50 淨損益、67% 勝率下高度違和。先弄清是 metric bug 還是真實浮虧。

---

## 2. 整體運作邏輯（從資料源到下單）

### 2.1 資料流

```
OKX 永續合約 ──CCXT──► Freqtrade 容器 ──get_pair_dataframe──► SupertrendStrategy
   (futures)         (拉 OHLCV)                            (本地計算指標)
                              │
                              └─► trading_log/journal/{date}.jsonl  ←  唯一 ground truth
                                          │
                                          ├─► /api/supertrend/* (10 個 dashboard endpoints)
                                          └─► market_monitor.telegram_zh.send_message → TG
```

**全部訊號都是「OKX 原始 K 線 → 本地計算」**。沒有外部 ML、沒有第三方訊號、沒有 Hyperliquid 跟單（那是 `smart_money/` Phase 0–4 的事，目前未啟用）。

### 2.2 時間框架（4 層 MTF）

| TF | 角色 |
|---|---|
| **15m** | base — 主訊號評估循環，每根收盤觸發 |
| 1h | HTF context（`informative_pairs()` 帶入） |
| 4h | HTF 趨勢方向（`dir_4h_score`） |
| 1d | HTF 主趨勢（`st_1d` + `st_1d_duration`） |

額外固定拉 **BTC/USDT 1d** 作市場 regime 參考（`supertrend.py:1046`）。

### 2.3 交易對（18 個，動態產生）

由 7 層 pairlist filter 串接：
```
VolumePairList → AgeFilter → PriceFilter → SpreadFilter
              → RangeStabilityFilter → VolatilityFilter → ShuffleFilter
```

**目前列表**：XRP、ETH、ADA、DOGE、XLM、TRX（高流動性主流幣，6 個）+ DOOD、H、STABLE、PUMP、TURBO、PIEVERSE、SPK、HUMA、ROBO、PENGU、TRIA、SENT（低市值新幣，12 個）

→ **問題**：低市值新幣的 ADX 永遠過不了門檻，過去 7 天有 16 個是 silent_pair（純噪音）。詳見 §4.4。

### 2.4 三層 Tier 進場系統

每層**都要先過共用品質閘門**（§3），再符合 tier 自身條件。

| Tier | 狀態 | 條件 | 倉位 |
|---|---|---|---|
| **confirmed** | ❌ R87 關閉 | 1D+4H+1H+15m 四層全對齊 + 15m flip | — |
| **scout** | ✅ 啟用 | 1D+4H+1H 三層對齊**剛形成這根 K**（edge trigger）+ 15m 還相反 | 中 Kelly |
| **pre_scout** | ✅ 啟用 | 1D+4H 兩層對齊**剛形成** + 1H 尚未對齊 | 0.25 Kelly |

### 2.5 出場、Kelly、Guard

- `EXIT_MODE = weighted` — 4 因子權重出場
- `KELLY_MODE = three_stage_inverted` — **反向 Kelly**（品質越高倉位越小，§4.2 詳述）
- 槓桿 = 1.0x（`SUPERTREND_LEVERAGE_DYNAMIC=0`）
- Guards 全開：CooldownGuard / MaxPositionGuard / DrawdownGuard / DailyLossGuard…
- `LIVE = 0`（dry-run 模式）

---

## 3. 開單門檻（prod 實際生效值）

### 3.1 共用品質閘門（所有 tier 必過）

| 條件 | 門檻 | env 來源 |
|---|---|---|
| ADX | `> 25` | `SUPERTREND_ADX_MIN=default` → 用 class 預設 25 |
| 成交量 | `> volume_ma_20 × 1.0` | `SUPERTREND_VOL_MULT=1.0`（已從 1.2 放鬆） |
| ATR | 必須**上升中** | `SUPERTREND_REQUIRE_ATR_RISING=1` |
| trend_quality | `> 0.5` | `SUPERTREND_QUALITY_MIN=0.5` |
| Funding rate | 不過濾 | `SUPERTREND_FR_ALPHA=0` |
| Regime | 只在 TRENDING 下開 | `SUPERTREND_REGIME_FILTER=1` |
| Guard layer | 全部生效 | `SUPERTREND_GUARDS_ENABLED=1` |

### 3.2 過去 24h blocker 排行（從 `/operations.failures_top` 取）

| Blocker | 觸發次數 | 比重 |
|---|---|---|
| `adx <= 25` | 3993 | #1 |
| `atr_not_rising` | 3082 | #2 |
| `quality <= 0.5` | 2848 | #3 |
| `vol <= 1.2*ma` | 1824 | #4 |
| `vol <= 1*ma` | 1940 | #5 |

→ **總過濾率 100%**：24h 評估 2419 次，tier_fired = 0。

### 3.3 Guard 攔截（過去 24h）

| Guard | 攔截數 |
|---|---|
| CooldownGuard | 18 |
| MaxPositionGuard | 9 |

→ 這 27 次與 §3.2 的「tier_fired = 0」表面矛盾，可能是計算窗口不同（tier_fired strict 24h vs guard rolling 7d），需用 `/skipped` 端點對時戳分布確認。

---

## 4. 疑慮清單（依嚴重性排序）

### 🚨 P0-A：`max_drawdown_pct = 74.83%`（7 日）

**現象**：在 +$3.50 總損益、67.66% 勝率下，系統紀錄的 7 日最大回撤是 **74.83%**。
**為何嚴重**：
- 若初始資金 $1000，意味中間有 ≈ $748 浮虧
- 與「保守設計、低槓桿、edge-trigger only」的所有設計取捨**完全衝突**
- 任何後續討論「要不要放寬門檻」之前，必須先弄清這個數字真假

**可能成因**：
1. Metric 計算 bug（peak equity 取錯起點 / 沒扣 dry_run 初始資金重設）
2. 真實浮虧 — 某幾筆未平倉部位走入深度負區
3. 反向 Kelly 在某個極端 quality 點觸發異常大倉位

**Action**：
```bash
ssh root@187.127.100.77 'curl -sf http://localhost/api/supertrend/snapshot?days=7 | jq ".equity_curve // .drawdown_history"'
ssh root@187.127.100.77 'curl -sf "http://localhost/api/supertrend/trades?limit=200" | jq "[.[] | select(.profit_pct < -0.1)] | sort_by(.profit_pct) | .[0:10]"'
```

### 🚨 P0-B：18 pair 中 16 個是 silent_pair

**現象**：過去 7 天只有 BTC / ETH / ADA 三對有真實交易，其餘 16 對（DOGE、XRP、XLM、TRX 含主流幣 + 12 個低市值新幣）一筆都沒進。

**為何嚴重**：
- VolumePairList 拉進的低市值新幣 ADX 永遠過不了 25 → 永遠不交易，但**它們的 evaluation 算進 24h tick 數**，灌水成「系統很忙」的假象
- 低流動性幣若真開了單，spread / slippage 會吃掉 dry-run 模型沒算到的成本，未來 live 上線會被打臉
- ShuffleFilter 隨機排序意味著相同設定每次部署 whitelist 不同，**結果不可重現**

**Action**：
- 短期：改 `StaticPairList` 鎖定 BTC / ETH / SOL / ADA / DOGE / XRP（5–7 個）
- 長期：若要保留動態 pairlist，加 `MinNotionalFilter` + 更嚴的 `RangeStabilityFilter` 篩掉新幣

### ⚠️ P1-A：反向 Kelly 的長期期望值問題

**現況**：`SUPERTREND_KELLY_MODE = three_stage_inverted`
- 意義：trend_quality 越高 → 下注越小
- 設計動機：怕 quality 假高（survivor bias）被打回原形

**問題**：
- 若 quality 真高你卻故意縮小，等於**主動壓低期望值**
- 對 quality 不信任的正解是**修 quality 計算邏輯**，不是用反向倉位繞過
- 反向 Kelly 沒有可重複驗證的學術 / 業界基礎，是工程性 hedge

**Action**：
- 改回 `three_stage`（正向）並 backtest 對比 inverted 版本
- 若 inverted 真的勝出，要寫一份 R86 follow-up 解釋為何

### ⚠️ P1-B：Confirmed tier 完全砍掉是否過度

**R87 證據**：confirmed tier 佔 86% 交易量、平均 -0.84%、勝率 48% → 全部關閉

**質疑**：
- 「confirmed 在當時的 quality_min / vol_mult 設定下會虧」 ≠「confirmed 永遠該被砍」
- 86% 量能砍掉後，目前只剩 scout（中度）+ pre_scout（最早期），都是「在訊號完整形成前進場」的試水溫 tier
- 缺少「四層完美對齊 + 高 ADX + 高 quality」的高勝率 setup tier
- **長期可能變成「只試水、不收成」結構** — 試水單能跑出 +28.80%（P0-3 backtest）但 scaling 上限低

**Action**：
- 在 `SUPERTREND_QUALITY_MIN=0.7`、`SUPERTREND_ADX_MIN=30` 的更嚴設定下重 backtest confirmed tier
- 若仍負期望，再寫 R87 follow-up 確認永久砍除

### ⚠️ P1-C：100% 過濾率的設計意圖 vs 實際後果

**現況**：5 個 quality gate × 3 個 tier mask × guard layer，24h tier_fired = 0。

**這是設計**（chop regime 不該交易），但體驗上：
- 用戶看不到 TG 通知 → 以為系統死了
- Dashboard 顯示「200 筆歷史交易」 vs 「24h 0 進場」 → 認知失調
- 「失真感」的真因之一

**Action**：
- 不用改門檻（門檻是 by design）
- 改 UX：在 dashboard 加一個 "24h NO_FIRES — 主因：ADX chop（過濾 3993 次）" 的醒目卡片
- 在 TG 排程一個每日 0900「今日 evaluation 摘要」（即使無交易也發），告訴用戶「系統活著、市場 chop、所以沒進場」

### ⚠️ P2：CooldownGuard / MaxPositionGuard 27 次攔截 vs tier_fired = 0 的矛盾

**問題**：如果 24h 沒有任何 tier 通過，為何 guard 還在擋 27 次 entry？

**可能解釋**：
- 計算窗口不同（guard rejections 是 7d rolling，不是 24h strict）
- Guard 在 evaluation 階段也被呼叫一次（不只在 entry）
- 有 race condition — tier 觸發但 strict edge-trigger 又取消了，guard 已記錄

**Action**：對 `/api/supertrend/skipped?limit=30` 的 timestamp 分布做一次驗證即可。

### ℹ️ P3：策略架構性疑慮（與本次修復無關，但寫下來備忘）

1. **整套策略只有單一信心源（Supertrend）+ ADX/ATR/quality 過濾** — 沒有 mean reversion / breakout / range 對手策略，極端 trending 時會 over-fit、極端 chop 時會 0 trade（現況）
2. **Kelly + Guard 雙層風控** 看似完善，但兩者用同一份 win rate / volatility 計算，**相關性 = 1**，不是真正的多層防線
3. **Smart Money 跟單系統** 一旦上線（CLAUDE.md Phase 0–4），這套 Supertrend 應重新定位為 fallback / 對沖角色，否則兩套系統的 PnL 會互相抵消

---

## 5. 優先處理順序（給 owner）

| 優先 | 項目 | 預期工時 | 阻塞下一步嗎 |
|---|---|---|---|
| **P0** | 查 `max_drawdown_pct = 74.83%` 真假 | 30 min | 阻塞所有調參討論 |
| **P0** | 縮 whitelist 到 5–7 主流幣（StaticPairList） | 1h（含 backtest）| 不阻塞但建議優先 |
| P1 | Confirmed tier 在嚴格設定下重 backtest | 2h | 否 |
| P1 | Kelly 改回 three_stage 對比 backtest | 2h | 否 |
| P2 | UX：dashboard 加「為何沒進場」卡片 + 每日摘要 TG | 半天 | 否 |
| P2 | 解 guard 27 次 vs tier_fired 0 矛盾 | 30 min | 否（純查證） |
| P3 | 規劃 Supertrend 與 Smart Money 系統的角色分工 | 規劃半天 | 否（中長期） |

---

## 6. 不在本報告範圍

- Smart Money Phase 0–4 的設計（見 `docs/SMART_MONEY_MIGRATION.md`）
- Polymarket 子系統
- Confidence engine（已 deprecated）
- 舊 SMC / ML / Volty / BB squeeze 策略（已封存於 `archive/`）
- Live trading 上線前 checklist（`SUPERTREND_LIVE=0` 的條件下不適用）

---

## 附錄 A：完整 prod env vars 快照（2026-04-27 05:28 UTC）

```
SUPERTREND_ADX_MIN=default               # = 25 (class default)
SUPERTREND_CORRELATION_FILTER=0
SUPERTREND_DISABLE_CONFIRMED=1
SUPERTREND_EVAL_JOURNAL=1
SUPERTREND_EXIT_MODE=weighted
SUPERTREND_FR_ALPHA=0
SUPERTREND_GUARDS_ENABLED=1
SUPERTREND_GUARDS_REQUIRE_LOAD=0
SUPERTREND_JOURNAL_DIR=/freqtrade/trading_log/journal
SUPERTREND_KELLY_MODE=three_stage_inverted
SUPERTREND_LIVE=0
SUPERTREND_ORDERBOOK_CONFIRM=0
SUPERTREND_QUALITY_MIN=0.5
SUPERTREND_REGIME_FILTER=1
SUPERTREND_REQUIRE_ATR_RISING=1
SUPERTREND_VOL_MULT=1.0
FREQTRADE__FORCE_ENTRY_ENABLE=false
FREQTRADE__TELEGRAM__ENABLED=true
```

## 附錄 B：`/operations` 完整快照節錄

```json
{
  "bot": {"state": "running", "dry_run": true, "strategy": "SupertrendStrategy", "max_open_trades": 3.0},
  "whitelist": {"n_pairs": 18},
  "pipeline": {
    "journal_ok": true,
    "health": {"ok": true, "last_event_ts": "2026-04-27T05:15:01", "events_in_window": 3151},
    "evaluations": {
      "n_evaluations": 2419,
      "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
      "failures_top": {"adx<=25": 3993, "atr_not_rising": 3082, "quality<=0.5": 2848, "vol<=1*ma": 1940, "vol<=1.2*ma": 1824},
      "observed_span_hours": 29.25
    },
    "recent_trades": 201,
    "recent_skipped": 27,
    "guard_rejections_top": {"CooldownGuard": 18, "MaxPositionGuard": 9}
  },
  "performance": {
    "window_days": 7,
    "n_trades": 201,
    "win_rate": 0.6766,
    "sum_pnl_usd": 3.50,
    "max_drawdown_pct": 74.83,
    "top_pairs": [
      {"pair": "BTC/USDT:USDT", "n_trades": 111, "win_rate": 0.6937, "sum_pnl_usd": -1.51},
      {"pair": "ETH/USDT:USDT", "n_trades": 46, "win_rate": 0.5217, "sum_pnl_usd": -10.74},
      {"pair": "ADA/USDT:USDT", "n_trades": 44, "win_rate": 0.7955, "sum_pnl_usd": 15.75}
    ],
    "silent_pair_count": 16,
    "active_pair_count": 3
  },
  "alerts": [
    "NO_FIRES_24H — 2419 evaluations, dominant blocker: adx<=25",
    "GUARD_REJECTING_HEAVILY — CooldownGuard blocked 18 entries in 1d",
    "GUARD_REJECTING_HEAVILY — MaxPositionGuard blocked 9 entries in 1d"
  ]
}
```

## 附錄 C：相關歷史報告

- `docs/reports/r84_supertrend_backtest_findings.md`
- `docs/reports/r86_inverted_kelly_findings.md` ← P1-A 對應
- `docs/reports/r87_disable_confirmed_findings.md` ← P1-B 對應
- `docs/reports/r89_vol_mult_findings.md`
- `docs/reports/r91_quality_gates_design.md`
- `docs/reports/strategy_audit_2026Q2.md`
- `docs/reports/incident_2026-04-26_silent_guards_failure.md`
