'use client';

import { fg, layer, semantic, tier as tierTokens } from '@/lib/polymarket/tokens';

interface Overview {
  tier_distribution: Record<string, number>;
  totals: {
    markets: number;
    active_markets: number;
    whales: number;
    trades: number;
  };
  activity_24h: {
    trades: number;
    alerts: number;
  };
  latest_tier_change: {
    wallet_address: string;
    from_tier: string | null;
    to_tier: string;
    changed_at: string;
    reason: string;
  } | null;
}

export function OverviewCards({ overview }: { overview: Overview | null }) {
  const dist = overview?.tier_distribution ?? {};
  const totals = overview?.totals ?? { markets: 0, active_markets: 0, whales: 0, trades: 0 };
  const act = overview?.activity_24h ?? { trades: 0, alerts: 0 };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {/* Tier distribution */}
      <div
        className="rounded-md p-4 border"
        style={{ backgroundColor: layer['01'], borderColor: 'oklch(30% 0.010 240)', color: fg.primary }}
      >
        <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          鯨魚層級分布
        </div>
        <div className="mt-3 grid grid-cols-5 gap-2">
          {(['A', 'B', 'C', 'volatile', 'excluded'] as const).map((t) => {
            const count = dist[t] ?? 0;
            const colors =
              (tierTokens as Record<string, { fg: string; bg: string; border: string; label: string } | undefined>)[t] ?? {
                fg: fg.tertiary,
                bg: layer['02'],
                border: 'oklch(28% 0.008 240)',
                label: t,
              };
            return (
              <div
                key={t}
                className="rounded p-2 border flex flex-col items-center"
                style={{
                  backgroundColor: colors.bg,
                  borderColor: colors.border,
                }}
              >
                <div
                  style={{
                    color: colors.fg,
                    fontSize: '20px',
                    fontFamily: 'var(--font-mono, ui-monospace)',
                    fontWeight: 600,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {count}
                </div>
                <div style={{ color: fg.tertiary, fontSize: '10px', marginTop: '2px' }}>{colors.label}</div>
              </div>
            );
          })}
        </div>
        {overview?.latest_tier_change && (
          <div
            className="mt-3 text-[11px]"
            style={{ color: fg.tertiary, fontFamily: 'var(--font-mono, ui-monospace)' }}
          >
            最新變動：{overview.latest_tier_change.wallet_address.slice(0, 10)}…{' '}
            <span style={{ color: fg.secondary }}>
              {overview.latest_tier_change.from_tier ?? '(新)'} → {overview.latest_tier_change.to_tier}
            </span>{' '}
            ({overview.latest_tier_change.reason})
          </div>
        )}
      </div>

      {/* Totals + 24h activity */}
      <div
        className="rounded-md p-4 border"
        style={{ backgroundColor: layer['01'], borderColor: 'oklch(30% 0.010 240)', color: fg.primary }}
      >
        <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          總計 / 24h 活動
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Metric label="活躍市場" value={`${totals.active_markets} / ${totals.markets}`} />
          <Metric label="追蹤錢包" value={totals.whales.toLocaleString()} />
          <Metric label="成交紀錄" value={totals.trades.toLocaleString()} />
          <Metric
            label="24h 成交"
            value={act.trades.toLocaleString()}
            sub={`${act.alerts} 筆鯨魚推播`}
            accent={act.alerts > 0 ? semantic.whale : undefined}
          />
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div>
      <div style={{ color: fg.tertiary, fontSize: '11px' }}>{label}</div>
      <div
        style={{
          color: accent ?? fg.primary,
          fontSize: '18px',
          fontFamily: 'var(--font-mono, ui-monospace)',
          fontWeight: 500,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
      {sub && <div style={{ color: fg.tertiary, fontSize: '10px' }}>{sub}</div>}
    </div>
  );
}
