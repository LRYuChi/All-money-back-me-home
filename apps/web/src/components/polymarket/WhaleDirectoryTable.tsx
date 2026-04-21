'use client';

import { useState } from 'react';
import { fg, layer, semantic } from '@/lib/polymarket/tokens';
import { TierBadge } from './TierBadge';

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
    <div
      className="rounded-md border"
      style={{ backgroundColor: layer['01'], borderColor: 'oklch(30% 0.010 240)' }}
    >
      <div className="flex items-center justify-between p-4 pb-3">
        <div>
          <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            鯨魚目錄
          </div>
          <div style={{ color: fg.tertiary, fontSize: '11px', marginTop: '2px' }}>
            共 {filtered.length} 個錢包 · 依累積 PnL 排序
          </div>
        </div>
        <div className="flex gap-1">
          <TierFilterButton
            active={tierFilter === 'all'}
            onClick={() => setTierFilter('all')}
            label={`全部 (${whales.length})`}
          />
          {tiers.map((t) => (
            <TierFilterButton
              key={t}
              active={tierFilter === t}
              onClick={() => setTierFilter(t)}
              label={`${t} (${whales.filter((w) => w.tier === t).length})`}
            />
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table
          className="w-full text-left"
          style={{ fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}
        >
          <thead>
            <tr style={{ color: fg.tertiary, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              <th className="px-4 py-2 font-normal">Tier</th>
              <th className="px-2 py-2 font-normal">錢包</th>
              <th className="px-2 py-2 font-normal text-right">交易 90d</th>
              <th className="px-2 py-2 font-normal text-right">已結算</th>
              <th className="px-2 py-2 font-normal text-right">勝率</th>
              <th className="px-2 py-2 font-normal text-right">累積 PnL</th>
              <th className="px-2 py-2 font-normal text-right">平均尺寸</th>
              <th className="px-2 py-2 font-normal">3 段穩定性</th>
              <th className="px-2 py-2 font-normal">最近交易</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center" style={{ color: fg.tertiary }}>
                  尚無符合條件的錢包（Pipeline 需累積數天資料）
                </td>
              </tr>
            )}
            {filtered.slice(0, 50).map((w, i) => (
              <tr
                key={w.wallet_address}
                style={{
                  backgroundColor: i % 2 === 0 ? layer['01'] : layer['02'],
                  borderTop: '1px solid oklch(24% 0.010 240)',
                  color: fg.primary,
                }}
              >
                <td className="px-4 py-2">
                  <TierBadge tier={w.tier} />
                </td>
                <td className="px-2 py-2" style={{ fontFamily: 'var(--font-mono, ui-monospace)' }}>
                  {w.wallet_address.slice(0, 6)}…{w.wallet_address.slice(-4)}
                </td>
                <td className="px-2 py-2 text-right" style={{ fontFamily: 'var(--font-mono)' }}>
                  {w.trade_count_90d}
                </td>
                <td className="px-2 py-2 text-right" style={{ fontFamily: 'var(--font-mono)' }}>
                  {w.resolved_count}
                </td>
                <td className="px-2 py-2 text-right" style={{ fontFamily: 'var(--font-mono)' }}>
                  {(w.win_rate * 100).toFixed(1)}%
                </td>
                <td
                  className="px-2 py-2 text-right"
                  style={{
                    fontFamily: 'var(--font-mono)',
                    color: w.cumulative_pnl >= 0 ? semantic.live : semantic.error,
                  }}
                >
                  {w.cumulative_pnl >= 0 ? '+' : ''}
                  ${Math.round(w.cumulative_pnl).toLocaleString()}
                </td>
                <td className="px-2 py-2 text-right" style={{ fontFamily: 'var(--font-mono)' }}>
                  ${Math.round(w.avg_trade_size).toLocaleString()}
                </td>
                <td className="px-2 py-2">
                  <StabilityBars segments={w.segment_win_rates} pass={w.stability_pass} />
                </td>
                <td className="px-2 py-2" style={{ color: fg.tertiary, fontSize: '11px' }}>
                  {formatRelative(w.last_trade_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TierFilterButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="rounded border"
      style={{
        padding: '4px 10px',
        fontSize: '11px',
        fontFamily: 'var(--font-mono, ui-monospace)',
        backgroundColor: active ? layer['03'] : layer['02'],
        color: active ? fg.primary : fg.secondary,
        borderColor: active ? 'oklch(40% 0.012 240)' : 'oklch(28% 0.010 240)',
      }}
    >
      {label}
    </button>
  );
}

function StabilityBars({ segments, pass }: { segments: number[]; pass: boolean }) {
  const safe = Array.from({ length: 3 }, (_, i) => segments[i] ?? -1);
  return (
    <div className="flex items-center gap-1">
      {safe.map((rate, i) => {
        const hasData = rate >= 0;
        const width = hasData ? Math.max(4, Math.round(rate * 40)) : 40;
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
              height: '6px',
              backgroundColor: color,
              borderRadius: '1px',
              opacity: hasData ? 1 : 0.3,
            }}
          />
        );
      })}
      <span
        className="ml-1 text-[10px]"
        style={{ color: pass ? semantic.live : semantic.stale }}
      >
        {pass ? '✓' : '✗'}
      </span>
    </div>
  );
}

function formatRelative(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const ms = Date.now() - d.getTime();
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)}m 前`;
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)}h 前`;
  return `${Math.floor(ms / 86400_000)}d 前`;
}
