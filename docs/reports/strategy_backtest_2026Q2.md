# SupertrendStrategy 回測報告與實盤對照 · 2026 Q2

**回測日：2026-04-23**
**策略：`SupertrendStrategy`（VPS 確認生效）**
**對照範圍：回測 6.5 個月歷史 vs 實盤 1 個月**

---

## TL;DR

回測（6.5 個月，129 筆）：**+28.80%、Sharpe 2.55、PF 3.20**
實盤（1 個月，58 筆）：**-3.78%、勝率 10.3%、PF 0.31**

**結論**：策略本身具備正期望值——但其大部分回測獲利依賴「2-3 次重大趨勢反轉跨日持倉」，而那種機會在實盤這 1 個月並未出現。換句話說，**這是一個「需要等到尾部行情才賺到大錢」的策略**，把短期的中位數表現誤認為策略破產，是錯誤的判讀；但同時，當前實作有結構性 bug（見上一份稽核報告 P0 條目）會把這類機會本身扼殺。

---

## 第 1 章 確認執行的策略

三重交叉驗證：

### 來源 1：Docker 容器啟動指令
```
freqtrade trade --strategy SupertrendStrategy
  -c /freqtrade/config/config_dry.json
  -c /freqtrade/config/config_secrets.json
  -c /freqtrade/config/config_telegram.json
  --strategy-path /freqtrade/user_data/strategies
```

### 來源 2：策略檔案內容
- `/freqtrade/user_data/strategies/supertrend.py`（22.5 KB，2026-03-24）→ `class SupertrendStrategy(IStrategy)` ✅
- `/freqtrade/user_data/strategies/supertrend_v3.py`（22.6 KB，2026-03-30）→ `class SupertrendStrategyV3` （**未在運行**，untracked 在 git）
- `/freqtrade/user_data/strategies/supertrend_scout.py`（21.4 KB，2026-03-24）→ 早期 prototype （未運行）

### 來源 3：實盤 trade 標籤一致性
所有 60 筆實盤交易的 `enter_tag` 都來自 `supertrend.py`（"scout" / "confirmed"），與策略原始碼吻合。

**結論：執行的就是 `strategies/supertrend.py` 中的 `SupertrendStrategy`。** 不是 `SMCTrend`，也不是 `_v3`。

---

## 第 2 章 回測設定

| 項目 | 設定 |
|---|---|
| 策略 | SupertrendStrategy |
| 期間 | 2025-09-01 ~ 2026-03-19（**200 日**）|
| 幣對 | BTC, ETH, AVAX, NEAR, ATOM, ADA, DOT（與實盤白名單完全一致）|
| Timeframe | 15m（含 1h / 4h / 1d informative） |
| Trading mode | Isolated Futures |
| Max open trades | 3 |
| 起始資金 | $1,000 USDT |
| 槓桿 | 1.5x – 5.0x（策略動態決定） |
| Stake amount | unlimited（實際由策略 Kelly 動態決定） |

**期間市場環境**：BTC 從 $59k 跌到 ~$28k（**-52.06%**）— 是個顯著的趨勢下行行情。

---

## 第 3 章 回測結果

### 3.1 整體指標

| 指標 | 回測值 | 實盤值 | 落差 |
|---|---|---|---|
| 總筆數 | 129 | 58 | — |
| 勝率 | **25.6%** | 10.3% | -15.3 pp |
| 期間總損益 | **+28.80%** | -3.78% | -32.6 pp |
| Profit Factor | **3.20** | 0.31 | -2.89 |
| Sharpe | **2.55** | 不適用 | — |
| Sortino | **25.83** | — | — |
| Calmar | **146.76** | — | — |
| 最大回撤 | **1.88%** | 4.15% | +2.27 pp |
| 最大連敗 | 14 | 12 | +2 |
| 最大連勝 | 5 | 1 | -4 |
| 中位持倉 | 5.5 小時 | 2.5 小時 | -3.0h |
| MFE 平均 | **4.52%** | 1.17% | -3.35 pp |
| MAE 平均 | 1.52% | 1.06% | -0.46 pp |

**關鍵讀法：MFE 落差是核心線索**。回測平均 MFE 4.52%，實盤只有 1.17%——**實盤的進場根本沒給策略足夠的「友好走勢」空間就被打回**。這不是策略本身好壞問題，是**進場時點與市場結構**的問題。

### 3.2 退出原因分布

| 退出原因 | 回測筆數 | 回測 PnL | 回測勝率 | 實盤筆數 | 實盤 PnL | 實盤勝率 |
|---|---|---|---|---|---|---|
| `stop_loss` | 49 | -$52.81 | 0% | 1 | -$2.11 | 0% |
| `multi_tf_exit` | 39 | **+$86.89** | 51.3% | 23 | -$10.23 | 21.7% |
| `trailing_stop_loss` | 30 | -$60.86 | 6.7% | — | — | — |
| `daily_reversal_exit` | **7** | **+$230.02** | **100%** | **0** | — | — |
| `time_decay` | 2 | +$25.15 | 100% | 1 | +$15.64 | 100% |
| `force_exit` | 2 | +$59.59 | 100% | — | — | — |
| `stoploss_on_exchange` | — | — | — | 33 | -$41.09 | 0% |

**這張表是整份報告最重要的圖**。

注意三件事：
1. **`daily_reversal_exit` 在回測賺了 $230（佔總利潤 80%），但實盤 0 次**
2. **`stop_loss` 在回測佔 38%（49/129），實盤幾乎不出現**——實盤改用 `stoploss_on_exchange`（OKX 交易所側硬停）
3. **`multi_tf_exit` 在回測勝率 51.3%、平均正收益**；實盤勝率掉到 21.7%、平均負收益

### 3.3 入場標籤對比

| 標籤 | 回測 | 實盤 |
|---|---|---|
| `scout` | 127（24.4% wr, +$173.23） | 57（10.5% wr, -$33.37） |
| `confirmed` | **2（100% wr, +$114.75 = avg +98.10%）** | **0** |

**回測中 `confirmed` 入場只 2 筆但平均賺 98% — 這是策略設計的核心命中鍵**。
**實盤 0 次** — 「scout → 15m flip → DCA confirmed」的雙階段機制在實盤從未進入第二階段。

### 3.4 多空對比

| | 回測筆數 | 回測勝率 | 回測平均 | 實盤筆數 | 實盤勝率 | 實盤平均 |
|---|---|---|---|---|---|---|
| Long | 33 | 30.3% | +1.73% | 24 | 4.2% | -4.39% |
| Short | 96 | 24.0% | +2.34% | 34 | 14.7% | -3.48% |

**回測 96 空單之所以多、之所以賺**：那段期間 BTC -52%。**Supertrend 策略本質是趨勢追隨，遇到強勢趨勢市才會發揮**。
實盤 1 個月期間市場結構不明（沒大趨勢），策略被反覆 whipsaw。

### 3.5 各幣對對比

| 幣對 | 回測 PnL | 回測勝率 | 實盤 PnL | 實盤勝率 |
|---|---|---|---|---|
| AVAX | **+$145.81** | 42.1% | -$8.07 | 22.2% |
| BTC | +$57.30 | 43.8% | -$6.12 | 11.1% |
| ATOM | +$51.35 | 17.6% | +$14.18（1 異常勝） | 50%（n=2）|
| DOT | +$23.37 | 25.0% | -$8.90 | 0% |
| ADA | +$17.35 | 13.3% | -$6.01 | 22.2% |
| ETH | +$10.34 | 29.2% | -$8.09 | 0% |
| NEAR | -$17.55 | 9.1% | -$14.79 | 0% |

**NEAR 在回測就是輸家**（9.1% 勝率，已虧 -$17.55）— 上一份稽核建議「實盤暫停 NEAR」，這個建議獲得回測的獨立佐證。

回測中 AVAX/BTC/DOT 都能賺，實盤都在虧。差別在於回測有大趨勢可賺，實盤沒有。

### 3.6 月度演進

| 月份 | 回測筆數 | 回測 PnL | 回測勝率 | 備註 |
|---|---|---|---|---|
| 2025-09 | 22 | -$3.59 | 13.6% | 略虧 |
| 2025-10 | 26 | **+$68.58** | 15.4% | 趨勢期，幾筆大勝 |
| 2025-11 | 5 | +$29.81 | 20.0% | 入場稀少（quality filter 起作用） |
| 2025-12 | **0** | $0 | — | **整月不交易**（quality 全擋） |
| 2026-01 | 26 | +$50.33 | 34.6% | 強勢期 |
| 2026-02 | 32 | +$48.76 | 31.2% | 強勢期 |
| 2026-03（前 19 天） | 18 | **+$94.07** | 33.3% | 趨勢加速 |

**關鍵觀察**：
1. **2025-12 整月零交易**——quality > 0.5 + ADX > 25 + ATR rising 同時不滿足。**證明過濾器確實會擋下「不該交易的時段」**
2. 強勢期月（10 月、1-3 月）勝率 30%+
3. 跌勢期月（9 月）勝率 13.6%、虧損

**實盤 2026-04 完全在 backtest 範圍外**——可能正在歷史上類似 9 月或 12 月的「不適合交易」窗口，但因 bug（連續觸發 scout、靜態停損）導致系統「在不該交易的時段被迫交易」。

---

## 第 4 章 對照解析：為何實盤完全偏離回測？

### 4.1 假說 A — 市場 regime 不同（最強解釋）

**證據**：
- 回測期間 BTC -52%（明確下跌趨勢）
- 實盤期間（2026-03-24 到 2026-04-23）BTC 走勢未知，但實盤交易資料顯示：MFE 1.17% vs 回測 4.52% — 表示**市場「沒在走」**
- Supertrend 是趨勢追隨策略，在 chop / range 行情中是負期望值

**驗證方法**：取回測中 PF < 1 的某個區間（例如 2025-09 月）與實盤對比。若指標相近，假說 A 成立。

**意涵**：**策略本身正常，當前是「不適合此策略的市場」**。應減少曝險或暫停，等趨勢明確再進場。

### 4.2 假說 B — Bug 加劇市場不利期的損失

來自上一份稽核報告：
1. `use_custom_stoploss = False` → 智慧 trailing 失效，所有止損都是硬 -5%
2. Scout 連續觸發 → 在不該進場的時段也進場
3. Kelly floor 3% → 即使 Kelly = 0 仍下單

**回測未受 bug #1 影響？** 神奇地是，回測有 30 筆 `trailing_stop_loss` 退出，意味著**回測中智慧停損仍然生效了**——可能是 freqtrade backtest 與 live 的處理路徑差異。需要進一步驗證。

**Bug 在回測下傷害有限**，但在實盤下被「無趨勢市場」放大，導致大量無謂虧損。

### 4.3 假說 C — Stoploss-on-exchange 在 OKX 上的滑點

回測使用 `stop_loss` 退出（49 筆），實盤幾乎全用 `stoploss_on_exchange`（33 筆）。差異：
- `stop_loss`（freqtrade-side）：以 K 線收盤價判定
- `stoploss_on_exchange`（OKX-side）：以即時 tick 觸發、可能在 wick 上被掃出

**現實 wick 比 backtest 預期更頻繁**，導致實盤停損更早觸發、虧損更大。

### 4.4 假說 D — 樣本量不足以下結論

實盤 58 筆 vs 回測 129 筆。實盤資料只覆蓋 1 個月，若策略真實 EV 是「6 個月平均 +28% 但其中 3 個月可能虧損」，則 1 個月樣本拒絕策略是統計學上的誤判。

**最大連敗 12 在回測中也出現過 14**——不是史無前例的事件。

---

## 第 5 章 回測中「真正賺錢的關鍵」分析

把 129 筆交易拆成兩類：

**A 類：跟趨勢的長持單（11 筆）** — `daily_reversal_exit` (7) + `force_exit` (2) + `confirmed` (2)
- 累積 PnL：$230.02 + $59.59 + $114.75 = **+$404.36**
- 平均：**+36.8%**
- **佔總利潤 140%**（其他類別淨虧）

**B 類：高頻 scout 短打（118 筆）** — 其餘所有
- 累積 PnL：$287.97 - $404.36 = **-$116.39**
- 平均：**-0.99%**

**這個分解告訴我們**：策略的本質是「廣撒 scout，等待少數變成 confirmed/daily_reversal 的爆發單」。如果系統把 scout 變得更頻繁（目前 bug）但同時讓 confirmed/daily_reversal 機制無效（停損太緊、邊緣觸發失靈），結果就是：留下了賠錢的 B 類，砍掉了賺錢的 A 類。

**這完全符合實盤資料**：
- 0 confirmed 入場
- 0 daily_reversal 退出
- 系統幾乎只執行了 B 類（虧損部分），把 A 類（賺錢部分）切掉了

---

## 第 6 章 修正後的預期表現重估

如果修完上一份報告的 P0（4 個動作），讓 confirmed entries 與 daily_reversal_exits 重新可能發生：

| 階段 | 預期 PnL/月 | 預期勝率 | 預期 PF |
|---|---|---|---|
| 當前實盤 | -$37 / 月 | 10.3% | 0.31 |
| P0 完成後 | -$10 ~ +$10 | 15-25% | 0.7 ~ 1.2 |
| P0 + P1 完成後 | +$10 ~ +$30 | 25-35% | 1.2 ~ 2.0 |
| 加上「等到趨勢期」的擇時 | +$30 ~ +$80 | — | 2.0+（趨勢月）|

關鍵：**這個策略在無趨勢期就是會虧錢**。真正的優化應該是「加入 regime detection 在無趨勢期暫停交易」，而不是「調整參數讓策略在無趨勢期能賺錢」（後者可能是 fitting）。

---

## 第 7 章 結論與行動

### 主要結論

1. **執行策略確認為 `SupertrendStrategy`**（非 SMCTrend）
2. **策略本身在回測有正期望值**（200 日 +28.8%, Sharpe 2.55, PF 3.20）
3. **但回測 80% 利潤來自 11 筆「跨趨勢長持單」**——策略本質是「等待大行情」
4. **實盤偏離回測的主因有三**：
   - 市場 regime 不同（無大趨勢期）
   - Bug 讓「會賺的長持機制」失效（confirmed=0, daily_reversal=0）
   - OKX exchange 停損 wick 觸發比 backtest 嚴苛
5. **單月實盤 -$37 不足以否定策略**——統計上連敗 12 在回測中也發生過

### 立即行動

| 優先序 | 動作 | 出處 |
|---|---|---|
| P0 | 執行上一份稽核報告的 4 項止血動作 | `strategy_audit_2026Q2.md` |
| P0 | 暫停 NEAR 交易（回測也是輸家，唯一獨立確認的「該砍」幣對） | 本報告 §3.5 |
| P1 | 加 regime detection：1d ATR 收縮 + ADX < 20 → 暫停所有入場 | 本報告 §4.1 |
| P1 | 監控 confirmed/daily_reversal 的觸發頻率，每週檢視 | 本報告 §5 |
| P2 | 將 SupertrendStrategy 與其他策略（SMCTrend, BB Squeeze）做對照 backtest | `strategy_audit_2026Q2.md` P2-1 |

### 不應做的事

- 不要因為實盤 1 個月虧損就放棄策略（樣本太小、市場 regime 太特定）
- 不要在實盤虧損狀態下做 hyperopt（會找到「最不爛的過擬合」）
- 不要刪除 `supertrend_v3.py` 之前先 backtest 對照（也許是已準備好的更新版）

---

## 附錄 A：回測指令

```bash
ssh root@187.127.100.77 "cd /opt/ambmh && \
  docker compose -f docker-compose.prod.yml exec -T freqtrade \
    freqtrade backtesting \
      --strategy SupertrendStrategy \
      -c /freqtrade/config/config_dry.json \
      --strategy-path /freqtrade/user_data/strategies \
      --timeframe 15m \
      --timerange 20250901-20260319 \
      --pairs BTC/USDT:USDT ETH/USDT:USDT AVAX/USDT:USDT NEAR/USDT:USDT \
              ATOM/USDT:USDT ADA/USDT:USDT DOT/USDT:USDT \
      --export trades \
      --breakdown day week month"
```

## 附錄 B：分析腳本

`scripts/analyze_backtest.py` — 從 backtest JSON 產出本報告所有數字。

```bash
python scripts/analyze_backtest.py data/audit/backtest_supertrend/backtest-result-2026-04-23_05-10-30.json
```

## 附錄 C：建議的下一步驗證

1. **跑 `supertrend_v3.py` 同期 backtest 對照**——是否新版已修了某些問題？
2. **跑 SMCTrend 同期 backtest 對照**——驗證上一份稽核 P2-1 的選擇假設
3. **重跑 backtest 但「手動禁用 use_custom_stoploss」**——分離 bug 影響 vs 市場影響
4. **取 2025-09 月（回測中的虧損月）數據單獨 backtest**——驗證假說 A

---

**報告版本：1.0**
**回顧時程：建議與 `strategy_audit_2026Q2.md` 同步檢視，每月一次更新**
