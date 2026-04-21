'use client';

import { borderColor, fg, layer, semantic, tier as tierTokens } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';

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

const TIER_ORDER = ['A', 'B', 'C', 'volatile', 'excluded'] as const;

export function OverviewCards({ overview }: { overview: Overview | null }) {
  const dist = overview?.tier_distribution ?? {};
  const totals = overview?.totals ?? { markets: 0, active_markets: 0, whales: 0, trades: 0 };
  const act = overview?.activity_24h ?? { trades: 0, alerts: 0 };

  const maxCount = Math.max(1, ...TIER_ORDER.map((t) => dist[t] ?? 0));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
      {/* Tier distribution - 佔 3 欄 */}
      <div className="lg:col-span-3">
        <Card>
          <CardHeader
            eyebrow="鯨魚層級分布"
            subtitle={overview ? `共 ${overview.totals.whales.toLocaleString()} 個錢包已分析` : undefined}
            divider
          />
          <div style={{ padding: '14px 20px 16px' }}>
            <ul style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {TIER_ORDER.map((t) => {
                const count = dist[t] ?? 0;
                const colors =
                  (tierTokens as Record<
                    string,
                    { fg: string; bg: string; border: string; label: string } | undefined
                  >)[t] ?? {
                    fg: fg.tertiary,
                    bg: layer['02'],
                    border: borderColor.hair,
                    label: t,
                  };
                const pct = count / maxCount;
                return (
                  <li key={t} className="flex items-center gap-3" style={{ fontSize: '12px' }}>
                    <span
                      className="inline-flex items-center justify-center rounded"
                      style={{
                        width: '32px',
                        height: '24px',
                        color: colors.fg,
                        backgroundColor: colors.bg,
                        border: `1px solid ${colors.border}`,
                        fontSize: '11px',
                        fontWeight: 600,
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      }}
                    >
                      {t === 'volatile' ? 'V' : t === 'excluded' ? '—' : t}
                    </span>
                    <span style={{ color: fg.secondary, flex: '0 0 72px' }}>{colors.label}</span>
                    <div
                      className="flex-1 relative"
                      style={{ height: '8px', backgroundColor: layer['00'], borderRadius: '2px' }}
                    >
                      <div
                        style={{
                          width: `${Math.max(2, pct * 100)}%`,
                          height: '100%',
                          backgroundColor: colors.fg,
                          opacity: count === 0 ? 0.15 : 0.85,
                          borderRadius: '2px',
                          transition: 'width 200ms ease-out',
                        }}
                      />
                    </div>
                    <span
                      className="flex-none text-right"
                      style={{
                        width: '56px',
                        color: count > 0 ? fg.primary : fg.tertiary,
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                        fontVariantNumeric: 'tabular-nums',
                        fontSize: '13px',
                        fontWeight: 500,
                      }}
                    >
                      {count.toLocaleString()}
                    </span>
                  </li>
                );
              })}
            </ul>

            {overview?.latest_tier_change && (
              <div
                className="mt-4 pt-3 flex items-center gap-2"
                style={{
                  borderTop: `1px solid ${borderColor.hair}`,
                  color: fg.tertiary,
                  fontSize: '11px',
                }}
              >
                <span style={{ color: fg.secondary }}>最新變動</span>
                <code
                  style={{
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    color: fg.secondary,
                  }}
                >
                  {overview.latest_tier_change.wallet_address.slice(0, 10)}…
                </code>
                <span style={{ color: fg.tertiary }}>
                  {overview.latest_tier_change.from_tier ?? '(新)'} →{' '}
                  <span style={{ color: fg.primary }}>{overview.latest_tier_change.to_tier}</span>
                </span>
                <span style={{ color: fg.tertiary }}>· {overview.latest_tier_change.reason}</span>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* Activity / totals - 佔 2 欄 */}
      <div className="lg:col-span-2 flex flex-col gap-3">
        <Card>
          <CardHeader eyebrow="資料總計" divider />
          <div style={{ padding: '14px 20px 16px' }}>
            <div className="grid grid-cols-2 gap-y-3 gap-x-4">
              <Metric label="活躍市場" value={`${totals.active_markets}`} sub={`/ ${totals.markets} 總數`} />
              <Metric label="追蹤錢包" value={totals.whales.toLocaleString()} sub="已分類" />
              <Metric label="成交紀錄" value={totals.trades.toLocaleString()} sub="資料庫累積" />
              <Metric
                label="24h 成交"
                value={act.trades.toLocaleString()}
                sub="最近 24 小時"
              />
            </div>
          </div>
        </Card>

        <Card accentColor={act.alerts > 0 ? semantic.whale : undefined}>
          <CardHeader eyebrow="24h 鯨魚推播" divider />
          <div
            style={{
              padding: '14px 20px 16px',
              display: 'flex',
              alignItems: 'baseline',
              gap: '12px',
            }}
          >
            <span
              style={{
                color: act.alerts > 0 ? semantic.whale : fg.tertiary,
                fontSize: '32px',
                fontWeight: 600,
                letterSpacing: '-0.01em',
                lineHeight: 1,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {act.alerts}
            </span>
            <span style={{ color: fg.tertiary, fontSize: '12px' }}>
              {act.alerts === 0 ? '等候 A/B/C 級鯨魚出現' : '筆推播'}
            </span>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div
        style={{
          color: fg.tertiary,
          fontSize: '10px',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        {label}
      </div>
      <div
        style={{
          color: fg.primary,
          fontSize: '20px',
          fontWeight: 500,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1.1,
          marginTop: '2px',
        }}
      >
        {value}
      </div>
      {sub && <div style={{ color: fg.tertiary, fontSize: '10px', marginTop: '1px' }}>{sub}</div>}
    </div>
  );
}
