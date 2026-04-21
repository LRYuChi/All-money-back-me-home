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
 */
export function FreshnessIndicator({ lastUpdate, label }: FreshnessIndicatorProps) {
  const ageMs = (() => {
    if (!lastUpdate) return Infinity;
    const d = typeof lastUpdate === 'string' ? new Date(lastUpdate) : lastUpdate;
    return Date.now() - d.getTime();
  })();

  const ageMin = ageMs / 60_000;
  const [color, text] = (() => {
    if (ageMs === Infinity) return [semantic.error, '未執行'];
    if (ageMin < 2) return [semantic.live, `live · ${formatAge(ageMs)}前`];
    if (ageMin < 10) return [semantic.warn, `${formatAge(ageMs)}前`];
    return [semantic.error, `${formatAge(ageMs)}前（延遲）`];
  })();

  return (
    <span
      className="inline-flex items-center gap-[6px]"
      style={{ fontFamily: 'var(--font-mono, ui-monospace)', fontSize: '12px' }}
    >
      <span
        className="inline-block rounded-full"
        style={{
          width: '8px',
          height: '8px',
          backgroundColor: color,
          animation: ageMin < 2 ? 'poly-pulse 1200ms ease-in-out infinite' : undefined,
        }}
      />
      <span style={{ color }}>{label ?? ''} {text}</span>
    </span>
  );
}

function formatAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h`;
}
