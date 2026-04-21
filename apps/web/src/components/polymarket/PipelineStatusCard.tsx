'use client';

import { layer, fg, semantic } from '@/lib/polymarket/tokens';
import { FreshnessIndicator } from './FreshnessIndicator';

interface Status {
  last_run_start: string | null;
  last_run_end: string | null;
  duration_seconds: number | null;
  result: 'ok' | 'fail' | 'never_run' | null;
  exit_code: number | null;
  mode: string | null;
  markets_limit: number | null;
  wallets_cap: number | null;
}

export function PipelineStatusCard({ status }: { status: Status | null }) {
  const ok = status?.result === 'ok';
  const fail = status?.result === 'fail';
  const color = ok ? semantic.live : fail ? semantic.error : semantic.warn;

  return (
    <div
      className="rounded-md p-4 border"
      style={{
        backgroundColor: layer['01'],
        borderColor: ok ? 'oklch(30% 0.06 200)' : 'oklch(30% 0.010 240)',
        color: fg.primary,
      }}
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Pipeline 狀態
          </div>
          <div className="mt-1 flex items-baseline gap-3">
            <span
              style={{
                color,
                fontSize: '24px',
                fontWeight: 600,
                fontFamily: 'var(--font-mono, ui-monospace)',
                letterSpacing: '0.02em',
              }}
            >
              {status?.result ?? '—'}
            </span>
            {status?.duration_seconds != null && (
              <span style={{ color: fg.secondary, fontSize: '13px', fontFamily: 'var(--font-mono)' }}>
                {status.duration_seconds}s
              </span>
            )}
          </div>
        </div>
        <FreshnessIndicator lastUpdate={status?.last_run_end ?? null} />
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-4">
        <KV label="上次開始" value={status?.last_run_start ?? '—'} mono />
        <KV label="上次結束" value={status?.last_run_end ?? '—'} mono />
        <KV label="模式" value={status?.mode ?? '—'} />
        <KV label="退出碼" value={status?.exit_code?.toString() ?? '—'} mono />
        <KV label="單次 markets" value={status?.markets_limit?.toString() ?? '—'} mono />
        <KV label="單次 wallets" value={status?.wallets_cap?.toString() ?? '—'} mono />
      </div>
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div style={{ color: fg.tertiary, fontSize: '11px' }}>{label}</div>
      <div
        style={{
          color: fg.primary,
          fontSize: '12px',
          fontFamily: mono ? 'var(--font-mono, ui-monospace)' : undefined,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
    </div>
  );
}
