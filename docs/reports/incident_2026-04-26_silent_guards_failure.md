# 事故報告 — Silent Guards Failure (2026-04-26)

## 嚴重程度：CRITICAL（生產安全失效）

R97-R103 整個 guard pipeline 在 freqtrade 生產容器內**完全沒運作**，
靜默失敗了一週以上。從程式碼角度看一切正常（單元測試通過、API
switchboard 顯示 `guards.available=true`），但實際進到 `confirm_trade_entry`
時，`from guards.base import GuardContext` 會 `ModuleNotFoundError` →
R97 預設 fail-open（`return None`）→ guards 完全跳過。

風險：如果 `dry_run=False` 上 LIVE，**沒有任何 DailyLossGuard /
DrawdownGuard / ConsecutiveLossGuard 保護**。一連串虧損會無上限累積
到 OKX 帳戶熔斷或人工發現。

## 時序

| 時間 | 事件 |
|------|------|
| ~2026-04-26 早期 | R97 ship — guards 接到 confirm_trade_entry。Local 測試通過。 |
| 2026-04-26 整段 session | R98-R103 持續加 guards 相關功能（telemetry、雙重計算修復、recording）。所有 PR 通過 CI 與 local pytest。 |
| 2026-04-26 下午 | 用戶反映「TG 有訊息但網頁 `/trades` 沒顯示」。 |
| 2026-04-26 晚 | SSH 進 VPS 診斷，發現：<br>1. freqtrade 容器 7 小時前啟動（其他容器 24 分鐘前），跑舊版本 supertrend.py<br>2. 重啟 freqtrade 後仍然有問題 — 進一步發現 `from guards.base import` 在 freqtrade 內失敗 |
| 2026-04-26 晚 | R104 修復：在 `_check_guards` 與 `confirm_trade_exit` 都加 `sys.path.insert(0, dirname(__file__))` |
| 修復後 | freqtrade 容器內 `guards loaded: 9 / paused_until: 0 / consec_losses: 0` |

## 五個 contributing factors

### 1. PYTHONPATH 跨容器不一致
- 本地：repo root 在 PYTHONPATH，`guards/` 是 top-level package。
- API 容器：`COPY` 整個專案到 `/app`，`guards/` 也在 `/app/guards`，import 成功。
- Freqtrade 容器：透過 docker volume mount strategies/ 到
  `/freqtrade/user_data/strategies`，但**這個路徑不在 PYTHONPATH**，且
  freqtrade 自己只在 strategy 載入時短暫加入 — runtime 期間
  `confirm_trade_entry` 跑的時候 sys.path 不一定包含。

### 2. Fail-open 反模式
R97 寫法：
```python
try:
    from guards.base import GuardContext
    from guards.pipeline import create_default_pipeline
except Exception as e:
    logger.warning("guards module unavailable, skipping checks: %s", e)
    return None  # ← 靜默跳過，不阻擋進場
```

**意圖良善**（不讓 guards 模組壞掉導致整支 bot 掛掉），**結果致命**（沒
guards = 沒保護 ≫ 拒絕進場）。`logger.warning` 在 freqtrade INFO 級別下
會被埋掉。

### 3. 多份持久化 state 不同步
`guards/pipeline.py` 從 `DATA_DIR` env 讀 `guard_state.json`。
- API 容器：`/app/data/guard_state.json` ← R98 switchboard 讀的就是這個
- Freqtrade 容器：`/freqtrade/user_data/shared_data/guard_state.json` ← 真正會被 R97 寫入的（但因 R104 之前 import 失敗，從來沒寫過）

API 容器讀到的是 **2026-03-21** 累積的 stale state（`consec_streak: 5,
paused_until: 1774221307` = 2026-04-30），完全不反映實際狀況，但
switchboard 報告它就像當前真實。

### 4. 部署驗證只看「容器啟動時間 + commit hash」
這次 session 的「部署驗證」是：
- ✅ `git log --oneline` 顯示 R103
- ✅ `docker ps` 顯示 freqtrade healthy
- ❌ 沒在容器內 runtime 測試 `from guards.base import` 能不能 import

R98 switchboard 設計時就有 caveat 註明 API 跟 freqtrade 容器 env 必須
分別配置，但實際發布時沒做這個 cross-container probe。

### 5. R94 switchboard 是 visibility theater
R94 commit message 我自己寫過：
> NOTE: these reads come from the API container's process env. For
> this to faithfully reflect what's in effect inside the freqtrade
> container, the same env vars must be exported to BOTH services in
> docker-compose.prod.yml.

但 R98 進一步加 `guards.available=true` 卻沒提類似 caveat。**操作員看到
"available=true" 會合理推論「guards 在運作」，但其實只代表 API 容器自己
能 import**。

## R104 修復（已 push 為 commit `930d2e7`）

```python
# strategies/supertrend.py _check_guards (R97) 與 confirm_trade_exit
# (R99/R100) 兩處：
import sys as _sys
import os as _os
_strat_dir = _os.path.dirname(_os.path.abspath(__file__))
if _strat_dir not in _sys.path:
    _sys.path.insert(0, _strat_dir)
from guards.base import GuardContext
from guards.pipeline import create_default_pipeline
```

理由：docker volume 同時把 `guards/` 帶到
`/freqtrade/user_data/strategies/guards/`（跟 supertrend.py 同層）。把這個
目錄加到 sys.path 就能 import。

部署步驟（已執行）：
1. `git pull` on VPS
2. `rm /app/data/guard_state.json` (在 API 容器內，清除 stale state)
3. `docker compose restart freqtrade`
4. 在 freqtrade 容器內驗證 `guards loaded: 9, state clean`

## 防呆修補（待做）

### A. Runtime probe in /operations（高）
在 `/api/supertrend/operations` 加一條 alert：當 R66 EvaluationEvent 中
最近 24h 都沒看到 `[GuardName]` pattern in failure reasons → **不是
"沒被擋"，而是 "guards 根本沒跑"**。觸發 alert：
`GUARDS_NEVER_FIRED — guards module may not be loadable inside
freqtrade container; verify with docker exec`.

### B. Cross-container probe（中）
寫一個 `scripts/verify_deploy.sh` 在每次 redeploy 後跑：
- `docker exec ambmh-freqtrade-1 sh -c "cd ... && python -c 'from guards.pipeline import create_default_pipeline; print(len(create_default_pipeline().guards))'"`
- 期待輸出 9。
- 不是 9 → fail loudly + Telegram alert。

### C. Fail-closed 選項（中）
加 `SUPERTREND_GUARDS_REQUIRE_LOAD=1` env，讓「import 失敗」變成「拒絕
進場」而非「跳過 guards」。預設仍 fail-open（避免一個 typo bug 把整個
bot 打死），但 LIVE 時必須設為 1。

### D. 統一 guard_state 持久化（低）
讓 freqtrade 跟 API 容器透過 named volume 共享同一個
`guard_state.json`。這樣 R98 switchboard 看到的 state 就是 freqtrade
真正用的 state。

### E. 多容器 env 一致性檢查（中）
在 CI 加一個 script，確保任何 `SUPERTREND_*` env 在
`docker-compose.prod.yml` 的 freqtrade service 與 api service environment
區塊**都有列出**。否則 fail PR check。

## 永久教訓 — 寫入 memory

寫入 `~/.claude/projects/.../memory/feedback_silent_failure_patterns.md`：

> **Silent failure pattern — multi-container module visibility**
> When code path A (unit tests + API container) succeeds but code path
> B (production trading container) silently fails because of
> different sys.path / PYTHONPATH:
> - Single-container observability (e.g. R98 switchboard reading from
>   API container) does NOT verify code in another container.
> - Verify with `docker exec` runtime probe AT LEAST after major
>   guards/safety changes.
> - Fail-open in safety-critical paths is a anti-pattern; default to
>   fail-closed when the only alternative is "no protection".

> **R97 design lesson**: 凡是「import 失敗 → 跳過 guards」的設計，需要
> 配套的 visibility — 不是只 log warning，而是在 API alert chain 中放
> 一個 hard signal 讓操作員知道「保護機制根本沒上線」。

## 對用戶的承諾

| 項目 | 狀態 |
|------|------|
| 找出根因 | ✅ R104 commit message 詳述 |
| 修復 R104 | ✅ commit `930d2e7` deploy 到 VPS |
| Reset stale guard state | ✅ 從 2026-03-21 殘留清除 |
| 驗證 freqtrade 容器內 guards 真的可用 | ✅ `guards loaded: 9` |
| Post-mortem 文件 | ✅ 本檔案 |
| 防呆修補（A-E） | ⏳ 接下來逐項補 |
| Memory 更新避免再犯 | ⏳ 下一步 |

---

歸檔到 `docs/reports/incident_2026-04-26_silent_guards_failure.md`，將來
任何「運作正常但生產沒效果」的疑似情況都先看本檔。
