# 加密期貨策略稽核與優化報告 · 2026 Q2

**評估期：2026-03-24 ~ 2026-04-23（29 天，58 筆已平倉 + 2 筆未平倉）**
**稽核日：2026-04-23**
**資料來源：`/freqtrade/tradesv3.dryrun.sqlite`（VPS）**

---

## TL;DR — 結論先行

當前生產跑的是 **`SupertrendStrategy`**，不是 README/CLAUDE.md 暗示的 SMCTrend。系統處於**結構性虧損**狀態，並且已知的代碼錯置與設定關閉讓事情更糟：

- **總損益 -$37.79（-3.78% 帳戶）**，29 天賠了起始資金約 4%
- **勝率 10.3% / 損益比 0.71** — 損益比 < 1 與低勝率的雙殺，期望值每筆 -3.85%
- **平倉需 58.4% 勝率才打平**，目前差距 -48 個百分點
- **12 連敗**（最多連勝 1）— 風控完全沒接住
- **3 個關鍵 bug 同時生效**：智慧停損被靜態關閉、Scout 入場過度頻繁、SMCTrend 整套不在跑

**應立即停止 dry-run 模式下的「為了賺錢而調參」工作，先把結構性 bug 修完，否則任何優化都是在錯誤的基礎上 noise**。

---

## 第 1 章 量化發現

### 1.1 頭部數據（最致命）

| 指標 | 數值 | 健康基準 | 判讀 |
|---|---|---|---|
| 總筆數（已平倉） | 58 | — | — |
| 累積 PnL | -$37.79 | > 0 | 嚴重虧損 |
| 勝率 | **10.3%** | > 40% | 災難性低 |
| 損益比 R/R | **0.71** | > 1.5 | 倒置 |
| **打平所需勝率** | **58.4%** | < 40% | 結構不可能 |
| 期望值 EV/筆 | **-3.85%** | > 0 | 確定虧損 |
| Profit Factor | **0.31** | > 1.3 | 0.31 = 賺 1 賠 3.3 |
| 最大回撤 | -4.15%（-$41） | < 10% | OK 但路徑驚險 |
| 最大連敗 | **12** | < 5 | 警示信號 |
| 最大連勝 | 1 | — | 無連續性 |

### 1.2 退出原因分布

| 退出原因 | 筆數 | 佔比 | PnL 加總 | 平均 % | 勝率 |
|---|---|---|---|---|---|
| `stoploss_on_exchange` | 33 | 57% | **-$41.09** | -5.69% | **0%** |
| `multi_tf_exit` | 23 | 40% | -$10.23 | -1.92% | 21.7% |
| `stop_loss` | 1 | 2% | -$2.11 | -5.76% | 0% |
| `time_decay` | 1 | 2% | **+$15.64** | **+14.24%** | 100% |

**關鍵讀法：**
- 一半以上交易（57%）走 OKX 的硬停損（exchange-level），全部虧損
- 唯一正向退出原因是 `time_decay`，且只觸發 1 次但救了 $15
- **如果沒有那 1 筆 time_decay 異常值，總虧損會是 -$53.43，不是 -$37.79**
- `multi_tf_exit`（多時框反向 exit）在虧損上明顯較克制，是相對「健康」的退出

### 1.3 多空對稱性

| | 筆數 | 勝率 | 平均 % | PnL |
|---|---|---|---|---|
| 多單 | 24 | **4.2%** | -4.39% | -$18.35 |
| 空單 | 34 | 14.7% | -3.48% | -$19.45 |

多單 24 筆只贏 1 筆。空單相對「沒那麼差」但仍是負期望值。**問題不是方向偏差，是進場品質**。

### 1.4 各幣對表現（按筆數排序）

| 幣對 | 筆數 | 勝率 | 平均 % | PnL | 最佳 | 最差 |
|---|---|---|---|---|---|---|
| ETH/USDT:USDT | 13 | **0%** | -5.04% | -$8.09 | -2.04% | -6.26% |
| BTC/USDT:USDT | 9 | 11.1% | -3.57% | -$6.12 | +0.64% | -5.53% |
| AVAX/USDT:USDT | 9 | 22.2% | -3.47% | -$8.07 | +1.69% | -5.56% |
| ADA/USDT:USDT | 9 | 22.2% | -4.47% | -$6.01 | +1.69% | -6.77% |
| NEAR/USDT:USDT | 8 | **0%** | -4.39% | -$14.79 | -1.42% | -5.71% |
| DOT/USDT:USDT | 8 | **0%** | -3.50% | -$8.90 | -0.41% | -5.80% |
| ATOM/USDT:USDT | 2 | 50% | +4.30% | +$14.18 | **+14.24%** | -5.63% |

**全幣對失敗**。ETH/NEAR/DOT 三檔 0% 勝率（共 29 筆）。ATOM 那 +14% 是唯一光點，但是樣本 2 筆 = 沒有統計意義。

### 1.5 持倉時長

- 中位 **2.5 小時**
- p25: 2.0 小時
- p75: 8.2 小時

15 分鐘 K 線策略，平均持倉只有 10 根 K 線就被踢出。MFE 平均 1.17%，MAE 平均 1.06% — **價格幾乎沒有時間朝任何方向充分發展**就觸發停損或多時框反向 exit。

### 1.6 槓桿與倉位

- 平均槓桿：**4.20x**
- 平均下單金額：$22 USDC
- 起始停損 100% 是 -5%

**5x 槓桿 + 5% 停損 = 25% 名目曝險的硬上限**。在加密 15m 級的波動下，這個組合保證頻繁觸發。

---

## 第 2 章 結構性問題（質化發現）

### 2.1 🔴 致命：策略路徑不一致

`config_dry.json` 啟動命令明確：
```
freqtrade trade --strategy SupertrendStrategy ...
```

但 `strategies/smc_trend.py` 仍是 **2501 行的「主」策略**，包含：
- 信心引擎整合
- ATR 動態停損
- Killzone 過濾
- Adam projection 過濾
- 三層 OB/FVG/BOS 訊號
- Anti-fragile 退化機制

**這 2501 行代碼在生產上完全沒有執行**。`smc_trend.json` 的 hyperopt 結果 (`use_killzone:0`, `use_adam_filter:0`) 也只是針對未在跑的策略。

`CLAUDE.md` 文件說「`SMCTrend` is the sole active strategy」**這個敘述是錯的**。

### 2.2 🔴 致命：智慧停損實際上被關掉

`strategies/supertrend.py`:
```python
stoploss = -0.05
trailing_stop = False
use_custom_stoploss = False  # ← line 140

# ...

def custom_stoploss(self, ...):  # ← line 321, 但永遠不會被呼叫
    """Smart trailing stop — profit-phase based.
    Phase 0: Flat -5% (breathe)
    Phase 1: Lock at entry + 0.3% (breakeven after fees)
    Phase 2: Trail at 50% of max profit
    Phase 3: Trail at 70% of max profit
    """
```

整個四階段的 trailing 邏輯（含長/空非對稱）**都是死碼**。只有那行 `stoploss = -0.05` 真的生效。

對應到資料：`avg_initial_stop_loss_pct = -5.0`、`stops_fired_pct = 56.9%` — 完全證實只有平坦 -5% 在用。

**設計上號稱「let winners run, lock profit at breakeven」實際運作是「全平 -5% 任死」**。

### 2.3 🔴 致命：Scout 入場過度頻繁

最近一次 commit (`4ae76e4 fix: scout triggers continuously while 3-layer aligned`) 把 Scout 從「只在 3 層對齊形成的那根 K 線觸發」改成「對齊期間每根 K 線都觸發」。

代碼證據（line 273-278）：
```python
three_bull = dataframe["all_bullish"] & (dataframe["st_trend"] == -1)
mask_scout_long = three_bull & quality & dataframe["fr_ok_long"] & ~mask_confirmed_long
dataframe.loc[mask_scout_long, "enter_long"] = 1
dataframe.loc[mask_scout_long, "enter_tag"] = "scout"
```

**沒有 candle-edge 觸發限制**。只要 1d/4h/1h 全多 + quality > 0.5 + funding 沒過熱，每根 15m K 線都可以開新單。Freqtrade 的 max_open_trades=3 是唯一的閘門。

對應數據：57/58 筆都是 scout（98%），confirmed 入場 0 筆。**整個「scout → confirm DCA」雙階段機制從未真正進入第二階段**。

### 2.4 🟠 高：Trend Quality 門檻過低

```python
quality > 0.5  # 入場門檻
trend_quality = 0.25*adx_norm + 0.25*duration_norm + 0.25*alignment + 0.25*atr_expand
```

`alignment` 已是入場前置條件，所以入場時 = 1。`atr_expand` 是 boolean 0/1。剩下兩個 normalised 平均 ≥ 0.5 即可。

實際門檻：`(adx_norm + duration_norm + 1 + atr_expand)/4 > 0.5` → `adx_norm + duration_norm + atr_expand > 1`。
意思是只要 ATR 在擴張（很常發生）+ ADX > 25（duration_norm 即使 0 也過），就能入場。**這跟「高品質趨勢」的標籤名不符**。

### 2.5 🟠 高：Kelly 計算用錯預設值

```python
_KELLY_DEFAULT_WR = 0.355
_KELLY_DEFAULT_WL = 3.36
```

預設 35.5% 勝率 + 3.36 R/R → Kelly = 0.355 - 0.645/3.36 = **0.163**（樣本 < 10 時用此）。

當樣本 ≥ 10 後切換到 rolling 60，但實際勝率 10.3%、R/R 0.71 → Kelly = max(0, 0.103 - 0.897/0.71) = **0**。

代碼之後：`target_pct = max(0.03, min(target_pct, 0.20))` → 即使 Kelly 算出 0，仍強制 3% baseline。**Kelly 公式對賠錢系統的零信號被「3% floor」蓋過**，繼續開倉。

Scout 進一步乘 0.25 → 0.75% 倉位 × 5x leverage = 3.75% 名目曝險。仍會持續燒。

### 2.6 🟡 中：Funding rate 篩選沒生效

```python
if "funding_rate" in dataframe.columns:
    fr = dataframe["funding_rate"].fillna(0)
    dataframe["fr_ok_long"] = fr < 0.001
else:
    dataframe["fr_ok_long"] = True   # ← fallback 直接放行
```

如果 OKX feed 沒有把 funding_rate 灌進 dataframe（多數預設不會），filter 直接 fallback 為 True。**保護機制無聲失效**。

### 2.7 🟡 中：缺少帳戶級風控

整個策略沒有：
- 日內最大虧損熔斷（連續 12 連敗時系統毫無反應）
- 單筆交易的最大允許 MAE（一旦 -5% 直接出，沒漸進防禦）
- 連敗後降級機制（連虧 3 筆後應降低 stake / 暫停）
- 最低勝率閘門（樣本 ≥ 30 後勝率 < 20% 應自動暫停）

`market_monitor` 模組中設計的 `confidence_engine` 完全沒有跟 `SupertrendStrategy` 接上（因為它是為 SMCTrend 設計的）。**風控設計存在但與運行中的策略斷層**。

### 2.8 🟢 低：Telegram 訊息發到兩個 bot

不是 bug 但有點冗：每筆進出場都同時發 `TELEGRAM_TOKEN` 與 `TG_AI_BOT_TOKEN`。意圖是分流，但沒有差異化內容。可以合併到單一 bot。

---

## 第 3 章 設計意圖 vs 實際表現

| 設計宣稱（comment / docstring） | 實際運作 | 落差 |
|---|---|---|
| 「Smart trailing — let winners run」 | 平坦 -5% 任死 | 智慧 SL 被關掉 |
| 「Two-phase: scout + confirm DCA」 | 100% scout，0% confirm | DCA 機制未啟用 |
| 「Trend Quality > 0.5 = high-quality entry」 | 門檻實際只需 ATR 擴張 | 命名誤導 |
| 「Rolling Kelly position sizing」 | floor 3% 蓋過 Kelly = 0 | 燒錢的 Kelly |
| 「FR filter prevents overcrowded entries」 | 無 FR feed 時 fallback True | 無聲失效 |
| 「ATR-multiplier dynamic stops」（SMCTrend 文件） | SMCTrend 不在跑 | 整套不存在 |
| 「Multi-bot Telegram for separation」 | 兩 bot 內容相同 | 冗餘 |
| 「Confidence engine drives sizing」 | Confidence 與 Supertrend 沒接上 | 斷層 |

---

## 第 4 章 優化建議（按優先序）

每項建議帶：**假說**、**預期影響**、**實作 effort**、**風險**。

### P0 — 緊急止血（24 小時內必做）

#### P0-1. 立即停止真實資金流向 dry-run 系統的「校準調參」

**假說**：當前系統的失敗不是參數問題，是結構問題。任何 hyperopt 都會在錯誤的基礎上找到「最不爛」的配置，掩蓋根因。

**預期影響**：避免在錯誤系統上累積錯誤的歷史信心。

**實作 effort**：0（停止操作）

**風險**：無

#### P0-2. 啟用 `use_custom_stoploss = True`

**假說**：這是被靜默關掉的智慧停損。即使其他都不改，啟用它後系統至少從「全平 -5% 任死」進化到「-5% 起 + 賺到 1.5% 後鎖損益兩平」。

**實作**：
```python
# strategies/supertrend.py line 140
use_custom_stoploss = True   # 改 False → True
```

**預期影響**：
- 預估救回 30%-50% 的「曾經到過 +1.5%、最後變 -5%」的交易（從現有 MFE 1.17% 來看，能擔下 1.5% threshold 的交易不多，但任何一筆都是淨救回 6.5%）
- 可能會把 multi_tf_exit 的小虧（-1.92%）變成更小或損益兩平
- 短期反而可能讓更多交易在 +1.5% 附近被掃出（noise wick），**這是已知 trade-off，需要 1-2 週觀察**

**Effort**：5 分鐘改 + 部署 + 重啟 freqtrade

**風險**：低。最壞情況是 `stoploss_from_open` 計算錯方向（從測試看不會），可以用 dry-run 驗證 24 小時。

#### P0-3. 復原 Scout 為「邊緣觸發」

**假說**：Scout 設計初衷是「對齊形成時試水溫」，現在改成「對齊期間每根 K 線都試」。這把試探單變成了高頻濫發，每筆 5x leverage + 5% 停損 = 持續燒錢。

**實作**：
```python
# strategies/supertrend.py line 273-274 — 加 edge condition
three_bull = (
    dataframe["all_bullish"]
    & (dataframe["st_trend"] == -1)
    & (dataframe["all_bullish"].shift(1) == False)  # ← 只在剛形成對齊的 K 線觸發
)
three_bear = (
    dataframe["all_bearish"]
    & (dataframe["st_trend"] == 1)
    & (dataframe["all_bearish"].shift(1) == False)
)
```

**預期影響**：
- 入場頻率從 ~2 trades/day 降到 ~0.5/day，曝險立即減半
- Scout 樣本品質提升（捕捉「轉折」而非「持續對齊」）
- 預期勝率從 10% 升到 25-35%（保守估計）

**Effort**：10 分鐘改 + 1 週觀察

**風險**：低。本質是回到 commit `4ae76e4` 之前的行為。

#### P0-4. 帳戶級熔斷 — 連虧 3 筆暫停 12 小時

**假說**：12 連敗的存在說明系統沒有自我保護。即使是好策略也會有衰減週期，沒熔斷 = 燒到資金歸零。

**實作**：在 `custom_stake_amount` 開頭加：
```python
# 取最近 3 筆已平倉
recent = sorted([t for t in Trade.get_trades_proxy(is_open=False)],
                key=lambda x: x.close_date or current_time, reverse=True)[:3]
if len(recent) >= 3 and all(t.close_profit and t.close_profit < 0 for t in recent):
    last_close = max((t.close_date for t in recent if t.close_date), default=None)
    if last_close and (current_time - last_close).total_seconds() < 12 * 3600:
        return 0  # 暫停 12 小時 — Freqtrade 會跳過此單
```

**預期影響**：12 連敗自動降為 3 連敗 + 12h 冷卻。即使勝率仍是 10%，連續燒錢被打斷。

**Effort**：30 分鐘 + 測試

**風險**：可能錯過反彈機會。但對當前 10% 勝率系統，「錯過」的比「亂追」的價值高很多。

---

### P1 — 結構修正（一週內）

#### P1-1. 提高 Trend Quality 門檻 + 重新定義

**假說**：當前 `quality > 0.5` 在 4 因子中只需 1.5 個強訊號就過。應改為 `quality > 0.65` 並重新加權。

**實作**：
```python
# 重新定義 trend_quality
dataframe["trend_quality"] = (
    0.30 * adx_norm        # ADX 是趨勢強度王道
    + 0.20 * duration_norm
    + 0.30 * alignment      # 對齊權重提高（不再 0.25）
    + 0.10 * atr_expand
    + 0.10 * fr_bonus       # 將既有的 fr_bonus_long/short 整合
)

# 入場閾值：0.5 → 0.65
quality = (...) & (dataframe["trend_quality"] > 0.65)
```

**預期影響**：每天進場機會減少 ~50%，但勝率改善 +15-20pp 可期。

**Effort**：30 分鐘 + 1 週觀察

**風險**：交易頻率降低，需 1 個月才能累積有意義樣本。

#### P1-2. 修正 Kelly 預設值 + 提高熔斷下限

**假說**：當前 Kelly 預設假設 35.5% WR + 3.36 R/R（合理目標）。但實際是 10% / 0.71，floor 3% 強制下注 = 持續燒。

**實作**：
```python
# strategies/supertrend.py
_KELLY_FLOOR = 0.0   # 從 0.03 改 0.0 — Kelly = 0 時不下單
_KELLY_LOOKBACK = 30  # 從 60 縮短，更快反應策略衰退

def custom_stake_amount(...):
    target_pct = self._calc_rolling_kelly()
    if target_pct <= 0:
        return 0  # Kelly = 0 = 不下單，不要 floor
    target_pct = min(target_pct, 0.20)
    # ...原邏輯
```

**預期影響**：當系統處於衰退期（Kelly < 0），自動停止下單，避免燒到復原。

**Effort**：10 分鐘

**風險**：可能完全停止下單（如果策略仍是負期望值）— **但這正是我們要的**。

#### P1-3. 統一信心引擎與 Supertrend

**假說**：現有 `confidence_engine` 是為 SMCTrend 設計的高品質風控層。把 Supertrend 接上後，可以用 confidence 動態調整 leverage 與是否准許入場。

**實作**：
```python
# strategies/supertrend.py
from market_monitor.confidence_engine import GlobalConfidenceEngine

# populate_indicators 後段
try:
    self._confidence = GlobalConfidenceEngine().calculate()["score"]
except Exception:
    self._confidence = 0.5

# leverage()
def leverage(self, ...):
    base_lev = ...   # 現有邏輯
    # 用 confidence 等比例縮放：confidence=0.5 → leverage 不變；
    # confidence=0.2 → leverage × 0.4 (HIBERNATE 模式)
    return base_lev * max(0.2, self._confidence * 2)

# populate_entry_trend 加入閘門
if self._confidence < 0.3:
    return dataframe   # 信心過低時直接不出訊號
```

**預期影響**：在巨觀環境惡劣時自動降低或暫停曝險。

**Effort**：1-2 小時

**風險**：增加策略對外部依賴。需要 confidence engine 穩定運作（已驗證）。

#### P1-4. 修正幣對 — 暫停 0% 勝率的標的

**假說**：ETH (13/0)、NEAR (8/0)、DOT (8/0) 樣本各自 ≥ 8，0% 勝率不太可能是雜訊。可能策略在這些標的上系統性錯位（流動性、波動結構、做市商行為差異）。

**實作**：在 `config_dry.json`（VPS 上 `config_demo.json`）的 `pair_whitelist` 暫時移除 ETH/NEAR/DOT，只保留 BTC/AVAX/ADA/ATOM/SOL。觀察 30 天後依新數據決定是否回加。

**預期影響**：直接砍掉 -$31.78 的歷史出處（ETH+NEAR+DOT 累積）。

**Effort**：5 分鐘改 config + 重啟

**風險**：減少了多樣化。但當前的「多樣化」是「在不同地方虧錢」，不是真多樣。

---

### P2 — 中期優化（1-3 月）

#### P2-1. 重新評估「SMCTrend vs SupertrendStrategy」的選擇

**問題**：為何要做了 2501 行的 SMCTrend 後跑 SupertrendStrategy？是 SMCTrend 在回測或 dry-run 表現更差所以放棄？還是只是「順手換」？

**行動**：
1. 用相同 30 天區間做 SMCTrend dry-run 對照
2. 對比 SMCTrend (有信心引擎、有 killzone、有多層 OB/FVG) vs Supertrend
3. 決定主策略，**只跑一個**，淘汰另一個

**Effort**：1-2 天 dry-run + 比對

**風險**：如果 SMCTrend 結果更差，2501 行代碼將被刪除（時間沉沒成本）。但保留兩套互不協作的策略代碼是更大的維護負擔。

#### P2-2. 建立 Walk-Forward Optimization (WFO) 流程

repo 中有 `scripts/wfo_smc.py` 和 `wfo_optimizer.py`，但沒有定期執行。

**建議**：
- 每月一次 WFO，將 hyperopt 結果寫入 git commit（pre-registration 風格）
- 用 Optuna 取代 freqtrade 的 SkLearn 預設（樣本效率更高）
- 包含 stoploss、leverage、trend_quality threshold 的 grid

#### P2-3. 加入「regime detection」自動切換

當前策略對所有市場狀態用同一套參數。建議：
- 用 1d ATR + ADX 偵測 trend / range / chop 三態
- trend → Supertrend full leverage
- range → mean reversion strategy（meta_strategy.py 已有概念）
- chop → 暫停所有入場

#### P2-4. 引入「策略動物園」框架（呼應 Polymarket 1.5b 設計）

把 Supertrend、SMCTrend、BB Squeeze、Volty Expan 都當成 **paper trading 候選**，只允許「連續 30 天 WFO 樣本內 + 樣本外都正向」的策略動用真實資金。

當前實際是「直接全資金跑 Supertrend，沒有篩選機制」。

---

### P3 — 觀察與資料積累（持續）

#### P3-1. 補強 trade journal

`smc_trend.py` 有 trade_journal 邏輯（line 110-127 寫 JSONL）但 supertrend 沒有。建議補上，包含：
- 進場時的 confidence 分數
- 當下的 macro 環境（VIX、BTC dominance）
- 退出時的 MFE / MAE
- 是否觸發 trailing 哪一階段

之後可以回頭分析「哪些情境下策略有效」。

#### P3-2. 建立 weekly 自動回顧報告

cron 每週日跑一次，產出：
- 該週 trade summary
- vs 前週的表現變化
- 觸發的所有 alert / 熔斷
- 建議參數微調（不自動套用，只提示）

#### P3-3. 評估「停止 dry-run 改用 paper trading 模擬」

當前 dry-run 已經用了 1 個月證明系統不行。繼續 dry-run 只是浪費時間。建議：
- **暫停實際下單** 30 天
- 改為純訊號收集 + 假設 PnL 計算（純 paper trading）
- 期間做完所有 P0/P1 修正
- 再從 dry-run 重啟，比對前後資料

---

## 第 5 章 估算修正後的預期表現

**保守模型**（實作完所有 P0 + P1-1, P1-2）：

| 指標 | 當前 | 預期 | 假設 |
|---|---|---|---|
| 勝率 | 10.3% | 25-30% | Trend Quality 提高 + Scout 邊緣觸發 |
| 損益比 | 0.71 | 1.5-2.0 | Smart trailing 啟用 + 鎖損益兩平 |
| Profit Factor | 0.31 | 0.9-1.4 | 從上述兩者推導 |
| EV/筆 | -3.85% | -0.5% to +0.8% | 保守，可能仍負 |
| 熔斷觸發 | 0 | 1-2/月 | 連敗 3 自動暫停 |
| 月損益 | -$37 | -$10 to +$15 | 仍偏保守，期望「不再快速燒錢」 |

**樂觀模型**（再加 P1-3, P1-4 + 月內接到 confidence engine）：

- 勝率 35-40%
- Profit Factor 1.5+
- EV/筆 +1.0% to +2.0%
- 月損益 +$30 to +$80（25% leverage 下）

**結論**：P0+P1 修完後，系統至少能停止燒錢；要轉正需要 P2 結構性改造。

---

## 第 6 章 立即行動清單（給操作者）

**今天（4/23）：**
- [ ] 確認本報告所有事實
- [ ] 確認 P0-1（停止調參）
- [ ] 部署 P0-2（啟用 use_custom_stoploss）— commit、push、`docker compose restart freqtrade`
- [ ] 部署 P0-3（Scout 邊緣觸發）— 同上

**本週（4/24-4/30）：**
- [ ] 部署 P0-4（連虧熔斷）
- [ ] 部署 P1-2（Kelly 修正）
- [ ] 觀察 7 天 dry-run，量化 P0 修正後的指標變化
- [ ] 決定是否暫停 ETH/NEAR/DOT（P1-4）

**本月（5 月）：**
- [ ] 完成 P1-1（Trend Quality）+ P1-3（接 confidence engine）
- [ ] SMCTrend vs Supertrend 對照 dry-run（P2-1）
- [ ] 寫下「策略動物園」設計文件（P2-4）

**長期：**
- [ ] 月度 WFO 流程
- [ ] 自動週度回顧
- [ ] regime detection 整合

---

## 附錄 A：用於本報告的指令

```bash
# 把 freqtrade DB 拉到本機
ssh root@187.127.100.77 "docker cp ambmh-freqtrade-1:/freqtrade/tradesv3.dryrun.sqlite /tmp/trades.sqlite"
scp root@187.127.100.77:/tmp/trades.sqlite data/audit/trades.sqlite

# 跑稽核
python scripts/audit_trades.py > data/audit/full_report.json
```

## 附錄 B：本報告對應的 commits

- `4ae76e4 fix: scout triggers continuously while 3-layer aligned (not just first candle)` — Scout 過度頻繁的源頭
- `bcebf66 feat: two-phase scout entry — 3-layer scout + 15m confirm DCA` — Scout/Confirm 雙階段機制引入
- `dd3d9ed feat: institutional data integration` — TWSE/MCP（與本策略無關）

## 附錄 C：與其他 repo 設計原則的呼應

本報告的核心建議「停止調參 → 修結構 bug → 再驗證」呼應 `docs/polymarket/architecture.md` 第一章原則 3：**Pre-Registration**。當前 Supertrend 的問題正是「事後在虧損的策略上找最不爛的參數」這個反模式 — 是 Polymarket 系統設計刻意要避免的。

---

**報告版本：1.0** · **稽核者：Claude (作為協作工程師)**
**回顧時程：建議 P0/P1 修完後 30 天回顧，60 天決定是否進入 P2**
