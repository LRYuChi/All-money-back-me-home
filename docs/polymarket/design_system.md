# Polymarket Design System

> Polymarket 儀表板與通知介面的設計系統。以 `docs/polymarket/architecture.md` 的 5 層架構為內容來源，
> 以 awesome-design-systems 中 4 個已驗證的工業級系統為參考祖先。
>
> 最後修訂：2026-04-21 · 版本 1.0

---

## 第 1 章 設計祖先（Design Ancestry）

我們不從零發明設計系統。以下 4 個來自 awesome-design-systems 的工業級系統為參考，每個都有明確的「為何借鑒」與「只借鑒什麼」。

### 1.1 AWS Cloudscape（主要結構）
**為什麼**：Cloudscape 專為運維儀表板而生，核心使用情境「高密度資料 + 即時狀態 + 層級式資訊架構」與 Polymarket 系統高度契合。它解決了「在同一頁面塞入大量數字而不讓人眼花」這個核心問題。

**借鑒**：
- Layout（`AppLayout` 模式：固定 sidebar + 可折疊 split panel）
- 狀態指示器（success/info/warning/error/pending/stopped 六態）
- Cards 元件的 container hierarchy
- Empty states、error states 的一致處理

**不借鑒**：其全寬式 header、AWS 特有的 breadcrumb 深度。

### 1.2 IBM Carbon（Token 架構）
**為什麼**：Carbon 在金融與企業 SaaS 有長期部署經驗，它的 token 架構（background layers、interactive colors、gradient-free elevation）是業界標竿。特別重要的是它有**完整的 dark mode token set**——而交易者大多在深色環境下看盤。

**借鑒**：
- 4 層 background token（`layer-01/02/03/04`）的階層模式
- Data visualization color palette（12 色可識別色系）
- Type scale（數字用等寬、label 用小型無襯線）
- Motion tokens（easing、duration 分級）

**不借鑒**：Carbon 的 pictogram 風格（過於企業感）、它的表單層（我們用 shadcn/ui 更好）。

### 1.3 Palantir Blueprint（密集資料模式）
**為什麼**：Blueprint 是**唯一專為「金融/情報/運營分析師的密集桌面儀表板」設計的開源系統**。它的 Table、Tree、Timeline 在表達大量帶時間軸的筆數時遠優於一般 web 導向系統。

**借鑒**：
- Dense 模式的 spacing 比例（padding 4px/6px/8px 而非 Tailwind 預設的 12px/16px）
- Hotkeys 系統（j/k 導航、/ 搜尋等 terminal-like 操作）
- Overlay 的多層 z-index 架構
- Focus ring 的可見度

**不借鑒**：Blueprint 的視覺色調（灰階偏暖，與我們的冷色情報感不合）。

### 1.4 GitHub Primer（只讀/配置視圖）
**為什麼**：Primer 為開發者導向的「讀 > 寫」型介面做了最佳設計，我們的 pre-registered.yaml 檢視器、配置版本歷史、歸因報告都屬於此類。

**借鑒**：
- Monospace 區塊的排版慣例
- diff view 風格（用於 `pre_registered.yaml` 版本對照）
- Label/tag 的顏色語義（soft-tinted backgrounds）
- Timeline / activity feed 的縮排階層

**不借鑒**：Primer 的 octicon 圖標集（我們用 lucide-react）。

---

## 第 2 章 設計原則（Design Principles）

這些原則是強制性的，違反需要在 PR 中明確標註理由。

### 原則 1：資訊密度優先於「呼吸空間」
交易者的時間是稀缺資源，每次眨眼都在燒錢。儀表板的每一像素都應承載資訊。這不代表雜亂——而是要求設計師為每一個 padding 辯護。**預設 padding 階梯：2 / 4 / 6 / 8 / 12 / 16 px**，不使用 Tailwind 的 20/24/32/48 除非在 landing page。

### 原則 2：狀態先於外觀
每一個資料容器都必須明確表達其 **freshness**（資料多新）、**confidence**（系統有多確定）、**liveness**（是否即時更新中）。狀態優先於美感——若一個好看的元件無法表達「這個數字 5 秒前才更新」，它不能用。

### 原則 3：色彩是語義，不是裝飾
**沒有純為美觀的配色**。每個顏色都綁定語義：紅=多頭（買 YES）、綠=空頭（買 NO）、紫=鯨魚活動、琥珀=風險警告、冷灰=歷史數據。違反此原則的 PR 會被駁回。

### 原則 4：數字是第一公民
所有數字採用 **tabular-nums**（等寬數字）以確保跨列對齊。金額、比例、時間永不 centered，一律右對齊或左對齊到小數點。

### 原則 5：深色優先
預設深色模式。淺色模式是選配。理由：(a) 交易者大多在深色環境下看盤；(b) 深色下的語義色差更易識別；(c) 長時間閱讀降低眼疲勞。

### 原則 6：Pre-Registration 延伸到設計
顏色命名、字級數字、間距階梯一旦登記在 `tokens.ts` 就是憲法。新增 token 必須 PR，改變現有 token 必須 PR + 理由 + 搜尋影響範圍。**禁止在元件中 inline hardcode 顏色或尺寸**。

---

## 第 3 章 Token 系統

### 3.1 色彩語義階層

```
意圖（Intent）     ─→  語義 token      ─→  色值
───────────────────────────────────────────
買 YES（做多）     ─→  semantic.yes    ─→  oklch(65% 0.22 25°)  紅
買 NO（做空）      ─→  semantic.no     ─→  oklch(65% 0.18 155°) 綠
鯨魚活動           ─→  semantic.whale  ─→  oklch(70% 0.18 290°) 紫
風險警告           ─→  semantic.warn   ─→  oklch(75% 0.15 75°)  琥珀
錯誤/熔斷          ─→  semantic.error  ─→  oklch(60% 0.22 15°)  深紅
歷史/靜態          ─→  semantic.stale  ─→  oklch(55% 0.02 240°) 冷灰
即時/live          ─→  semantic.live   ─→  oklch(80% 0.15 200°) 青藍
晉升/提升          ─→  semantic.tier   ─→  oklch(70% 0.12 90°)  金綠
```

**為何用 OKLCH 而非 HEX**：OKLCH 在亮度層面是感知均勻的，這讓「同亮度不同色相」的 token 在深色模式下保持視覺權重一致。Carbon 從 2023 年已全面遷移。

### 3.2 背景層級（借自 Carbon）

```
layer-00  ─→  最深背景（頁面底色）
layer-01  ─→  大型區塊（side panel, card container）
layer-02  ─→  巢狀於 layer-01 之上（inner card, filter strip）
layer-03  ─→  巢狀於 layer-02 之上（選中列、悬停高亮）
```

這個階層讓「zoom in」式的介面（點進 whale → 看 trades → 看單筆 trade detail）能用視覺層級自然表達。

### 3.3 字型系統

| 用途 | 字型 | 字重 | 特性 |
|---|---|---|---|
| 數字（價格、size、PnL） | JetBrains Mono 或 Geist Mono | 500 | tabular-nums, slashed-zero |
| 資料 label、表頭 | Inter | 500 | small-caps 可選 |
| 敘事文字（說明、錯誤訊息） | Inter | 400 | 標準 |
| 代碼片段、token_id、tx hash | JetBrains Mono | 400 | truncate with tooltip |
| 標題 | Inter | 600 | tight letter-spacing |

**Type scale**：11 / 12 / 13 / 14 / 16 / 20 / 24 / 32 px。不用 10px（不可讀），不用 18px（干擾 16↔20）。

### 3.4 間距階梯

```
2  px  ─→  tight inline（列高內補白）
4  px  ─→  dense data（Blueprint-style table padding）
6  px  ─→  button internal, chip internal
8  px  ─→  card inner spacing (低密度區)
12 px  ─→  section spacing
16 px  ─→  between major blocks
24 px  ─→  page gutters
```

### 3.5 動效

| Token | 值 | 用於 |
|---|---|---|
| motion.fast | 120ms ease-out | hover、focus、小型 ui 狀態 |
| motion.base | 200ms ease-out | 面板切換、tooltip |
| motion.slow | 400ms ease-in-out | 頁面轉場、大型布局變動 |
| motion.pulse | 1.2s infinite | 即時資料的「心跳」指示 |

**禁止跳動動畫**：交易介面不做 bounce、spring、overshoot。任何「彈性」動畫會干擾對數字變化的感知。

---

## 第 4 章 元件清單（Component Inventory）

元件分三級：**基礎層（Primitive）**、**複合層（Composite）**、**視圖層（View）**。每個元件註明出於哪個 Phase 的需求。

### 4.1 基礎層（Phase 0 即需要，可直接用 shadcn/ui）

| 元件 | 來源 | 說明 |
|---|---|---|
| Button | shadcn/ui | 四色階：primary / secondary / destructive / ghost |
| Badge | shadcn/ui 擴充 | 加入 whale / tier-A/B/C / live / stale 變體 |
| Input | shadcn/ui | 加 mono 變體（用於 token_id、wallet address） |
| Select | shadcn/ui | - |
| Tooltip | shadcn/ui | - |
| Dialog | shadcn/ui | - |
| Skeleton | shadcn/ui | 數字欄位專用 mono 骨架 |

### 4.2 複合層（Phase 1 建）

| 元件 | 功能 | 靈感來源 |
|---|---|---|
| `<KPICard>` | 單一指標展示（大數字 + 變化 + 時間戳） | Cloudscape KPI pattern |
| `<DataTable>` | 密集資料表（虛擬滾動、排序、篩選） | Blueprint Table |
| `<OrderBookLadder>` | 訂單簿階梯視覺化 | 自創（參考交易所共識） |
| `<ActivityFeed>` | 時間軸式活動流（whale trades） | Primer timeline + Blueprint dense |
| `<TierBadge>` | A/B/C 級鯨魚徽章 | Primer label |
| `<FreshnessIndicator>` | 資料新鮮度（🟢 live / 🟡 delayed / 🔴 stale） | Cloudscape status |
| `<PreRegBlock>` | 顯示 pre-registered 門檻值（含 rationale / next_review） | Primer config block |
| `<MiniSparkline>` | 小型走勢微縮圖 | 現有 `Sparkline.tsx` 擴充 |

### 4.3 視圖層（分 Phase 實作）

#### Phase 1 視圖
- `WhaleFeedView` — 鯨魚活動流（左側篩選、右側時間軸）
- `ActiveMarketsView` — 活躍市場列表（表格，依成交量排序）
- `MarketDetailView` — 單市場詳情（orderbook + trades + metadata）
- `WhaleDirectoryView` — A/B/C 級鯨魚白名單

#### Phase 2 視圖（先設計、Phase 2 再實作）
- `StrategyHealthView` — 策略健康儀表板（Brier、回撤、樣本量）
- `PaperEquityView` — 紙上交易淨值曲線（live + experimental 並排）
- `AttributionView` — 週歸因報告
- `PreRegisteredView` — 憲法檢視器（read-only，diff 歷史）

#### Phase 3 視圖（草圖）
- `LiveMonitorView` — 真實下單即時監控
- `CapitalLadderView` — 資本階梯狀態（月 1/2/3）
- `CircuitBreakerView` — 熔斷狀態面板

---

## 第 5 章 Phase 1 具體螢幕設計

### 5.1 `WhaleFeedView` — 鯨魚活動流

**佈局**：三欄
- 左（240px）：篩選 sidebar（tier A/B/C toggle、類別 filter、金額門檻 slider）
- 中央（flexible）：活動時間軸
- 右（320px，可折疊）：選中項目詳情

**時間軸列項內容**（借 Primer timeline 格式）：
```
┌─ 14:23:05 ─────────────────────────────────────────────────────┐
│ [A] 0x74e3...c9a1  BUY  YES @ 0.34   $12,450                  │
│     Market: Will Trump mention "economy" in next speech?       │
│     90d 勝率 67% · 累積 +$34k · 該錢包近 30d 本市場第 3 筆     │
│     [查看此錢包] [查看此市場] [假跟單記錄]                      │
└────────────────────────────────────────────────────────────────┘
```

**色彩應用**：
- 列左緣 border：tier A 紫、B 靛、C 冷灰
- BUY 用 semantic.yes，SELL 用 semantic.no
- 金額 > $10k 用 semantic.warn 標示

**即時性表達**：新項目從頂部滑入（200ms），頂部有 `FreshnessIndicator` 顯示「Live / Last update 3s ago」。

### 5.2 `ActiveMarketsView` — 活躍市場列表

**核心表格欄位**：
| 市場（連結） | 類別 | YES 價 | 24h 成交 | Spread | 鯨魚筆數 | 結算日 |
|---|---|---|---|---|---|---|
| Will Fed raise 25bps? | Macro | 0.63 ↑0.04 | $29.7M | 0.01 | 12 (A:3) | 5d |

**互動**：
- 鍵盤 j/k 移動選中列（Blueprint-style）
- 按 Enter 進入 MarketDetailView
- 欄位排序（預設 24h 成交量降序）
- 第一欄 freeze

**狀態表達**：
- 結算日 < 24h 用 semantic.warn 標示
- spread > 5% 用 semantic.stale（我們不跟這類）

### 5.3 `MarketDetailView` — 單市場詳情

**佈局**：
- 頂部：market header（question、tokens、結算日、類別）
- 左（40%）：`OrderBookLadder`（YES + NO 並排）
- 右上（60%）：價格走勢（`MiniSparkline` 放大版，24h / 7d / 30d 切換）
- 右下（60%）：最近成交 `ActivityFeed`（全量，非僅鯨魚）

**設計重點**：
- OrderBook 用水平長條視覺化每檔位的 size，讓「流動性集中在哪」一眼可見
- 超過 1% 市場 size 的單筆掛單用 semantic.whale 特別標示

### 5.4 `WhaleDirectoryView` — 鯨魚白名單

**佈局**：三頁籤（A / B / C）+ 「波動型（被排除）」

每頁籤是一個密集表格：
| 錢包 | 首次進入 | 90d 交易 | 勝率 | 勝率 3 段 | 累積 PnL | 平均尺寸 | 上次活動 |
|---|---|---|---|---|---|---|---|

「勝率 3 段」顯示 3 個 mini bar，每段 30 天勝率。若其中任一 < 層門檻 × 0.85，該列整條用 semantic.stale 降級。

---

## 第 6 章 實作路徑

### 6.1 立即（Phase 1 啟動前）
1. 建 `apps/web/src/lib/polymarket/tokens.ts` — 色彩、字型、間距 token
2. 擴充 `apps/web/tailwind.config.ts` 讀取 tokens
3. 安裝 shadcn/ui（若尚未）並加入 Button、Badge、DataTable、Tooltip
4. 建 `apps/web/src/components/polymarket/` 資料夾容納 Polymarket 專屬元件
5. 更新 `globals.css`：深色預設、mono 字型、tabular-nums

### 6.2 Phase 1 實作順序（與後端同步）
1. `<FreshnessIndicator>` + `<TierBadge>` + `<KPICard>`（基礎）
2. `<ActivityFeed>` + `<DataTable>`（複合）
3. `WhaleFeedView`（最有價值的單一視圖）
4. `ActiveMarketsView`
5. `MarketDetailView`（含 `<OrderBookLadder>`）
6. `WhaleDirectoryView`

### 6.3 Phase 2+ 的設計更新節奏
- 每個 Phase 啟動前 1 週，專門一天重看本文件並擴充對應視圖的 spec
- Phase 2 的 `PreRegisteredView` 必須出現——這是 pre-registration 憲法的視覺化入口
- Phase 3 的 `CircuitBreakerView` 必須能在緊急情境下被單擊觸發「強制熔斷」（此按鈕為紅色、需 double-click 確認）

### 6.4 已知的反模式（禁止）
- 🚫 用 Recharts 預設配色（它沒有深色語義區分）
- 🚫 用 emoji 作為狀態（🔴🟢🟡 在深色背景下可讀性差，改用 lucide icons）
- 🚫 用 Tailwind 的 blue-500 / green-500（違反原則 3：色彩是語義不是裝飾）
- 🚫 在元件檔中 inline 任何顏色或尺寸（一律走 token）
- 🚫 用 `text-sm` / `text-xs` 等模糊字級（用明確的 px 或 type token）
- 🚫 動畫使用 `ease-bounce` / `ease-elastic`（違反原則 6 的禁跳動）

---

## 附錄 A：對照表 — 設計祖先的具體引用

| 我們做的決策 | 借鑒自 | 具體什麼 |
|---|---|---|
| 4 層 background token | Carbon | `$layer-01..04` tokens |
| j/k 鍵盤導航 | Blueprint | HotkeysProvider pattern |
| FreshnessIndicator 三態 | Cloudscape | StatusIndicator type="success/warning/error" |
| PreRegBlock 的 monospace 格式 | Primer | CodeBlock + DetailsSummary |
| OKLCH 色彩空間 | Carbon (v11+) | 整套 color token 採用 |
| dense table padding | Blueprint | `.bp5-html-table-condensed` |
| KPI card with delta | Cloudscape | `<KeyValuePairs>` |
| Tier badge 軟色調 | Primer | Label variants |

## 附錄 B：版本歷史

| 日期 | 版本 | 變更 |
|---|---|---|
| 2026-04-21 | 1.0 | 初版：4 祖先、6 原則、3 層元件、Phase 1 視圖具體 spec |
