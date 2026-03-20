# Trading System Architecture Skill

## 系統總覽

OKX USDT 永續合約自動交易系統，基於 Freqtrade + SMC (Smart Money Concepts)。

## 信號鏈

```
OKX Exchange
  ↓ CCXT
Freqtrade Engine
  ↓
populate_indicators()
  ├── SMC 指標 (smartmoneyconcepts lib)
  │   ├── Swing Highs/Lows → swing_hl, swing_level
  │   ├── BOS/CHoCH → bos, choch, bos_level (趨勢突破/反轉)
  │   ├── Order Blocks → ob, ob_top, ob_bottom (機構掛單區)
  │   ├── Fair Value Gaps → fvg, fvg_top, fvg_bottom (價格不平衡區)
  │   ├── Liquidity → liq_level, liq_swept (流動性池)
  │   └── Retracements → retrace_pct, valid_retrace
  │
  ├── HTF 4H 趨勢 (merge_asof)
  │   ├── 4H BOS/CHoCH → htf_trend (1/-1/0)
  │   ├── 4H Order Blocks → htf_ob_top/bottom
  │   └── 4H FVG → htf_fvg_top/bottom
  │
  ├── 技術指標
  │   ├── ATR(14) → atr, atr_pct, atr_sl_dist, atr_tp_dist
  │   ├── VWAP(50) → vwap, above_vwap
  │   ├── Adam Projection → adam_slope, adam_bullish
  │   └── Premium/Discount → in_premium, in_discount, OTE zones
  │
  ├── 環境指標
  │   ├── Killzone (UTC 小時活躍度) → activity_mult, in_killzone
  │   ├── Funding Rate → fr_ok_long, fr_ok_short
  │   └── Vol Regime → vol_regime_ok
  │
  ├── 區域偵測 (_detect_active_zones)
  │   ├── Active OB/FVG tracking (72/48 candle expiry)
  │   ├── OB+FVG Confluence (Grade A 信號)
  │   └── Equal Highs/Lows (liquidity pools)
  │
  └── Confidence (_calculate_confidence)
      ├── Momentum (25%): ROC-6/24/72 加權
      ├── Trend Alignment (25%): HTF 趨勢 + 動量一致性
      ├── Volume Conviction (12%): 成交量 vs MA20
      ├── Volatility Quality (13%): ATR 擴張方向
      ├── Market Health (13%): 價格 vs EMA50/200
      └── Activity Regime (12%): 時段活躍度
      → EMA(3) 平滑 → confidence [0, 1]
```

## 進場邏輯 (populate_entry_trend)

### 進場等級
- **Grade A**: OB+FVG 重疊 → confidence > 0.1 即可
- **Grade B**: 單獨 OB 或 FVG → confidence > 0.35
- **Grade B+**: Grade B + OTE zone 或 EQH/EQL sweep → confidence > 0.1

### 必要條件
1. `htf_trend` 方向一致 (long: >0, short: <0)
2. Zone 在場 (Grade A/B/B+)
3. `adam_bullish` 方向一致 (可關閉)
4. `above_vwap` (可關閉)
5. `fr_ok` (funding rate < 0.05%)
6. `vol_regime_ok` (波動率正常)
7. `in_killzone` (活躍時段, 可關閉)

### 特殊模式
- **Reverse Confidence Short**: confidence < 0.2 → 反向做空，用 `1-confidence` 計算倉位

## 信心引擎 (GlobalConfidenceEngine)

### 四沙箱加權
| 沙箱 | 權重 | 指標 |
|------|------|------|
| Macro | 35% | NFCI, 10Y Yield, DXY, M2, Oil |
| Sentiment | 30% | VIX, Fear&Greed, SPY/IEF ratio, News |
| Capital Flow | 20% | BTC Dominance, Stablecoin MCap, SPY-BTC Corr |
| Haven | 15% | Gold trend, Gold/Oil ratio |

### Z-Score 正規化
- 看多方向 (positive): `score = 0.5 + z * 0.25`
- 看多方向 (negative, 如 VIX): `score = 0.5 - z * 0.25`
- Clip to [0, 1]

### 事件日曆乘數
- FOMC: 0.5x (±1 day buffer)
- CPI: 0.7x (same day)
- Options Expiry: 0.8x (same day)

### Regime 對照
| Score | Regime | Position | Leverage | Threshold |
|-------|--------|----------|----------|-----------|
| ≥0.8 | AGGRESSIVE | 100% | 3.0x | ×1.0 |
| ≥0.6 | NORMAL | 75% | 2.0x | ×1.1 |
| ≥0.4 | CAUTIOUS | 50% | 1.5x | ×1.25 |
| ≥0.2 | DEFENSIVE | 25% | 1.0x | ×1.5 |
| <0.2 | HIBERNATE | 0% | 0x | ×999 |

### 數據來源
- FRED API: NFCI, M2 (需 FRED_API_KEY, 否則回 0.5)
- yfinance: 10Y, DXY, VIX, Oil, Gold, SPY, BTC
- alternative.me: Fear & Greed Index
- CoinGecko: BTC Dominance
- DefiLlama: Stablecoin Market Cap

## 風控 Guard Pipeline

### 執行順序
```python
1. MaxPositionGuard(max_pct=30)      # 單倉 ≤ 30% (高信心可達 45%)
2. MaxLeverageGuard(max_leverage=5)  # 動態槓桿上限 (小帳戶更嚴格)
3. LiquidationGuard(min_distance=2x) # 清算距離 ≥ 2倍止損
4. TotalExposureGuard(max_pct=80)    # 總曝險 ≤ 80%
5. DrawdownGuard(max_dd=10%)         # 回撤 >10% 凍結交易
6. CooldownGuard(minutes=60)         # 同幣種冷卻 60 分鐘
7. DailyLossGuard(max_pct=5%)       # 日虧損 ≤ 5%
8. ConsecutiveLossGuard(streak=5)    # 連虧 5 次暫停 24h
```

### Confidence-Aware MaxPositionGuard
- confidence < 0.7: 30% 上限
- confidence 0.7-1.0: 線性插值 30% → 45%
- TotalExposureGuard 80% 仍為硬上限

### 狀態持久化
- 存儲: `/data/guard_state.json`
- 全部 guard 狀態在交易退出時寫入
- 啟動時自動載入

## 智能 Agent 控制

### 工具層級
| Tier | 工具 | 效果 |
|------|------|------|
| 0 | get_market_overview, get_confidence_score 等 | 只讀 |
| 1 | set_risk_level | conservative(0.6x) / normal(1.0x) / aggressive(1.2x) |
| 1 | set_leverage_cap | 限制最大槓桿 (1.0-5.0) |
| 2 | pause_entries(hours) | 暫停進場 N 小時 |

### Agent 狀態
- 存儲: `/data/reports/bot_state.json`
- 欄位: `agent_pause_entries`, `agent_resume_at`, `agent_leverage_cap`, `agent_risk_level`
- 冷卻: 每個工具 1 小時

### 決策循環
- 完整分析: 每 8 小時
- 快速檢查: 每 4 小時
- 心跳: 每 5 分鐘

## 倉位管理

### 槓桿
```
leverage = 1.0 + (max_leverage - 1.0) × confidence²
```
- Blend: 60% macro + 40% local confidence (live mode)
- Agent cap: `min(lev, agent_leverage_cap)`

### 倉位大小
```
base_scale = 0.2 + 1.3 × confidence  (0.2x - 1.5x)
× anti_martingale  (虧: -20%/次, 贏: +15%/次)
× activity_boost   (高峰 +10%, 低谷 -20%)
× agent_risk_mult  (conservative: 0.6, normal: 1.0, aggressive: 1.2)
```
- 硬上限: 2% 帳戶風險/筆交易

### 部分止盈 + 金字塔加倉
- 1.5R: 賣 33%
- 2.5R: 再賣 33%
- 剩餘 34%: trailing stop
- 金字塔: 利潤 >5% + confidence ≥ 0.5, 最多 3 筆

### 止損 (3 階段 ATR)
1. 初始: ATR × sl_mult (預設 2.2)
2. 1.5R 利潤: 移到打平 + 0.3% buffer
3. 2.5R+ 利潤: Trail at 0.7R below high

## 熔斷器 (Circuit Breakers)

1. BTC 24h 漲跌 > ±10% → 全部擋
2. ATR spike > 3x avg → 全部擋
3. Confidence < 0.15 → 只擋 long (允許 reverse short)
4. Crypto Env < 0.25 (HOSTILE) → 對應幣種擋

## 關鍵閾值快查

| 參數 | 值 | 位置 |
|------|---|------|
| max_leverage | 3.0 (hyperopt) | smc_trend.py |
| atr_period | 14 | smc_trend.py |
| atr_sl_mult | 2.2 | smc_trend.py |
| atr_tp_mult | 3.5 | smc_trend.py |
| swing_length | 12 | smc_trend.py |
| htf_swing_length | 14 | smc_trend.py |
| adam_lookback | 20 | smc_trend.py |
| confidence EMA span | 3 (local), 5 (global) | smc_trend.py, confidence_engine.py |
| OB expiry | 72 candles | smc_trend.py:_detect_active_zones |
| FVG expiry | 48 candles | smc_trend.py:_detect_active_zones |
| EQH/EQL threshold | 0.1 × ATR | smc_trend.py:_detect_equal_highs_lows |
| Funding rate limit | 0.05%/8h | smc_trend.py |
| Stale confidence | >3 hours → 0.25 | smc_trend.py:bot_loop_start |
| Data blackout | ≥70% at 0.5 → DEFENSIVE | confidence_engine.py |
