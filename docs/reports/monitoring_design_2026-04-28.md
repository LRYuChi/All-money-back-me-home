# 監控系統設計：對齊後的最小可運作版本

**日期**：2026-04-28
**狀態**：Phase A 已實作（EntryRateGuard）；Phase B 待 2 週後評估；Phase C 不做。
**前置事件**：2026-04-26 burst — R84/R85 backtest 污染 prod journal，1 小時 201 筆 entry 寫入造成「7 日 max_drawdown 74.83%」誤導性 dashboard 數字。詳見 `strategy_review_2026-04-27.md`。

---

## 設計目標（一句話）

**讓系統會主動對抗自我欺騙**——而非「呈現漂亮數字」。當 dashboard 顯示的故事與資料實際說的故事脫節時，使用者要在被誤導之前看到不一致。

## 五原則（哲學層）

1. **每個數字都帶上下文**：樣本範圍、健全性、計算版本、相關事件
2. **沉默不是證據**：系統要主動證明自己活著
3. **異常本身是訊號**：預設標記為待調查，不是自動正規化
4. **監控系統自己也要被監控**：但用最樸素的方式，不要無限遞迴
5. **歷史資料的可追溯性**：發現過去某指標被誤算時，要能精確找出受影響範圍

## 四層分類（故障定位框架）

| 層 | 回答的問題 | 失敗特徵 |
|---|---|---|
| Layer 0 | 系統還活著嗎？ | heartbeat 過期 |
| Layer 1 | 運作條件正確嗎？ | assertion violations |
| Layer 2 | 產出資料可信嗎？ | 樣本污染、burst event |
| Layer 3 | 我從資料得到的結論成立嗎？ | claims 過期 |

## Defense vs Diagnosis 的區分

設計過程中發現一個關鍵區分：

- **Defense**：在事件發生時或發生中阻止/限制傷害
- **Diagnosis**：在事件發生後幫助理解、追溯、避免重演

兩者都重要但**目標不同**。「會在 04-26 那天觸發嗎？」這個試金石檢測的是 Defense。Diagnosis 元件即使通不過這個試金石也有價值，但不能排在前期。

---

## 範圍對齊（最重要）

過去三輪對話累積了一份完整的 4 層架構提案。**對齊後刪掉 80%**，只實作真正具有 Defense 價值 + 維護成本可承受的部分。

### 維護預算前提

Single operator，5 個專案在跑（TAHZAN / Supertrend / Polymarket / kendama / ASP.NET），每週可承擔的監控維護時間誠實估計 = **30 min ~ 1h**。

任何超出此預算的設計都會被棄用，產生「false sense of safety + 沒人在看」的失敗模式（比沒有監控還糟）。

### 三 Phase 範圍

| Phase | 元件 | 維護成本 | 狀態 |
|---|---|---|---|
| **A**（已做） | EntryRateGuard + circuit breaker | ~0/週 | ✅ 2026-04-28 部署 |
| **B**（觀察 2 週後決定） | burst detection daemon、system_events table、effective_sample_period 標記 | ~30 min/週 | ⏸️ pending Phase A 評估 |
| **C**（不做） | 完整 4 層、Daily Truth Report、claims registry as table、decision linkage 自動化 | > 1h/週 | ❌ 已對齊 |

---

## Phase A 實作摘要

**`EntryRateGuard`**（`guards/guards.py`）：
- Wall-clock 滑動窗口（預設 3600 秒）
- 累積 entry 數達 `SUPERTREND_MAX_ENTRIES_PER_HOUR`（預設 5）後，第 N+1 筆起回拒
- Instance state 在記憶體（不持久化 — 重啟歸零是有意的，避免部署本身被誤判）
- 加入 strategy layer guard pipeline（`guards/pipeline.py`）
- 由 `strategies/supertrend.py:confirm_trade_entry` 在 `return True` 之前 call `record_entry()`

**04-26 對照**：若這個 guard 當時存在，第 6 筆 entry 起就會被擋 + 透過既有 R97 telemetry（Telegram「🛡️ Guard 攔截」+ `/api/supertrend/skipped`）警報。傷害規模從 201 筆縮到 ≤6 筆。

---

## 為什麼不做的記錄（給未來決策者）

### Sample health score 0-100

被討論並拒絕。理由：把多種異常壓縮成一個數字會讓使用者「掃過去看 92/100 覺得還可以」，違反第 3 原則「異常本身是訊號」。Phase B 會考慮 explicit flags 列表替代（每個異常獨立顯示，不可平均化）。

### Claims registry 升級為資料表

被討論並拒絕。理由：md → table 增加的是 entry friction，不是 incentive。真正的問題是「寫了沒下游動作 → 沒人催 → 棄用」。替代方案是 keep markdown + CI 強制 commit message 含 `claims:` 行，但這也延後到實際需要時再做。

### 外部監控放另一台機器

被討論並拒絕。理由：另一台機器有自己的 uptime 問題、網路斷線會 false-positive、維護兩套監控成本高。若 Phase B 真的要做外部觀察者，用同台 VPS 上的 systemd timer + 極簡 shell + curl + telegram api，已滿足「依賴極簡 + 獨立於主 daemons」的真正目標。

### Daily Truth Report

被討論並 deferred 到 Phase C。理由：是儀式工具，前提是「人類願意每天讀」。在每週 1h 維護預算下這個前提脆弱。effective_sample_period 標記（即時、附在資料旁、難以略過）是 Phase B 的 prevention 工具，效果更直接。

---

## 評估點（部署後 2 週）

2026-05-12 評估 Phase A：

1. EntryRateGuard 有沒有誤觸發？（看 `/api/supertrend/operations.guard_rejections_top` 是否含 EntryRateGuard）
2. 維護時間實際是多少？
3. 是否前進 Phase B？

進入 Phase B 的準入條件：
- 0 誤觸發 + 維護 < 30 min/週

進入 Phase B 的範圍：
- burst detection daemon（單獨 systemd timer，每 N 分鐘掃 journal）
- `system_events` table（在 Supabase，記錄 deploy / env_change / burst / restart）
- `/api/supertrend/snapshot` 加 `effective_sample_period` 欄位（first→last entry timestamp delta），當 effective < nominal × 50% 自動 flag

---

## 元層次紀律

這份設計的形成過程本身值得記錄。原始提案是 4 週實作完整 4 層架構。經過用戶 critique（套用「會在 04-26 觸發嗎」試金石回檢設計者自己），縮減到 1.5 週版本，再進一步縮到「Phase A 100 行 + 評估後再決定」。

教訓：**設計能力會跑得比維護能力快**。完整性是一種美學偏誤，誘惑你蓋超出實際需要的東西。簡單比複雜更難達成，但簡單的監控系統比複雜的更可信。

未來新增任何監控功能前，必過的試金石：

1. 如果這在 04-26 那天存在，會幫使用者更早發現問題嗎？
2. 維護成本加進每週預算後仍然 < 1h 嗎？
3. 這是 Defense 還是 Diagnosis？若是 Diagnosis，當前優先級對嗎？

任一答錯就 reject。

---

## 相關檔案

- `guards/guards.py:14-` — EntryRateGuard 實作
- `guards/pipeline.py` — 加入 strategy layer
- `strategies/supertrend.py:2037-` — record_entry hook
- `tests/test_entry_rate_guard.py` — 8 個 unit test
- `docs/reports/strategy_review_2026-04-27.md` — 04-26 burst 事後排查報告
