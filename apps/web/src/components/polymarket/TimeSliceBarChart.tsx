'use client';

import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * Phase A2b — 時間切片一致性柱狀圖.
 *
 * 3 段 30d 的勝率並排比較。若 segment 勝率標準差 > 閾值 → 視為不穩定。
 * 每根柱子標註：段號、日期範圍、勝率%、樣本數。
 * 中心水平線 = 整體平均勝率（mean）。
 */

export interface Segment {
  index: number;
  days_back?: [number, number];
  resolved: number;
  win_rate?: number;
}

interface TimeSliceBarChartProps {
  segments: Segment[];
  meanWinRate?: number;
  isConsistent?: boolean | null;
}

export function TimeSliceBarChart({
  segments,
  meanWinRate,
  isConsistent,
}: TimeSliceBarChartProps) {
  if (segments.length === 0) {
    return (
      <div style={{ color: fg.tertiary, fontSize: 13, padding: '12px 0' }}>
        尚無時間切片資料
      </div>
    );
  }

  const height = 160;
  const barWidth = 56;
  const gap = 24;
  const axisPadding = 20;

  return (
    <div>
      {/* Chart area */}
      <div
        style={{
          position: 'relative',
          height,
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'center',
          gap,
          paddingTop: axisPadding,
          paddingBottom: 20,
          background: layer['02'],
          borderRadius: 4,
        }}
      >
        {/* Mean line */}
        {typeof meanWinRate === 'number' && meanWinRate > 0 && (
          <div
            style={{
              position: 'absolute',
              left: 20,
              right: 20,
              top: `${axisPadding + (1 - meanWinRate) * (height - axisPadding - 20)}px`,
              borderTop: `1px dashed ${isConsistent ? semantic.live : semantic.warn}`,
              zIndex: 2,
            }}
          >
            <span
              style={{
                position: 'absolute',
                right: 0,
                top: -14,
                fontSize: 10,
                color: isConsistent ? semantic.live : semantic.warn,
                fontVariantNumeric: 'tabular-nums',
                background: layer['02'],
                padding: '0 3px',
              }}
            >
              mean {(meanWinRate * 100).toFixed(0)}%
            </span>
          </div>
        )}

        {/* Horizontal grid lines for 0/50/100 */}
        {[0, 0.5, 1].map((frac) => (
          <div
            key={frac}
            style={{
              position: 'absolute',
              left: 20,
              right: 20,
              bottom: 20 + frac * (height - axisPadding - 20),
              borderTop: `1px dotted ${borderColor.hair}`,
              zIndex: 1,
            }}
          >
            <span
              style={{
                position: 'absolute',
                left: -20,
                bottom: -6,
                fontSize: 9,
                color: fg.tertiary,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {(frac * 100).toFixed(0)}%
            </span>
          </div>
        ))}

        {segments.map((s) => (
          <SegmentBar
            key={s.index}
            segment={s}
            height={height - axisPadding - 20}
            width={barWidth}
            meanWinRate={meanWinRate}
          />
        ))}
      </div>

      {/* X-axis labels */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          gap,
          marginTop: 6,
          fontSize: 10,
          color: fg.tertiary,
        }}
      >
        {segments.map((s) => (
          <div
            key={s.index}
            style={{ width: barWidth, textAlign: 'center' }}
          >
            <div>
              {s.days_back
                ? `${s.days_back[0]}-${s.days_back[1]}d`
                : `段 ${s.index}`}
            </div>
            <div style={{ color: fg.tertiary, fontSize: 9 }}>n={s.resolved}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SegmentBar({
  segment,
  height,
  width,
  meanWinRate,
}: {
  segment: Segment;
  height: number;
  width: number;
  meanWinRate?: number;
}) {
  const hasData = typeof segment.win_rate === 'number' && segment.resolved >= 3;

  if (!hasData) {
    return (
      <div
        style={{
          width,
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: fg.tertiary,
          fontSize: 10,
          border: `1px dashed ${borderColor.hair}`,
          borderRadius: 3,
          textAlign: 'center',
        }}
      >
        樣本
        <br />
        不足
      </div>
    );
  }

  const wr = segment.win_rate!;
  const barHeight = Math.max(wr * height, 2);
  const color = _barColor(wr);
  const diff = meanWinRate !== undefined ? wr - meanWinRate : 0;
  const diffSign = diff > 0 ? '+' : diff < 0 ? '−' : '';

  return (
    <div
      style={{
        width,
        height,
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
      }}
    >
      {/* 勝率數字 */}
      <div
        style={{
          textAlign: 'center',
          fontSize: 11,
          fontVariantNumeric: 'tabular-nums',
          color: fg.primary,
          marginBottom: 2,
          position: 'absolute',
          top: -18,
          left: 0,
          right: 0,
        }}
      >
        {(wr * 100).toFixed(0)}%
      </div>

      {/* 柱狀條 */}
      <div
        style={{
          width: '100%',
          height: barHeight,
          background: color,
          borderRadius: '3px 3px 0 0',
          transition: 'height 400ms ease',
          position: 'relative',
        }}
      >
        {meanWinRate !== undefined && Math.abs(diff) >= 0.03 && (
          <span
            style={{
              position: 'absolute',
              top: 4,
              left: '50%',
              transform: 'translateX(-50%)',
              fontSize: 9,
              color: '#fff',
              textShadow: '0 1px 2px rgba(0,0,0,0.5)',
            }}
          >
            {diffSign}
            {Math.abs(diff * 100).toFixed(0)}pp
          </span>
        )}
      </div>
    </div>
  );
}

function _barColor(winRate: number): string {
  if (winRate >= 0.6) return semantic.yes;
  if (winRate >= 0.5) return semantic.warn;
  return semantic.error;
}
