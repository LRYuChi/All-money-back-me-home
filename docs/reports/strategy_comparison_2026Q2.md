# 三策略同期回測對照 · 2026 Q2

**回測日：2026-04-23**
**對照範圍：`SupertrendStrategy` (V1) vs `SupertrendStrategyV3` vs `SMCTrend`**
**統一條件：2025-09-01 ~ 2026-03-19（200 日）/ 7 個 USDT 永續對 / 15m TF**
**環境：BTC -52.06%（趨勢下行）**

---

## TL;DR — 反直覺的結論

| 策略 | 總損益 | PF | Sharpe | 勝率 | 最大連敗 | 結論 |
|---|---|---|---|---|---|---|
| **V1（生產中）** | **+28.80%** | **3.20** | **+2.55** | 25.6% | 14 | ✅ **保留**，繼續用 |
| V3（為「修復 V1」設計） | -7.62% | 0.16 | -24.30 | 41.8% | 28 | ❌ **永遠不上線** |
| SMCTrend（2501 行主策略） | **-10.71%** | **0.03** | **-23.89** | **3.3%** | **44** | ❌ **明確淘汰** |

1. **V1 是三者裡明確最好的**——其他兩個都嚴重虧損
2. **V3 證明「啟用 use_custom_stoploss」是錯方向**——這推翻了我上一份 audit 的 P0-2 建議
3. **SMCTrend 的失敗解釋了「為何放棄 SMC 改 Supertrend」**——同期回測 -10.71%、勝率 3.3%、44 連敗、僅 BTC/ETH 觸發訊號
4. **三個策略對「停損夠寬」這件事的反應幾乎一致**：V1 寬 -5% 賺最多，V3 -7% 但加 trailing 反而崩，SMCTrend 動態 ATR 停損平均 -0.93% 最緊最慘

對於上一份 `strategy_audit_2026Q2.md` 的 P0 動作清單，必須**重新校準**：
- ❌ 撤回 P0-2「啟用 use_custom_stoploss」
- ✅ 保留 P0-3（Scout 邊緣觸發）
- ✅ 保留 P0-4（連虧熔斷）
- ✅ 保留 P1-4（暫停 NEAR）

---

## 第 1 章 三個策略各是什麼

### V1 — `SupertrendStrategy`（**正在生產跑的版本**）
- 1d/4h/1h/15m 多時框 Supertrend
- Scout (3 層對齊) + Confirmed (15m flip)
- **stoploss = -5%**, `use_custom_stoploss = False`
- Kelly cap 20%, scout 25% / confirmed 75%
- 平均槓桿 4.24x

### V3 — `SupertrendStrategyV3`（**寫好但未上線**，2026-03-30）
docstring 自稱「Based on 1-week live diagnosis」。修正：
1. stoploss -5% → **-7%**
2. **`use_custom_stoploss = True`**
3. Kelly cap 20% → **12%**
4. Scout 25% → **15%** Kelly, Confirmed 75% → **60%**
- 平均槓桿 4.42x（理論上應較低，但動態計算結果類似）

### SMCTrend（2501 行的「主」策略，但**從未在生產跑過**）
- 1h/4h informative + 15m base
- ICT 方法論：Order Block (OB) + Fair Value Gap (FVG) + BOS/CHoCH
- 多入場類型：1h_grade_a/b（多空）+ mean_rev（多空）
- ATR 動態停損（實測平均 -0.93%）+ R-multiple TP
- Confidence engine 整合（4 sandbox: macro/sentiment/capital/haven）
- hyperopt 設 `use_killzone=0`、`use_adam_filter=0`（已關閉兩個過濾器）
- 平均槓桿 2.49x

---

## 第 2 章 三策略整體指標對照

| 指標 | V1 | V3 | SMCTrend |
|---|---|---|---|
| 總筆數 | **129** | 673 | 121 |
| 期間總損益 | **+28.80%** | -7.62% | -10.71% |
| Profit Factor | **3.20** | 0.16 | **0.03** |
| Sharpe | **+2.55** | -24.30 | -23.89 |
| Sortino | +25.83 | -22.12 | -32.80 |
| Calmar | +146.76 | -9.57 | -9.60 |
| 勝率 | 25.6% | 41.8% | **3.3%** |
| 最大回撤 | **1.88%** | 7.64% | 10.71% |
| 最大連敗 | 14 | 28 | **44** |
| 中位持倉 | **5.5h** | 15min | **0min** |
| 平均槓桿 | 4.24x | 4.42x | 2.49x |
| MFE 平均 | **4.52%** | 0.73% | 0.26% |
| MAE 平均 | 1.52% | 0.71% | 0.53% |

**讀法**：
- V1 在每個關鍵指標都領先
- SMCTrend 中位持倉 **0 分鐘**——大部分交易在進場那一根 K 線就被掃出
- SMCTrend 勝率 3.3%（4 勝 117 敗）= 樣本完全偏向災難
- 三策略平均 MFE 衰退 4.52% → 0.73% → 0.26%——**停損越緊，能捕捉的順向走勢越短**

---

## 第 3 章 V1 vs V3 — V3「修復」失敗的解剖

### 3.1 V3 為何崩壞

**根因：V3 的「保本 +0.3% 鎖」trailing stop 在 15m 級噪音中變成 hair-trigger。**

V3 邏輯：
```
profit_pct >= 1.5%（多）→ stop = +0.3%
profit_pct >= 4%   → stop = 30% of max profit
profit_pct >= 8%   → stop = 50% of max profit
```

問題：
1. 一旦觸到 +1.5%，停損從 -7% 跳到 +0.3%
2. 15m 級的反向 wick 經常 1-2% 內就把價格拉回 +0% 附近
3. 觸發 +0.3% 鎖損 → 「成功保本」但**錯過了後續可能的 5%、10%、+50% 行情**
4. 重複 629 次（V3 的 trailing_stop_loss 出場數）→ 大部分有正向苗頭的單子都被掐死

### 3.2 退出原因對比

| 退出原因 | V1 筆數 | V1 PnL | V3 筆數 | V3 PnL |
|---|---|---|---|---|
| `trailing_stop_loss` | 30 | -$60.86 | **629** | -$61.08 |
| `stop_loss` | 49 | -$52.81 | 16 | -$9.23 |
| `multi_tf_exit` | 39 | **+$86.89** | 28 | -$5.90 |
| `daily_reversal_exit` | **7** | **+$230.02** | **0** | — |
| `time_decay` | 2 | +$25.15 | 0 | — |
| `force_exit` | 2 | +$59.59 | 0 | — |

**關鍵教訓**：策略的「edge」在於 11 筆極端勝局（7 daily_reversal + 2 force + 2 confirmed）佔 V1 總利潤 140%。V3 把這 11 筆中的每一筆都在小賺時掐死。

### 3.3 V3 的設計者警告了自己

V3 docstring 原文：
> 「V2 的 custom_stoploss 在 15m 回測中會崩壞 PF（已驗證 3 次）。
>  V3 只做保本保護，不做激進追蹤」

V3 設計者意識到問題但選擇了「降低 trailing 強度但不關掉」——這個半吊子方案結果反而崩壞更嚴重，因為：
- V2 的 trailing 鎖 50-70% 利潤，需要先有大利潤才觸發
- V3 的「保本」trailing 反而**門檻更低**（1.5% 就觸發），更容易掃出

### 3.4 對上一份稽核報告的反思

`strategy_audit_2026Q2.md` 的 P0-2 寫：
> 啟用 `use_custom_stoploss = True`

**這個建議基於我對策略代碼的分析，但沒有實證**。V3 提供了實證：**啟用 custom_stoploss 在當前 trailing 邏輯下會讓 PF 從 3.20 崩到 0.16**。

**修正後的 P0-2**：
- ❌ 撤回「啟用 `use_custom_stoploss`」
- ✅ 保持 V1 的 `use_custom_stoploss = False`
- ✅ 如果未來想加 trailing：門檻必須**遠遠高於 +1.5%**（建議 ≥ +10%）；trailing 比例不能太密（建議 ≥ +15% 才鎖 30%）
- ✅ 任何 trailing 改動必須先 **WFO 驗證**才能上線，不能直接套用「程式碼可讀但未驗證」的設計

---

## 第 4 章 V1 vs SMCTrend — 為何放棄 SMC 改 Supertrend

### 4.1 SMCTrend 的崩壞模式比 V3 更嚴重

| 指標 | V1 | SMCTrend | 差異 |
|---|---|---|---|
| 總筆數 | 129 | 121 | 持平 |
| 勝率 | 25.6% | **3.3%** | -22.3 pp |
| 期間總損益 | +28.80% | -10.71% | -39.5 pp |
| Profit Factor | 3.20 | **0.03** | 幾乎為零 |
| 最大連敗 | 14 | **44** | -30 |
| 中位持倉 | 5.5h | **0min** | 同根 K 線即出 |
| MFE 平均 | 4.52% | 0.26% | 趨勢完全沒展開 |
| ATR 動態停損 | n/a | **avg -0.93%** | **比 V1 的 -5% 緊 5.4x** |

**SMCTrend 的問題鏈**：
1. ATR 動態停損自動算到 ~0.9-1.4%（依當下波動率）→ **比 V1 的 5% 緊 5x**
2. 15m 加密期貨噪音遠遠超過 1% → 進場後幾乎必然觸停損
3. 中位持倉 **0 分鐘** → 進場那根 K 線就被掃出
4. 不斷重複 → 117 筆虧損、4 筆勝、44 連敗

### 4.2 SMCTrend 的入場標籤

| 入場標籤 | 筆數 | PnL | 勝率 |
|---|---|---|---|
| `mean_rev_long` | **57** | -$52.41 | 1.8% |
| `mean_rev_short` | 32 | -$30.56 | 0% |
| `1h_grade_b_long` | 24 | -$20.00 | 4.2% |
| `1h_grade_b_short` | 8 | -$4.15 | 25% |
| `1h_grade_a_long` | **0** | — | — |
| `1h_grade_a_short` | **0** | — | — |
| `reverse_confidence_*` | 0 | — | — |

**關鍵發現**：
- **「1h_grade_a」（最高品質）入場從未觸發**
- 觸發的全是 grade_b 與 mean_rev（次級品質）
- mean_rev_long 57 筆勝率 1.8%（1 勝 56 敗）— 完全反指標
- 「reverse_confidence」入場（信心引擎降低時的反向交易）也從未觸發

**意涵**：SMCTrend 的設計把所有「品質好的入場」門檻訂得太高，永遠不會發生。實際發生的全是「降級入場」，但降級入場的 edge 顯著為負。**整套 2501 行代碼設計的精華（grade_a + reverse_confidence）在實際資料上死碼**。

### 4.3 SMCTrend 只在 BTC + ETH 觸發

| 幣對 | SMCTrend 筆數 |
|---|---|
| BTC | 48 |
| ETH | 73 |
| AVAX, NEAR, ATOM, ADA, DOT | **0** |

其他 5 個幣對在整個 200 天 backtest 中**完全沒被 SMCTrend 觸發任何訊號**。可能原因：
- ATR 過濾或 funding rate 過濾排除
- OB/FVG/BOS 條件太嚴格
- 信心引擎在這些幣對上的分數總是不夠

無論如何結果是：**SMCTrend 在 7 個幣對中只用到 2 個，浪費了大部分 universe**。

### 4.4 V1 對 SMCTrend 的明確優勢

| 維度 | V1 | SMCTrend | V1 勝出 |
|---|---|---|---|
| 收益 | +28.80% | -10.71% | ✅ |
| 風險（最大回撤） | 1.88% | 10.71% | ✅ |
| 一致性（最大連敗） | 14 | 44 | ✅ |
| 幣對覆蓋（7 個） | 7/7 | 2/7 | ✅ |
| 訊號品質設計 | 簡潔（trend quality 一個閘門） | 過度複雜（15+ 條件、信心引擎） | ✅ |
| 代碼維護性（行數） | 521 行 | **2501 行** | ✅ |
| Hyperopt 調校 | 開箱即可 | 需要關閉多個 filter | ✅ |

**結論**：「為何放棄 SMC 改 Supertrend」這個謎已解：SMCTrend **在實際資料上是個輸家**。可能是某個歷史時段表現不錯（無法驗證，因為現有代碼可能與當時不同）但當前版本**配當前 hyperopt 參數**就是糟糕。Supertrend 設計簡單卻在同一資料上 +28.8%——**簡單性勝出**。

### 4.5 SMCTrend 該怎麼處理？

選項 A：**淘汰** — 把 `strategies/smc_trend.py` 移到 `archive/` 目錄，保留 git 歷史。
選項 B：**重寫核心邏輯** — 大幅放寬 ATR 停損（最低 3-5%）+ 重新校準 1h_grade_a 條件
選項 C：**保留作為「策略動物園」候選** — 不上線，但可以後續持續 backtest 觀察是否在某些 regime 下變強

**建議選項 A**：2501 行代碼維護成本極高，當前實證為負期望值。WFO 後若新版本能在多個時段都正向再考慮復活。

---

## 第 5 章 三策略總結與行動建議

### 5.1 確定可說的

1. **V1 (`SupertrendStrategy`) 是當前最佳方案** — backtest +28.80% / Sharpe 2.55 / max DD 1.88%
2. **V1 的 `use_custom_stoploss = False` 不是 bug，是 feature** — 啟用會把 PF 從 3.20 崩到 0.16（V3 證實）
3. **V1 的實盤虧損 -3.78% (1 個月) 不是策略破產** — 是「無趨勢期 + Scout 過度頻繁 + NEAR 拖累」三件事疊加
4. **SMCTrend 與 V3 都應淘汰** — backtest 無一倖存

### 5.2 修正後的 P0 動作清單（取代 audit 的版本）

| 動作 | 原 P0 | 校準後 P0 |
|---|---|---|
| P0-1 停止調參 | ✅ | ✅ 保留 |
| P0-2 啟用 use_custom_stoploss | ✅ | **❌ 撤回**（V3 已證明會崩） |
| P0-3 Scout 改邊緣觸發 | ✅ | ✅ 保留（V1 backtest 已是邊緣觸發） |
| P0-4 連虧 3 筆暫停 12h | ✅ | ✅ 保留 |
| **新增** P0-5 暫停 NEAR | — | ✅ 新增（backtest 與實盤雙重背書）|
| **新增** P0-6 維持 SupertrendStrategy 不變 | — | ✅ **新增 — 不要切到 V3 或 SMCTrend** |

### 5.3 中期方向（取代 audit 的 P2-1）

原 P2-1 寫：「重新評估 SMCTrend vs Supertrend」 — **已完成**，結論是 Supertrend 勝。

新 P2-1：**保護 V1 的「跨日趨勢長持」edge**
- 不動 stoploss = -5% 與 use_custom_stoploss = False
- 加入 regime detection：在無趨勢期降低交易頻率（不是停止策略，而是減少 Scout 觸發頻率）
- 監控 `daily_reversal_exit` 與 `confirmed` 的觸發次數，每月檢視
- 如果連續 30 天都 0 次 daily_reversal/confirmed，觸發人工檢查（市場可能進入長期 chop）

### 5.4 不應做的事

- ❌ 不要把 V3 推上生產
- ❌ 不要在 V1 上啟用 `use_custom_stoploss = True`
- ❌ 不要重啟 SMCTrend（除非完全重寫並 WFO 驗證）
- ❌ 不要因為 V3 / SMCTrend 的勝率高就誤以為它們「比較穩」（PF 才是真正指標）

---

## 第 6 章 實證對「stoploss 越緊越保守」這個直覺的反駁

三策略對「停損寬度」的處理方式與結果：

| 策略 | 停損 | PF | 結論 |
|---|---|---|---|
| V1 | 平坦 -5% | **3.20** | 最賺 |
| V3 | 寬 -7% + 1.5% 後保本 trailing | 0.16 | 慘賠 |
| SMCTrend | 動態 ATR 平均 -0.93% | **0.03** | 最慘 |

**反直覺結論**：在 15m 加密期貨上，停損越緊（不論平坦或 trailing），策略越虧錢。

**機制**：
- 加密期貨噪音極大，1-2% 反向 wick 是常態
- 緊停損 + 高頻入場 → 大部分趨勢苗頭被當噪音掃出
- 寬停損 + 接受 -5% 大虧 → 換到能等到 +20%、+50%、+100% 的爆發單
- 用 1 個 +73% 賺回 5 個 -5%，淨勝；緊停損則無此可能

這不是普世真理（不同 TF、不同資產可能不同），但在**當前 universe 上有強烈實證**。

---

## 附錄

- V1 完整資料：`docs/reports/strategy_backtest_2026Q2.md`
- 實盤稽核（需校準）：`docs/reports/strategy_audit_2026Q2.md`
- 分析腳本：`scripts/analyze_backtest.py`
- 原始資料：
  - `data/audit/backtest_supertrend/`（V1）
  - `data/audit/backtest_v3/`（V3）
  - `data/audit/backtest_smc/`（SMCTrend）

## 附錄 B：回測指令

```bash
# V3
ssh root@187.127.100.77 "docker compose ... freqtrade backtesting \
  --strategy SupertrendStrategyV3 \
  --strategy-path /freqtrade/user_data/strategies \
  --timeframe 15m \
  --timerange 20250901-20260319 \
  --pairs BTC ETH AVAX NEAR ATOM ADA DOT (futures) \
  --export trades"

# SMCTrend (同上但 --strategy SMCTrend)
```

---

**報告版本：1.0** · **三策略 backtest 完整對照已完成**
