'use client';

import { useState } from 'react';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';
import { TierBadge } from './TierBadge';
import { parseServerDateStr } from './FreshnessIndicator';

interface Whale {
  wallet_address: string;
  tier: string;
  trade_count_90d: number;
  win_rate: number;
  cumulative_pnl: number;
  avg_trade_size: number;
  segment_win_rates: number[];
  stability_pass: boolean;
  resolved_count: number;
  last_trade_at: string | null;
}

export function WhaleDirectoryTable({ whales }: { whales: Whale[] }) {
  const [tierFilter, setTierFilter] = useState<string>('all');
  const tiers = Array.from(new Set(whales.map((w) => w.tier))).sort();
  const filtered = tierFilter === 'all' ? whales : whales.filter((w) => w.tier === tierFilter);

  return (
    <Card>
      <CardHeader
        eyebrow="鯨魚目錄"
        subtitle={`共 ${filtered.length} 個錢包 · 依累積 PnL 排序`}
        trailing={
          <div className="flex gap-1 flex-wrap justify-end">
            <FilterButton
              active={tierFilter === 'all'}
              onClick={() => setTierFilter('all')}
              label="全部"
              count={whales.length}
            />
            {tiers.map((t) => (
              <FilterButton
                key={t}
                active={tierFilter === t}
                onClick={() => setTierFilter(t)}
                label={tierLabel(t)}
                count={whales.filter((w) => w.tier === t).length}
              />
            ))}
          </div>
        }
        divider
      />

      <div className="overflow-x-auto">
        <table
          className="w-full text-left"
          style={{
            fontSize: '12px',
            fontVariantNumeric: 'tabular-nums',
            borderCollapse: 'separate',
            borderSpacing: 0,
          }}
        >
          <thead>
            <tr style={{ color: fg.tertiary, fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              <Th>Tier</Th>
              <Th>錢包</Th>
              <Th right>交易 90d</Th>
              <Th right>已結算</Th>
              <Th right>勝率</Th>
              <Th right>累積 PnL</Th>
              <Th right>平均尺寸</Th>
              <Th>3 段穩定性</Th>
              <Th>最近交易</Th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="text-center"
                  style={{ color: fg.tertiary, padding: '48px 20px', fontSize: '12px' }}
                >
                  尚無符合條件的錢包
                  <div style={{ fontSize: '11px', color: fg.tertiary, marginTop: '4px', opacity: 0.7 }}>
                    Pipeline 需累積數天資料才會有 A/B/C 級鯨魚
                  </div>
                </td>
              </tr>
            )}
            {filtered.slice(0, 50).map((w, i) => {
              const pnlColor = w.cumulative_pnl > 0 ? semantic.live : w.cumulative_pnl < 0 ? semantic.error : fg.tertiary;
              const lastTrade = parseServerDateStr(w.last_trade_at);
              return (
                <tr
                  key={w.wallet_address}
                  style={{
                    backgroundColor: i % 2 === 0 ? 'transparent' : 'color-mix(in oklab, white 2%, transparent)',
                    color: fg.primary,
                  }}
                >
                  <Td first>
                    <TierBadge tier={w.tier} />
                  </Td>
                  <Td>
                    <code style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>
                      {w.wallet_address.slice(0, 6)}…{w.wallet_address.slice(-4)}
                    </code>
                  </Td>
                  <Td right mono>{w.trade_count_90d}</Td>
                  <Td right mono>{w.resolved_count}</Td>
                  <Td right mono>{(w.win_rate * 100).toFixed(1)}%</Td>
                  <Td right mono style={{ color: pnlColor, fontWeight: 500 }}>
                    {w.cumulative_pnl > 0 ? '+' : ''}${Math.round(w.cumulative_pnl).toLocaleString()}
                  </Td>
                  <Td right mono>${Math.round(w.avg_trade_size).toLocaleString()}</Td>
                  <Td>
                    <StabilityBars segments={w.segment_win_rates} pass={w.stability_pass} />
                  </Td>
                  <Td style={{ color: fg.tertiary, fontSize: '11px' }}>
                    {lastTrade ? formatRelative(Date.now() - lastTrade.getTime()) : '—'}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function tierLabel(t: string): string {
  if (t === 'volatile') return '波動';
  if (t === 'excluded') return '排除';
  return t;
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      style={{
        padding: '10px 12px',
        fontWeight: 500,
        textAlign: right ? 'right' : 'left',
        borderBottom: `1px solid ${borderColor.hair}`,
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  mono,
  first,
  style,
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  first?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td
      style={{
        padding: '10px 12px',
        paddingLeft: first ? '20px' : '12px',
        borderBottom: `1px solid ${borderColor.hair}`,
        textAlign: right ? 'right' : 'left',
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {children}
    </td>
  );
}

function FilterButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-full border transition-colors"
      style={{
        padding: '3px 10px',
        fontSize: '11px',
        backgroundColor: active ? layer['03'] : layer['02'],
        color: active ? fg.primary : fg.secondary,
        borderColor: active ? borderColor.strong : borderColor.hair,
      }}
    >
      <span>{label}</span>
      <span
        style={{
          marginLeft: '6px',
          color: active ? fg.secondary : fg.tertiary,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {count}
      </span>
    </button>
  );
}

function StabilityBars({ segments, pass }: { segments: number[]; pass: boolean }) {
  const safe = Array.from({ length: 3 }, (_, i) => segments[i] ?? -1);
  return (
    <div className="flex items-center gap-1">
      {safe.map((rate, i) => {
        const hasData = rate >= 0;
        const width = hasData ? Math.max(6, Math.round(rate * 36)) : 36;
        const color = !hasData
          ? semantic.stale
          : rate >= 0.5
          ? semantic.live
          : rate >= 0.3
          ? semantic.warn
          : semantic.error;
        return (
          <div
            key={i}
            title={hasData ? `段 ${i}: ${(rate * 100).toFixed(0)}%` : `段 ${i}: 樣本不足`}
            style={{
              width: `${width}px`,
              height: '5px',
              backgroundColor: color,
              borderRadius: '1px',
              opacity: hasData ? 0.85 : 0.25,
            }}
          />
        );
      })}
      <span
        className="ml-1"
        style={{
          fontSize: '10px',
          color: pass ? semantic.live : fg.tertiary,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        }}
      >
        {pass ? '✓' : '✗'}
      </span>
    </div>
  );
}

function formatRelative(ms: number): string {
  if (ms < 0) return '—';
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)} 分鐘前`;
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)} 小時前`;
  return `${Math.floor(ms / 86400_000)} 天前`;
}
