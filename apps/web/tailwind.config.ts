import type { Config } from "tailwindcss";
import { tokens } from "./src/lib/polymarket/tokens";

/**
 * Polymarket 設計系統延伸 Tailwind 配置。
 * Token 來源：src/lib/polymarket/tokens.ts（勿在此 inline 覆寫）
 *
 * 使用範例：
 *   text-poly-semantic-yes       ─→  YES 色
 *   bg-poly-layer-01             ─→  第一層背景
 *   px-poly-2                    ─→  8px padding
 *   font-poly-mono               ─→  等寬數字字型
 *   text-poly-md                 ─→  13px 字級
 */
const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        // Polymarket 語義色（OKLCH）— 命名前綴 "poly-" 避免與 Tailwind 預設碰撞
        "poly-semantic": tokens.semantic,
        "poly-layer": tokens.layer,
        "poly-fg": tokens.fg,
        "poly-tier-a": tokens.tier.A.fg,
        "poly-tier-b": tokens.tier.B.fg,
        "poly-tier-c": tokens.tier.C.fg,
        "poly-tier-volatile": tokens.tier.volatile.fg,
      },
      fontFamily: {
        "poly-mono": tokens.font.mono,
        "poly-sans": tokens.font.sans,
      },
      fontSize: {
        "poly-xs": tokens.fontSize.xs,
        "poly-sm": tokens.fontSize.sm,
        "poly-md": tokens.fontSize.md,
        "poly-base": tokens.fontSize.base,
        "poly-lg": tokens.fontSize.lg,
        "poly-xl": tokens.fontSize.xl,
        "poly-2xl": tokens.fontSize["2xl"],
        "poly-3xl": tokens.fontSize["3xl"],
      },
      spacing: {
        "poly-0.5": tokens.space["0.5"],
        "poly-1": tokens.space["1"],
        "poly-1.5": tokens.space["1.5"],
        "poly-2": tokens.space["2"],
        "poly-3": tokens.space["3"],
        "poly-4": tokens.space["4"],
        "poly-6": tokens.space["6"],
        "poly-8": tokens.space["8"],
      },
      borderRadius: {
        "poly-sm": tokens.radius.sm,
        "poly-md": tokens.radius.md,
        "poly-lg": tokens.radius.lg,
      },
      transitionDuration: {
        "poly-fast": "120ms",
        "poly-base": "200ms",
        "poly-slow": "400ms",
      },
      transitionTimingFunction: {
        "poly-out": "cubic-bezier(0.0, 0.0, 0.2, 1)",
        "poly-inout": "cubic-bezier(0.4, 0.0, 0.2, 1)",
      },
      animation: {
        "poly-pulse": "poly-pulse 1200ms cubic-bezier(0.4, 0.0, 0.6, 1) infinite",
      },
      keyframes: {
        "poly-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
