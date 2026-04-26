# R112 — backtest reproducibility fully restored (2026-04-26 update)

## UPDATE — R112 100% 修好

完整 git bisect 結果（8 commits × R89 baseline + GUARDS_ENABLED=0）：

| Commit | Trades | Profit | 結論 |
|--------|--------|--------|------|
| R98 (5225948) | 8 | **+$5.324** | reference baseline |
| R99 (ca853ba) | 8 | +$0.901 | leverage active 縮 5x profit |
| R100 (aaea439) | 8 | +$0.901 | (no entry-path change) |
| R103 (3efed77) | 8 | +$0.901 | (no entry-path change) |
| R104 (930d2e7) | 8 | +$0.901 | (no entry-path change) |
| R105 (8f60806) | 8 | +$0.901 | (no entry-path change) |
| **R112 (401ffd5)** | **8** | **+$5.324** | ✅ **完美還原** |

「之前以為 R112 沒修」的原因：第一次 verify 用的 prod strategy.py md5 是
舊版 — git reset --hard 沒徹底清乾淨 working tree（原因不明，可能 cache）。
第二次 git stash + git reset --hard 後 strategy.py md5 = 05cc386...
跟 R112 commit 內容一致，backtest 重跑 8 trades / +$5.324。

## 確診結論

**Culprit 唯一是 R99 leverage class-method change**（從 dead-code 1x → live
1.5-5x），跟 R47 trailing % 衝突造成 P&L 縮 ~83% (5.32→0.90)。R112 把
default 改回 1x 完全還原。

**B-BACKTEST 結論在乾淨環境下重做：**
| Config | Trades | WR | P&L |
|--------|--------|-----|-----|
| R89 baseline | 8 | 100% | +$5.32 |
| R89 + ADX_MIN=20 (R112 cleanenv) | 1 | 0% | **-$3.83** |
| R89 + GUARDS_ENABLED=0 (sanity) | 8 | 100% | +$5.32 (matches baseline) |

R91 ADX_MIN=20 確認**不採用**。實質結論不變（B fail acceptance bar）但
數字更精確 — ADX 放寬不僅沒幫助，還主動破壞 strategy。

## 對下一步的影響

- ✅ Backtest 環境可信
- ✅ R91 matrix 可重新跑其他 knobs (QUALITY_MIN=0.4, REQUIRE_ATR_RISING=0)
- ✅ R85 baseline 也可重跑驗證（之前 -$13.46 / 55.6%）→ A 選項有依據
- ✅ R110 STRONG_TREND_NO_FIRES alert 仍有效
- 下次想試 dynamic leverage 必須先實作 leverage-aware R47 trailing
  + 跑 backtest 確認不再 corrupt

## 真正的 Operator 選項（Backtest 修好後重新評估）

| 選項 | 動作 | Backtest 證據 |
|------|------|---------------|
| **A** | `SUPERTREND_DISABLE_CONFIRMED=0` | R85 -$13.46/55.6% (待重跑驗證) |
| **B** | `SUPERTREND_ADX_MIN=20` | ❌ -$3.83/0% — 確認不採用 |
| **B'** | `SUPERTREND_QUALITY_MIN=0.4` | 待跑 |
| **B''** | `SUPERTREND_REQUIRE_ATR_RISING=0` | 待跑 |
| **C** | 不動，等 chop | 0 cost |
| **D** | regime-aware R87 | 大改，可待 backtest 驗證 |

---

# R112 — Partial fix + second culprit confirmed (2026-04-26 — historical, REVISED above)

## TL;DR

R112 修了第一個 culprit（R99 leverage class method 從 dead-code 變 live
1.5-5x → R47 trailing 衝突）— **但只還原了 backtest 環境的 1/8**：

| Backtest scenario | Trades | P&L | Status |
|-------------------|--------|-----|--------|
| R89 baseline (期望) | 8 | +$5.32 | reference |
| R98 strategy + R89 env | 8 | +$5.32 | ✅ confirmed baseline still reachable |
| pre-R112 main (R99 active leverage) | 1 | -$0.43 | ❌ R99 leverage broke it |
| **R112 main (leverage default 1x)** | **1** | **+$1.95** | ❌ partial fix |
| R112 main + GUARDS_ENABLED=0 | 1 | +$1.95 | ❌ guards aren't culprit |

R112 把 leverage 從 5x 拉回 1x → 1 trade 從爆倉 (-$0.43) 變成微盈 (+$1.95)
= leverage 確實是部分原因。**但還缺 7 trades**。

## 第二個 culprit 候選範圍

`5225948` (R98) 跟 `401ffd5` (R112) 之間 strategies/supertrend.py 改了：

| Commit | Round | 對 entry/exit path 的修改 |
|--------|-------|----------------------------|
| `6f9798e` | R97 | confirm_trade_entry 加 `_check_guards` |
| `ca853ba` | R99 | leverage class method + confirm_trade_exit 加 record_loss/result/trade |
| `aaea439` | R100 | confirm_trade_exit 加 DrawdownGuard.update_equity |
| `3efed77` | R103 | GuardContext.amount = stake (notional/leverage) |
| `930d2e7` | R104 | sys.path.insert before guards import |
| `8f60806` | R105 | SUPERTREND_GUARDS_REQUIRE_LOAD env |
| `401ffd5` | R112 | leverage default 1.0 unless DYNAMIC=1 |

GUARDS_ENABLED=0 跳過 R97 的 _check_guards、R99 的 record_*、R100 的
update_equity → 但 backtest 仍 1 trade. 所以 **R97/R99/R100 的 guard
recordings 也不是 culprit**.

剩下 R103 + R104 + R112。R104 純 import path 不影響 entry. R112
default 1.0 等同 R98 dead-code 1x 行為（已驗證 R98 是 8 trades）.

候選最後縮成：**R103 GuardContext.amount = notional / leverage**.

但 GUARDS_ENABLED=0 時 _check_guards 整個 block 跳過，R103 不會跑。

**矛盾**: 沒有任何 commit 應該在 GUARDS_ENABLED=0 時影響 backtest. 但事實
是影響了。

未確認 hypothesis:
- (i) R99 confirm_trade_exit 的某段 try/except wrapper 即便 GUARDS=0 仍
  影響 freqtrade 內部 trade lifecycle (e.g. 錯誤的 return value)
- (ii) `import sys; import os` (R104) 在 strategy module load 時的副作用
- (iii) freqtrade backtest cache (`ft-data` volume) 在 freqtrade restart
  之間有狀態，所有「之後再跑」的 backtest 都受 corrupt cache 影響

## 真正的 git bisect 在哪裡

要絕對確認需要按 commit 一個個試（pre-R112 流程）：

```bash
ssh root@VPS
cd /opt/ambmh
cp strategies/supertrend.py /tmp/main.py

# 對每個候選 commit：
for commit in 5225948 6f9798e ca853ba aaea439 3efed77 930d2e7 8f60806 401ffd5; do
    git show $commit:strategies/supertrend.py > strategies/supertrend.py
    docker exec \
      -e SUPERTREND_DISABLE_CONFIRMED=1 \
      -e SUPERTREND_KELLY_MODE=three_stage_inverted \
      -e SUPERTREND_VOL_MULT=1.0 \
      -e SUPERTREND_GUARDS_ENABLED=0 \
      ambmh-freqtrade-1 freqtrade backtesting \
      --strategy SupertrendStrategy --timeframe 15m \
      --timerange 20251001-20260330 \
      -c /freqtrade/config/config_dry.json \
      -c /freqtrade/config/config_backtest.json \
      --strategy-path /freqtrade/user_data/strategies \
      2>&1 | grep "Total/Daily Avg Trades" | head -1
    echo "  ↑ commit $commit"
done

cp /tmp/main.py strategies/supertrend.py   # restore
```

8 個 backtest × ~5min each = ~40min total. Once we see "8 trades" → "1 trade"
transition, we have THE culprit.

## 對 prod 的影響評估

**Prod 安全 — 不需要立刻動作**:
- prod 過去 24h tier_fired_count = {0,0,0} → populate_entry_trend 連 mask
  都沒 set → 第二個 culprit 在 prod 不會觸發
- 但是 IF 之後 strategy 條件放寬讓 entries 開始 fire (e.g. CHOPPY regime
  讓 *_just_formed 滿足) → 第二個 culprit 會在 prod 表現出來
- 所以 **不是 emergency，但 strategy 調參/上 LIVE 前必須先解決**

## 真正的下一步

按優先順序：

1. **git bisect 完整跑（~40 min）** — 找確切元凶 commit。我可以後續執行。
2. **找到後 → 寫 minimal repro test** — 讓 CI 抓得住未來 regression
3. **Fix or revert 該 commit 的問題部分** — 保留功能但不破壞 backtest
4. **重新驗證 R89 baseline 還原** — 8 trades / +$5.32
5. **才能繼續 R91 的 quality/adx tuning backtests**

## Operator 現在能做的

**Option A**: 等 git bisect 完成 → 修元凶 → 才能 run R91 tuning backtests
**Option B**: 接受 backtest 不可信，直接在 prod 試 `SUPERTREND_DISABLE_CONFIRMED=0`
            （R85 baseline 已知 -$13.46/55.6%，不需 backtest 重驗）
**Option C**: 不動，等 chop regime 來自然 fire scout/pre_scout

我建議 A（先把工具修好）。

## R112 commit 內容

- strategies/supertrend.py: leverage() default 1.0 unless DYNAMIC=1
- apps/api/src/routers/supertrend.py: switchboard.leverage_dynamic
- tests: 5 leverage tests updated + new default test
- 153 SUPERTREND tests pass
- VPS deployed: 401ffd5

## 文件版本

2026-04-26 — written after R112 deploy + verification revealed second
culprit. 待 git bisect 後更新本檔。
