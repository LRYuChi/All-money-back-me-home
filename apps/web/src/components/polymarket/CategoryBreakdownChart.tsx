'use client';

import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * Phase A2b — 類別專精橫條圖.
 *
 * 每個類別顯示:
 *   - 橫條長度 = 該類別佔總 resolved 的比例
 *   - 橫條顏色 = 勝率越高越綠
 *   - 右側數字 = 勝率%
 *   - Specialist 類別加 ⭐ 前綴
 *
 * Baseline (整體勝率) 以垂直虛線呈現供對照。
 */

export interface CategoryStat {
  trades?: number;
  resolved: number;
  win_rate?: number;
  is_specialist?: boolean;
  notional?: number;
}

interface CategoryBreakdownChartProps {
  categories: Record<string, CategoryStat>;
  baselineWinRate?: number; // 錢包整體勝率作 baseline
  maxRows?: number;
}

export function CategoryBreakdownChart({
  categories,
  baselineWinRate,
  maxRows = 8,
}: CategoryBreakdownChartProps) {
  const entries = Object.entries(categories)
    .filter(([, s]) => (s?.resolved ?? 0) >= 1)
    .sort(([, a], [, b]) => (b?.resolved ?? 0) - (a?.resolved ?? 0))
    .slice(0, maxRows);

  if (entries.length === 0) {
    return (
      <div style={{ color: fg.tertiary, fontSize: 13, padding: '12px 0' }}>
        無已結算倉位可分析
      </div>
    );
  }

  const maxResolved = Math.max(...entries.map(([, s]) => s?.resolved ?? 0));

  return (
    <div style={{ position: 'relative' }}>
      {/* Baseline marker — vertical dashed line */}
      {typeof baselineWinRate === 'number' && baselineWinRate > 0 && (
        <Baseline winRate={baselineWinRate} />
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {entries.map(([cat, s]) => (
          <CategoryRow
            key={cat}
            name={cat}
            resolved={s.resolved}
            winRate={s.win_rate ?? 0}
            isSpecialist={!!s.is_specialist}
            widthFraction={maxResolved > 0 ? (s.resolved ?? 0) / maxResolved : 0}
          />
        ))}
      </div>

      {/* Legend */}
      <div
        style={{
          marginTop: 12,
          fontSize: 10,
          color: fg.tertiary,
          display: 'flex',
          gap: 12,
          justifyContent: 'flex-end',
        }}
      >
        <span>• 橫條長度 = 該類別倉位數</span>
        {typeof baselineWinRate === 'number' && <span>• 虛線 = 整體勝率 baseline</span>}
      </div>
    </div>
  );
}

function CategoryRow({
  name,
  resolved,
  winRate,
  isSpecialist,
  widthFraction,
}: {
  name: string;
  resolved: number;
  winRate: number;
  isSpecialist: boolean;
  widthFraction: number;
}) {
  const wrPct = (winRate * 100).toFixed(0);
  const barColor = _winRateColor(winRate);

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '120px 1fr 70px',
        gap: 10,
        alignItems: 'center',
        fontSize: 12,
      }}
    >
      <span
        style={{
          color: isSpecialist ? semantic.tier : fg.primary,
          fontWeight: isSpecialist ? 500 : 400,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {isSpecialist && '⭐ '}
        {name}
      </span>

      <div
        style={{
          position: 'relative',
          height: 18,
          background: layer['02'],
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: `${Math.max(widthFraction * 100, 4)}%`,
            height: '100%',
            background: barColor,
            transition: 'width 300ms ease',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 6,
            top: '50%',
            transform: 'translateY(-50%)',
            fontSize: 10,
            color: fg.primary,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontVariantNumeric: 'tabular-nums',
            textShadow: '0 0 2px rgba(0,0,0,0.5)',
          }}
        >
          {resolved} 筆
        </div>
      </div>

      <span
        style={{
          textAlign: 'right',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
          color: _winRateColor(winRate, 'strong'),
        }}
      >
        {wrPct}%
      </span>
    </div>
  );
}

function Baseline({ winRate }: { winRate: number }) {
  // 用 CSS 畫一條垂直虛線代表 baseline（位置比例 = winRate 相對 0-100%）
  // 放在 bar 區（中間 column），由於 grid layout 固定 120/1fr/70，
  // 絕對定位相對父元素
  const leftPct = winRate * 100;
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        bottom: 32, // 留空間給 legend
        left: `calc(120px + 10px + ${leftPct}% * (100% - 120px - 70px - 20px) / 100%)`,
        width: 1,
        borderLeft: `1px dashed ${borderColor.medium}`,
        pointerEvents: 'none',
        zIndex: 2,
      }}
      aria-label={`baseline ${(winRate * 100).toFixed(0)}%`}
    />
  );
}

function _winRateColor(winRate: number, mode: 'bar' | 'strong' = 'bar'): string {
  // Gradient: red < 0.45, yellow 0.45-0.55, green > 0.55
  if (winRate >= 0.6) return mode === 'bar' ? 'rgba(72, 200, 120, 0.55)' : semantic.yes;
  if (winRate >= 0.5) return mode === 'bar' ? 'rgba(180, 180, 80, 0.50)' : semantic.warn;
  return mode === 'bar' ? 'rgba(220, 80, 80, 0.50)' : semantic.error;
}
