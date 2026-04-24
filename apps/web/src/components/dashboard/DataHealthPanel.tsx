'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * DataHealthPanel — Overview 頁頂部的資料管線健康面板。
 *
 * 機構交易軟體都有類似的「Market Data: OK / Broker: OK / Risk: OK」
 * 橫向指示條。這裡把我們的三條管線攤平顯示：
 *   - Polymarket scanner (5min cron)
 *   - Smart Money (Supabase sm_*, manual scan)
 *   - Freqtrade Supertrend (15m timeframe)
 *
 * 資料源: GET /api/system/data-health (15s cache)
 * 自動每 20 秒 refetch。
 */

type Health = 'green' | 'yellow' | 'red' | 'unknown';

interface PipelineHealth {
  name: string;
  configured: boolean;
  expected_cadence_s: number | null;
  last_data_at: string | null;
  age_seconds: number | null;
  health: Health;
  // pipeline-specific extras
  trades_24h?: number;
  trades_1h?: number;
  trades_5m?: number;
  alerts_24h?: number;
  paper_trades_total?: number;
  wallets_total?: number;
  trades_total?: number | null;
  latest_snapshot_date?: string | null;
  scan_cadence?: string;
  state?: string | null;
  dry_run?: boolean | null;
  strategy?: string | null;
  pairs_count?: number;
  open_trades?: number;
  profit?: number;
  error?: string;
}

interface HealthPayload {
  overall_health: Health;
  checked_at: string;
  elapsed_ms: number;
  pipelines: PipelineHealth[];
}

const REFRESH_MS = 20_000;

export function DataHealthPanel() {
  const [data, setData] = useState<HealthPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const d = await apiClient.get<HealthPayload>('/api/system/data-health');
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  return (
    <div
      style={{
        backgroundColor: layer['01'],
        border: `1px solid ${borderColor.hair}`,
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          height: 28,
          padding: '0 12px',
          borderBottom: `1px solid ${borderColor.hair}`,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          backgroundColor: layer['01'],
          fontSize: 10,
          letterSpacing: 1,
          textTransform: 'uppercase',
          color: fg.tertiary,
        }}
      >
        <HealthDot health={data?.overall_health ?? 'unknown'} />
        <span>Data Pipelines</span>
        {data && (
          <span style={{ color: fg.tertiary, fontFamily: 'ui-monospace, monospace', fontVariantNumeric: 'tabular-nums' }}>
            · probed {data.elapsed_ms}ms
          </span>
        )}
        {error && (
          <span style={{ color: semantic.error, marginLeft: 8 }}>
            {error}
          </span>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
        }}
      >
        {(data?.pipelines ?? []).map((p, i) => (
          <PipelineCell key={p.name} p={p} isFirst={i === 0} />
        ))}
        {!data &&
          ['polymarket', 'smart_money', 'freqtrade_supertrend'].map((n, i) => (
            <PipelineSkeleton key={n} isFirst={i === 0} />
          ))}
      </div>
    </div>
  );
}

function PipelineCell({ p, isFirst }: { p: PipelineHealth; isFirst: boolean }) {
  return (
    <div
      style={{
        padding: '10px 14px',
        borderLeft: isFirst ? 'none' : `1px solid ${borderColor.hair}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <HealthDot health={p.health} />
        <span style={{ fontSize: 12, fontWeight: 500, color: fg.primary }}>
          {labelForName(p.name)}
        </span>
        {p.age_seconds != null && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 10,
              color: ageColor(p.health),
              fontFamily: 'ui-monospace, monospace',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {fmtAge(p.age_seconds)} ago
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: 10,
          color: fg.tertiary,
          fontFamily: 'ui-monospace, monospace',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1.5,
        }}
      >
        {p.name === 'polymarket' && (
          <>
            <span>trades: 5m={p.trades_5m ?? '-'} · 1h={p.trades_1h ?? '-'} · 24h={p.trades_24h ?? '-'}</span>
            <br />
            <span>alerts 24h: {p.alerts_24h ?? '-'} · paper_trades: {p.paper_trades_total ?? '-'}</span>
          </>
        )}
        {p.name === 'smart_money' && (
          <>
            <span>
              wallets: {p.wallets_total ?? '-'} · trades:{' '}
              {p.trades_total == null ? 'count err' : p.trades_total.toLocaleString()}
            </span>
            <br />
            <span>
              snapshot: {p.latest_snapshot_date ?? 'none'} · mode: {p.scan_cadence ?? '-'}
            </span>
          </>
        )}
        {p.name === 'freqtrade_supertrend' && (
          <>
            <span>
              {p.state ?? '-'} · {p.dry_run ? 'dry-run' : 'LIVE'} · {p.strategy ?? '-'}
            </span>
            <br />
            <span>
              pairs: {p.pairs_count ?? 0} · trades: {p.trades_total ?? 0} · open: {p.open_trades ?? 0}
            </span>
          </>
        )}
        {p.error && (
          <div style={{ color: semantic.error, marginTop: 2 }}>error: {p.error}</div>
        )}
      </div>
    </div>
  );
}

function PipelineSkeleton({ isFirst }: { isFirst: boolean }) {
  return (
    <div
      style={{
        padding: '10px 14px',
        borderLeft: isFirst ? 'none' : `1px solid ${borderColor.hair}`,
        color: fg.tertiary,
        fontSize: 11,
      }}
    >
      loading…
    </div>
  );
}

function HealthDot({ health }: { health: Health }) {
  const color =
    health === 'green'
      ? semantic.live
      : health === 'yellow'
        ? semantic.warn
        : health === 'red'
          ? semantic.error
          : fg.tertiary;
  const pulse = health === 'green';
  return (
    <span
      style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        backgroundColor: color,
        boxShadow: pulse ? `0 0 6px ${color}` : 'none',
        animation: pulse ? 'ambmh-pulse 1200ms ease-in-out infinite' : 'none',
        display: 'inline-block',
        flexShrink: 0,
      }}
    />
  );
}

function labelForName(n: string): string {
  switch (n) {
    case 'polymarket':
      return 'Polymarket Scanner';
    case 'smart_money':
      return 'Smart Money (HL)';
    case 'freqtrade_supertrend':
      return 'Freqtrade · Supertrend';
    default:
      return n;
  }
}

function ageColor(h: Health): string {
  return h === 'green' ? semantic.live : h === 'yellow' ? semantic.warn : h === 'red' ? semantic.error : fg.tertiary;
}

function fmtAge(secs: number): string {
  if (secs < 60) return `${Math.floor(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}
