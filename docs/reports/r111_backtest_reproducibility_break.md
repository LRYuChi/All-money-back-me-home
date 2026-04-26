# R111 — Backtest Reproducibility Break (2026-04-26)

## TL;DR

**B-BACKTEST 結論**：`SUPERTREND_ADX_MIN=20` fail acceptance bar — 不採用。

**但更嚴重的發現**：跑 R89 baseline 設定（理應產出 8 trades / +$5.32）只得 **1 trade / -$0.43**。
本 session 內某個 R97-R110 改動破壞了 backtest 的可重現性，
所有 R91 design doc 列的 backtest matrix 都不能再以 R89 baseline 對照。

## Backtest 結果矩陣

同一段 `20251001-20260330`，三個配置：

| Variant | env 額外設定 | Trades | WR | P&L |
|---------|--------------|--------|-----|-----|
| **R89 baseline (期望)** | (R89 三條 env) | **8** | **100%** | **+$5.32** |
| R89 baseline (現在重跑) | 同上 | 1 | 0% | -$0.43 |
| R89 + ADX_MIN=20 | + `ADX_MIN=20` | 1 | 0% | -$0.43 |
| R89 + GUARDS_ENABLED=0 | + 關 R97 guards | 1 | 0% | -$0.43 |

關 guards 不能還原 8 trades → 不是 R97 的問題。

## 三個配置都同一筆 trade

同一筆 BTC short scout trade 在 2026-01-21 16:15，trailing_stop_loss exit
-0.65%。stake 65 USDT。但 R89 8 trades 大多是 long。**現在這個 backtest 環境
缺了 7 個 long entries**。

## 最可能的 Culprit

### R99 leverage class method fix

R97-R98 之前：`leverage()` 是 nested function 在 `_arrow` 內，dead code。Freqtrade
fall back 到 config default = **1x**。R99 修好之後變動態 1.5-5x。

R89 8 trades baseline 是在 leverage=1x 跑出來的。同一段歷史資料用
leverage=1.5-5x 會：
- 觸發 stoploss / trailing stop 的 P&L threshold 不一樣（5x 倍放大）
- R47 trailing stop 階段切換時機不同
- custom_stake_amount 計算的 stake 對比 freqtrade 配額也變了

但這應該影響 P&L，不應該讓 entry mask 從 8 → 1。除非...

### R47 trailing stop / custom_stoploss 跟 leverage 互動

R47 設計是 `-5% initial SL → +1% break-even → trailing 3 phases`. 在 leverage=5x
下這些 % 就放大了 25 倍 — 第一筆 entry 觸發後可能立刻 SL 出場（一根
candle 內 -1% 就 = -5% leveraged），freqtrade 視為 0:00:00 duration trade，
然後 max_open_trades=3 + stake config 互動下 freqtrade 可能不再開新倉位。

實際上 backtest output 顯示 `Avg Duration 0:00:00` — entry 之後立刻 exit。
這就是 leverage 太高 + R47 設計不相容。

## 為什麼 prod (dry-run) 沒這個問題？

過去 24h prod 是 `tier_fired_count = {0,0,0}` — populate_entry_trend 連
mask 都沒 set，根本沒到 confirm_trade_entry / leverage 那一層。**Prod 是
策略條件不滿足**，跟 backtest 是兩個不同問題。

但兩個問題都是 leverage 修復的後遺症：
- Prod: 條件嚴所以沒 fire → leverage 不會生效 → 看不到副作用
- Backtest: 6 個月歷史中有些 candle 滿足條件 → fire 後 leverage 5x → R47
  設計在新 leverage 下立刻 SL → 後續 entries 也表現異常

## 建議下一步（按優先序）

### 1. 立即 — 不要當作 R89 baseline 還在
`docs/reports/r91_quality_gates_design.md` 列的 acceptance bar
（WR ≥ 87.5% / P&L > +$5.32 / Max DD ≤ 1%）**不再有效**。要先重建 baseline。

### 2. 短期 — 確認 R99 是 culprit
切到 R99 之前的 commit (例如 R98 對應 commit `5225948`) 跑 R89 baseline。
如果還原 8 trades → 確診 R99 是元凶。

### 3. 中期 — leverage-aware R47 trailing
方案 A: 在 leverage > 1 時把 SL/trailing 的 % 除以 leverage（讓實際 P&L
threshold 不變）。
方案 B: 把 leverage 上限從 5x 降到 2x — 跟 R86 backtest 環境更接近。

### 4. 長期 — 統一 backtest 跟 prod 的 leverage path
要嘛兩邊都 1x（取消 R99 leverage 修復），要嘛兩邊都 dynamic（接受 backtest
數字會不一樣）。混用是真實 backtest pollution 的根源。

## R110 STRONG_TREND_NO_FIRES alert 仍是對的

R110 在 24h aggregate 上看到 confirmed_disabled_R87 270 hits — 但
just_formed=False 比它多，所以 alert 不觸發。**設計沒問題**。但這個結論
跟 backtest 結論獨立 — backtest 環境破了不影響 prod alert 邏輯。

## 給用戶的選項更新

原來的 4 個選項裡 **B 跟 D 都不可行**：
- B: backtest 不可信
- D: 同樣需要 backtest 驗證 → 也不可信

剩下：
- **A**: `SUPERTREND_DISABLE_CONFIRMED=0` — 沒 backtest 證明，但 R85 已有
  -$13.46 baseline 知道大概期望值
- **C**: 不動，等 chop regime 自然 fire scout/pre_scout

我建議：**先做下一步 #2（確診 R99 culprit）**。如果是 R99，再決定要不要 revert
或實作 leverage-aware trailing。先別動 R87 設定。

## Reproduce

```bash
ssh root@VPS

# Variant 1: R89 + ADX_MIN=20 (B-BACKTEST 主要結果)
docker exec \
  -e SUPERTREND_DISABLE_CONFIRMED=1 \
  -e SUPERTREND_KELLY_MODE=three_stage_inverted \
  -e SUPERTREND_VOL_MULT=1.0 \
  -e SUPERTREND_ADX_MIN=20 \
  ambmh-freqtrade-1 freqtrade backtesting \
  --strategy SupertrendStrategy --timeframe 15m \
  --timerange 20251001-20260330 \
  -c /freqtrade/config/config_dry.json \
  -c /freqtrade/config/config_backtest.json \
  --strategy-path /freqtrade/user_data/strategies

# Variant 2: same as R89 baseline — should be 8 trades but isn't
# (ADX_MIN= default, three_stage_inverted, vol=1.0)
docker exec \
  -e SUPERTREND_DISABLE_CONFIRMED=1 \
  -e SUPERTREND_KELLY_MODE=three_stage_inverted \
  -e SUPERTREND_VOL_MULT=1.0 \
  ambmh-freqtrade-1 freqtrade backtesting [...同上...]

# Variant 3: R89 + guards 關
docker exec \
  -e SUPERTREND_DISABLE_CONFIRMED=1 \
  -e SUPERTREND_KELLY_MODE=three_stage_inverted \
  -e SUPERTREND_VOL_MULT=1.0 \
  -e SUPERTREND_GUARDS_ENABLED=0 \
  ambmh-freqtrade-1 freqtrade backtesting [...同上...]
```
