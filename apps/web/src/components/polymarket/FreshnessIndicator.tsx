import { semantic } from '@/lib/polymarket/tokens';

interface FreshnessIndicatorProps {
  lastUpdate: Date | string | null;
  label?: string;
}

/**
 * 三態：
 *   🟢 live     - 更新 < 2 min
 *   🟡 stale    - 2-10 min
 *   🔴 dead     - > 10 min 或 null
 *
 * 時區處理：若字串不帶時區標記（Z / +XX:XX），一律視為 UTC。
 * wrapper 現在寫 ISO 8601 Z 格式，此 fallback 保留處理舊資料。
 */
export function FreshnessIndicator({ lastUpdate, label }: FreshnessIndicatorProps) {
  const ageMs = (() => {
    if (!lastUpdate) return Infinity;
    if (lastUpdate instanceof Date) return Date.now() - lastUpdate.getTime();
    const parsed = parseServerTime(lastUpdate);
    if (parsed === null) return Infinity;
    return Date.now() - parsed;
  })();

  const ageMin = ageMs / 60_000;
  const [color, text] = (() => {
    if (ageMs === Infinity || ageMs < 0) return [semantic.error, '未執行'];
    if (ageMin < 2) return [semantic.live, `${formatAge(ageMs)} 前`];
    if (ageMin < 10) return [semantic.warn, `${formatAge(ageMs)} 前`];
    return [semantic.error, `${formatAge(ageMs)} 前`];
  })();

  const isLive = ageMin < 2 && ageMs >= 0;

  return (
    <span
      className="inline-flex items-center gap-[6px] rounded-full border"
      style={{
        padding: '3px 10px',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: '11px',
        color,
        borderColor: 'color-mix(in oklab, currentColor 40%, transparent)',
        backgroundColor: 'color-mix(in oklab, currentColor 8%, transparent)',
      }}
    >
      <span
        className="inline-block rounded-full"
        style={{
          width: '7px',
          height: '7px',
          backgroundColor: color,
          animation: isLive ? 'poly-pulse 1200ms ease-in-out infinite' : undefined,
        }}
      />
      {label ? <span style={{ opacity: 0.7 }}>{label}</span> : null}
      <span>{text}</span>
    </span>
  );
}

function parseServerTime(s: string): number | null {
  if (!s) return null;
  const trimmed = s.trim();
  // 已有時區標記（Z、+/-XX:XX）交給原生解析
  if (/Z$|[+-]\d\d:?\d\d$/.test(trimmed)) {
    const t = Date.parse(trimmed);
    return Number.isNaN(t) ? null : t;
  }
  // 沒有時區標記 → 當作 UTC 解析
  // 將 "YYYY-MM-DD HH:MM:SS" 轉成 "YYYY-MM-DDTHH:MM:SSZ"
  const iso = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T');
  const t = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
  return Number.isNaN(t) ? null : t;
}

function formatAge(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** 外部元件用：把 server 時間字串轉 Date（或 null），一致 UTC 處理 */
export function parseServerDateStr(s: string | null | undefined): Date | null {
  if (!s) return null;
  const t = parseServerTime(s);
  return t === null ? null : new Date(t);
}
