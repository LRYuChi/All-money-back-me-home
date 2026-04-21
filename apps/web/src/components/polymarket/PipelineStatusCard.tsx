'use client';

import { borderColor, fg, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';
import { FreshnessIndicator, parseServerDateStr } from './FreshnessIndicator';

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

const STATUS_META = {
  ok: { label: '運行正常', color: semantic.live },
  fail: { label: '失敗', color: semantic.error },
  never_run: { label: '尚未執行', color: semantic.stale },
} as const;

export function PipelineStatusCard({ status }: { status: Status | null }) {
  const key = (status?.result ?? 'never_run') as keyof typeof STATUS_META;
  const meta = STATUS_META[key] ?? STATUS_META.never_run;
  const lastEnd = parseServerDateStr(status?.last_run_end ?? null);

  return (
    <Card accentColor={meta.color}>
      <CardHeader
        eyebrow="Pipeline 狀態"
        trailing={<FreshnessIndicator lastUpdate={lastEnd} />}
      />

      {/* 主視覺 */}
      <div style={{ padding: '4px 20px 20px' }}>
        <div className="flex items-baseline gap-3" style={{ marginBottom: '2px' }}>
          <span
            style={{
              color: meta.color,
              fontSize: '32px',
              fontWeight: 600,
              letterSpacing: '-0.01em',
              lineHeight: 1,
            }}
          >
            {meta.label}
          </span>
          {status?.duration_seconds != null && (
            <span
              style={{
                color: fg.tertiary,
                fontSize: '13px',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              耗時 {status.duration_seconds}s
            </span>
          )}
        </div>
        <div style={{ color: fg.tertiary, fontSize: '12px' }}>
          每 5 分鐘自動觸發 · {status?.mode === 'docker' ? 'Docker Compose 模式' : '本機模式'}
        </div>
      </div>

      {/* KV 區 */}
      <dl
        className="grid grid-cols-2 md:grid-cols-4"
        style={{
          borderTop: `1px solid ${borderColor.hair}`,
          margin: 0,
        }}
      >
        <KV label="上次開始" value={formatLocal(status?.last_run_start)} mono />
        <KV label="上次結束" value={formatLocal(status?.last_run_end)} mono border />
        <KV label="每次掃市場數" value={status?.markets_limit?.toString() ?? '—'} mono border />
        <KV label="每次掃錢包數" value={status?.wallets_cap?.toString() ?? '—'} mono border />
      </dl>
    </Card>
  );
}

function KV({
  label,
  value,
  mono,
  border,
}: {
  label: string;
  value: string;
  mono?: boolean;
  border?: boolean;
}) {
  return (
    <div
      style={{
        padding: '12px 20px',
        borderLeft: border ? `1px solid ${borderColor.hair}` : undefined,
      }}
    >
      <dt style={{ color: fg.tertiary, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {label}
      </dt>
      <dd
        style={{
          color: fg.primary,
          fontSize: '13px',
          marginTop: '4px',
          fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </dd>
    </div>
  );
}

function formatLocal(s: string | null | undefined): string {
  if (!s) return '—';
  const d = parseServerDateStr(s);
  if (!d) return s;
  return d.toLocaleString('zh-TW', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}
