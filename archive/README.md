# Archive — 已淘汰但保留歷史的代碼

此目錄保存了**已決定淘汰但保留 git 歷史**的代碼。原因：
- 如果直接 `git rm`，未來查歷史時找不到上下文
- 文件中的設計決策仍可能有借鑒價值
- 若市場 / 數據改變，未來可能需要復活某些模組

**淘汰 ≠ 失敗**——淘汰可能是因為更簡單的方案勝出，或市場結構不再適合。

---

## 已歸檔項目

### `strategies/smc_trend.py` + `smc_trend.json`（淘汰於 2026-04-23）

**原狀態**：CLAUDE.md 與 README 標示為「主策略」，**但生產從未實際執行過**（生產跑的是 `SupertrendStrategy`）。

**淘汰理由**：
1. 200 日同期 backtest 結果：**-10.71% / 勝率 3.3% / 44 連敗**（vs Supertrend +28.80%）
2. ATR 動態停損平均 -0.93%——在 15m 加密期貨上太緊，導致中位持倉 **0 分鐘**
3. 7 個 USDT 永續對中**只在 BTC 與 ETH 觸發訊號**
4. 高品質入場標籤 `1h_grade_a` **從未觸發**——只跑了次級 `mean_rev`（勝率 1.8%）
5. 整套 2501 行代碼設計的精華（grade_a + reverse_confidence + killzone）**在實際資料上是死碼**

**詳見**：`docs/reports/strategy_comparison_2026Q2.md` §第 4 章

**復活條件**（如果未來想用）：
- 大幅放寬 ATR 停損下限（最低 3-5%）
- 重新校準 1h_grade_a 觸發條件，讓它真的會發生
- 重新評估 hyperopt 參數（當前 `use_killzone=0`、`use_adam_filter=0` 已關閉兩個過濾器）
- 在多個 regime 上 WFO 驗證後再考慮

**警告**：直接複製回 `strategies/` 並啟動會立即燒錢——backtest 已驗證。

---

## 不該放進這裡的項目

- 還在跑的策略代碼（如 `strategies/supertrend.py`）
- 雖無人使用但邏輯上可能被 import 的工具模組
- 任何 active 配置檔
