'use client';

import { ReactNode } from 'react';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * StatBar — Bloomberg 風格的橫向統計條。
 *
 * 橫向一條，各 cell 用細分隔線，數字大而粗、label 小而灰。
 * 適合放在頁面頂部顯示 PnL / 倉位數 / 勝率 等關鍵指標。
 */

export interface StatItem {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'up' | 'down' | 'neutral' | 'warn';
}

interface StatBarProps {
  stats: StatItem[];
  minColWidth?: number;
}

export function StatBar({ stats, minColWidth = 140 }: StatBarProps) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fit, minmax(${minColWidth}px, 1fr))`,
        backgroundColor: layer['01'],
        border: `1px solid ${borderColor.hair}`,
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      {stats.map((s, i) => (
        <StatCell key={`${s.label}-${i}`} item={s} isFirst={i === 0} />
      ))}
    </div>
  );
}

function StatCell({ item, isFirst }: { item: StatItem; isFirst: boolean }) {
  const color =
    item.tone === 'up'
      ? semantic.live
      : item.tone === 'down'
        ? semantic.error
        : item.tone === 'warn'
          ? semantic.warn
          : fg.primary;
  return (
    <div
      style={{
        padding: '10px 14px',
        borderLeft: isFirst ? 'none' : `1px solid ${borderColor.hair}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 3,
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: fg.tertiary,
          letterSpacing: 0.5,
          textTransform: 'uppercase',
        }}
      >
        {item.label}
      </div>
      <div
        style={{
          fontSize: 18,
          fontWeight: 600,
          color,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1.1,
        }}
      >
        {item.value}
      </div>
      {item.sub && (
        <div
          style={{
            fontSize: 10,
            color: fg.tertiary,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {item.sub}
        </div>
      )}
    </div>
  );
}
