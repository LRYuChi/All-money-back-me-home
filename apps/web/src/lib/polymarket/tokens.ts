/**
 * Polymarket Design Tokens
 *
 * 真實來源：docs/polymarket/design_system.md §第 3 章
 *
 * 修改規則：
 *   - 新增 token：PR + 說明用途
 *   - 修改既有 token：PR + 理由 + 搜尋影響範圍（grep 使用該 token 的所有檔案）
 *   - 禁止在元件檔內 inline hardcode 色值或尺寸（用 className 讀這裡的 token）
 *
 * 色彩空間：OKLCH（感知均勻，深色模式下同亮度 token 視覺權重一致）
 *   若需轉 hex 可用 https://oklch.com/
 */

// ─────────────────────────────────────────────────────────────────────
// § 3.1 語義色彩（intent-bound）
// 每個顏色都綁定語義，不是裝飾。違反此原則的 PR 會被駁回。
// ─────────────────────────────────────────────────────────────────────

export const semantic = {
  /** 買 YES / 做多 / 看多 */
  yes: "oklch(65% 0.22 25)",
  yesBg: "oklch(25% 0.08 25)",
  yesBorder: "oklch(45% 0.15 25)",

  /** 買 NO / 做空 / 看空 */
  no: "oklch(65% 0.18 155)",
  noBg: "oklch(25% 0.06 155)",
  noBorder: "oklch(45% 0.12 155)",

  /** 鯨魚活動 / 大額 / 可觀察資本流 */
  whale: "oklch(70% 0.18 290)",
  whaleBg: "oklch(25% 0.08 290)",
  whaleBorder: "oklch(50% 0.14 290)",

  /** 風險警告 / 接近門檻 / 需留意 */
  warn: "oklch(75% 0.15 75)",
  warnBg: "oklch(28% 0.06 75)",
  warnBorder: "oklch(55% 0.12 75)",

  /** 錯誤 / 熔斷 / 強制停止 */
  error: "oklch(60% 0.22 15)",
  errorBg: "oklch(25% 0.10 15)",
  errorBorder: "oklch(50% 0.18 15)",

  /** 歷史資料 / 已結算 / 靜態 */
  stale: "oklch(55% 0.02 240)",
  staleBg: "oklch(20% 0.01 240)",
  staleBorder: "oklch(35% 0.02 240)",

  /** 即時資料 / live / 剛更新 */
  live: "oklch(80% 0.15 200)",
  liveBg: "oklch(25% 0.06 200)",
  liveBorder: "oklch(50% 0.12 200)",

  /** 晉升 / 升階 / 達標 */
  tier: "oklch(70% 0.12 90)",
  tierBg: "oklch(25% 0.05 90)",
  tierBorder: "oklch(50% 0.10 90)",
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.2 背景層級（Carbon-inspired）
// layer-00 最深，layer-03 用於選中/hover 高亮
// ─────────────────────────────────────────────────────────────────────

export const layer = {
  /** 頁面底色（整個 app 最底層） */
  "00": "oklch(12% 0.005 240)",
  /** 大型區塊（side panel, main card container） */
  "01": "oklch(16% 0.008 240)",
  /** 巢狀於 01 之上（inner card, filter strip） */
  "02": "oklch(20% 0.010 240)",
  /** 巢狀於 02 之上（選中列、強調格） */
  "03": "oklch(24% 0.012 240)",
} as const;

// 淺色模式（選配）
export const layerLight = {
  "00": "oklch(99% 0.002 240)",
  "01": "oklch(97% 0.003 240)",
  "02": "oklch(95% 0.004 240)",
  "03": "oklch(93% 0.005 240)",
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.2.5 前景色（文字）
// ─────────────────────────────────────────────────────────────────────

export const fg = {
  /** 主要文字（titles、數字） */
  primary: "oklch(96% 0.005 240)",
  /** 次要文字（labels、metadata） */
  secondary: "oklch(72% 0.008 240)",
  /** 第三級（hint、disabled） */
  tertiary: "oklch(55% 0.008 240)",
  /** 反白文字（button on strong background） */
  onStrong: "oklch(99% 0.002 240)",
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.3 字型系統
// ─────────────────────────────────────────────────────────────────────

export const font = {
  /** 數字專用：price, size, pnl。tabular-nums + slashed-zero */
  mono: `"JetBrains Mono", "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace`,
  /** UI 文字 */
  sans: `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`,
} as const;

/** Type scale（px）— 禁用 10 / 18 / 22 等中間值 */
export const fontSize = {
  xs: "11px",
  sm: "12px",
  md: "13px", // 預設 body size（比 Tailwind 14 更緊湊，符合原則 1）
  base: "14px",
  lg: "16px",
  xl: "20px",
  "2xl": "24px",
  "3xl": "32px",
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
} as const;

/** Line-height for each font-size（密集資料需要較緊湊的行高） */
export const lineHeight = {
  tight: 1.25,
  normal: 1.4,
  relaxed: 1.6,
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.4 間距階梯（px）
// 禁用 20/24/32/48 作為元件內部 padding（僅限 page gutters）
// ─────────────────────────────────────────────────────────────────────

export const space = {
  "0.5": "2px",
  "1": "4px",
  "1.5": "6px",
  "2": "8px",
  "3": "12px",
  "4": "16px",
  "6": "24px", // 限頁面邊距
  "8": "32px", // 限 landing / docs
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.5 動效
// 禁止 bounce / elastic / overshoot（違反原則 6）
// ─────────────────────────────────────────────────────────────────────

export const motion = {
  fast: "120ms cubic-bezier(0.0, 0.0, 0.2, 1)",
  base: "200ms cubic-bezier(0.0, 0.0, 0.2, 1)",
  slow: "400ms cubic-bezier(0.4, 0.0, 0.2, 1)",
  /** 即時資料「心跳」脈衝 */
  pulse: "1200ms cubic-bezier(0.4, 0.0, 0.6, 1) infinite",
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.6 邊框與圓角
// ─────────────────────────────────────────────────────────────────────

export const radius = {
  none: "0",
  sm: "2px",
  md: "4px",
  lg: "6px",
  full: "9999px",
} as const;

export const border = {
  /** 最細的分隔線，用於表格列、卡片邊緣 */
  hair: `1px solid oklch(28% 0.008 240)`,
  /** 一般邊框 */
  base: `1px solid oklch(32% 0.010 240)`,
  /** 強調邊框（focus, selected） */
  strong: `1px solid oklch(50% 0.014 240)`,
} as const;

// ─────────────────────────────────────────────────────────────────────
// § 3.7 Tier 專屬色彩（A/B/C 級鯨魚 + 波動型）
// ─────────────────────────────────────────────────────────────────────

export const tier = {
  A: {
    fg: semantic.whale,
    bg: semantic.whaleBg,
    border: semantic.whaleBorder,
    label: "Tier A",
  },
  B: {
    fg: "oklch(70% 0.15 260)", // 靛藍
    bg: "oklch(25% 0.06 260)",
    border: "oklch(50% 0.12 260)",
    label: "Tier B",
  },
  C: {
    fg: semantic.stale,
    bg: semantic.staleBg,
    border: semantic.staleBorder,
    label: "Tier C",
  },
  volatile: {
    fg: "oklch(55% 0.04 30)", // 弱橙（降級）
    bg: "oklch(20% 0.02 30)",
    border: "oklch(35% 0.04 30)",
    label: "波動型",
  },
  excluded: {
    fg: "oklch(45% 0.005 240)",
    bg: "oklch(18% 0.005 240)",
    border: "oklch(28% 0.005 240)",
    label: "排除",
  },
} as const;

// ─────────────────────────────────────────────────────────────────────
// 匯出型別（供 TS 使用處型別推導）
// ─────────────────────────────────────────────────────────────────────

export type SemanticToken = keyof typeof semantic;
export type LayerToken = keyof typeof layer;
export type SpaceToken = keyof typeof space;
export type TierLevel = keyof typeof tier;

/** 一站式匯出：給 tailwind.config 讀取 */
export const tokens = {
  semantic,
  layer,
  layerLight,
  fg,
  font,
  fontSize,
  fontWeight,
  lineHeight,
  space,
  motion,
  radius,
  border,
  tier,
} as const;

export default tokens;
