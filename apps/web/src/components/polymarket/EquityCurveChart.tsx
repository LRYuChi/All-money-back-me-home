'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  LineSeries,
  AreaSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type AreaData,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

export interface CurvePoint {
  date: string; // ISO YYYY-MM-DD
  value: number; // cumulative realized PnL (USDC)
}

export interface CurveEvent {
  date: string;
  pnl: number;
  won: boolean;
  notional: number;
  condition_id?: string;
  outcome?: string;
}

type RangeKey = '7d' | '30d' | '90d' | 'all';

interface EquityCurveChartProps {
  curve: CurvePoint[];
  events?: CurveEvent[];
  height?: number;
  showDrawdown?: boolean;
  defaultRange?: RangeKey;
}

const RANGE_DAYS: Record<RangeKey, number | null> = {
  '7d': 7,
  '30d': 30,
  '90d': 90,
  all: null,
};

/**
 * 錢包資金曲線圖 — 已實現 PnL 90 天走勢.
 *
 * 視覺層級：
 *   1. 底層線：cumulative PnL (主色 live)
 *   2. 陰影：drawdown underwater area (peak - current)
 *   3. 標記：resolved positions 贏輸點 (大小 ∝ notional)
 */
export function EquityCurveChart({
  curve,
  events = [],
  height = 320,
  showDrawdown = true,
  defaultRange = 'all',
}: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [range, setRange] = useState<RangeKey>(defaultRange);

  // Filter curve/events by selected range
  const { filteredCurve, filteredEvents } = useMemo(() => {
    const days = RANGE_DAYS[range];
    if (days === null || curve.length === 0) {
      return { filteredCurve: curve, filteredEvents: events };
    }
    const lastDate = new Date(curve[curve.length - 1].date);
    const cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffIso = cutoff.toISOString().slice(0, 10);
    return {
      filteredCurve: curve.filter((p) => p.date >= cutoffIso),
      filteredEvents: events.filter((e) => e.date >= cutoffIso),
    };
  }, [curve, events, range]);

  useEffect(() => {
    if (!containerRef.current) return;
    if (filteredCurve.length === 0) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: layer['01'] },
        textColor: fg.secondary,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      },
      grid: {
        vertLines: { color: borderColor.hair, style: LineStyle.Dotted },
        horzLines: { color: borderColor.hair, style: LineStyle.Dotted },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: borderColor.hair },
      timeScale: {
        borderColor: borderColor.hair,
        timeVisible: false,
        secondsVisible: false,
      },
      localization: {
        priceFormatter: (v: number) =>
          `${v >= 0 ? '+' : ''}$${Math.round(v).toLocaleString()}`,
      },
    });
    chartRef.current = chart;

    // 1. 主線 — cumulative PnL
    const mainSeries: ISeriesApi<'Line'> = chart.addSeries(LineSeries, {
      color: semantic.live,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
    });
    const lineData: LineData[] = filteredCurve.map((p) => ({
      time: p.date as Time,
      value: p.value,
    }));
    mainSeries.setData(lineData);

    // 2. Drawdown underwater area (peak - current, negative shading)
    if (showDrawdown) {
      let peak = filteredCurve[0]?.value ?? 0;
      const ddData: AreaData[] = filteredCurve.map((p) => {
        peak = Math.max(peak, p.value);
        const underwater = p.value - peak; // <= 0
        return { time: p.date as Time, value: underwater };
      });

      const ddSeries = chart.addSeries(AreaSeries, {
        topColor: 'rgba(220, 65, 65, 0.35)',
        bottomColor: 'rgba(220, 65, 65, 0.02)',
        lineColor: 'rgba(220, 65, 65, 0.6)',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: 'dd',
      });
      ddSeries.setData(ddData);
      chart.priceScale('dd').applyOptions({
        scaleMargins: { top: 0.75, bottom: 0 },
        visible: false,
      });
    }

    // 3. 贏輸事件標記
    if (filteredEvents.length > 0) {
      const markers: SeriesMarker<Time>[] = filteredEvents.map((e) => ({
        time: e.date as Time,
        position: e.won ? 'aboveBar' : 'belowBar',
        color: e.won ? semantic.yes : semantic.error,
        shape: e.won ? 'arrowUp' : 'arrowDown',
        size: _markerSize(e.notional),
        text: e.won
          ? `+$${Math.round(e.pnl).toLocaleString()}`
          : `$${Math.round(e.pnl).toLocaleString()}`,
      }));
      // v5: setMarkers on series
      // @ts-expect-error — setMarkers types vary across lightweight-charts minor versions
      mainSeries.setMarkers(markers);
    }

    // Fit to range
    chart.timeScale().fitContent();

    // Responsive resize
    const resize = () => {
      if (!containerRef.current || !chart) return;
      chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    resize();
    const obs = new ResizeObserver(resize);
    obs.observe(containerRef.current);

    return () => {
      obs.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [filteredCurve, filteredEvents, height, showDrawdown]);

  if (curve.length === 0) {
    return (
      <div
        style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: fg.tertiary,
          fontSize: '13px',
          border: `1px dashed ${borderColor.hair}`,
          borderRadius: 6,
        }}
      >
        尚無已結算倉位可重建曲線（需 ≥ 1 筆 resolved position）
      </div>
    );
  }

  return (
    <div>
      <RangeSelector current={range} onChange={setRange} counts={_rangeCounts(curve)} />
      <div ref={containerRef} style={{ width: '100%', height }} />
    </div>
  );
}

function RangeSelector({
  current,
  onChange,
  counts,
}: {
  current: RangeKey;
  onChange: (r: RangeKey) => void;
  counts: Record<RangeKey, number>;
}) {
  const options: Array<{ key: RangeKey; label: string }> = [
    { key: '7d', label: '7 天' },
    { key: '30d', label: '30 天' },
    { key: '90d', label: '90 天' },
    { key: 'all', label: '全部' },
  ];
  return (
    <div style={{ display: 'flex', gap: 4, marginBottom: 10, justifyContent: 'flex-end' }}>
      {options.map((opt) => {
        const active = current === opt.key;
        const hasData = counts[opt.key] >= 2;
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => hasData && onChange(opt.key)}
            disabled={!hasData}
            style={{
              fontSize: 11,
              padding: '4px 10px',
              borderRadius: 4,
              border: `1px solid ${active ? semantic.live : borderColor.hair}`,
              backgroundColor: active ? semantic.liveBg : 'transparent',
              color: active ? semantic.live : hasData ? fg.secondary : fg.tertiary,
              cursor: hasData ? 'pointer' : 'not-allowed',
              opacity: hasData ? 1 : 0.5,
              fontFamily: 'inherit',
            }}
          >
            {opt.label}
            {counts[opt.key] > 0 && (
              <span style={{ marginLeft: 4, fontSize: 10, color: fg.tertiary }}>
                ({counts[opt.key]})
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function _rangeCounts(curve: CurvePoint[]): Record<RangeKey, number> {
  if (curve.length === 0) {
    return { '7d': 0, '30d': 0, '90d': 0, all: 0 };
  }
  const lastDate = new Date(curve[curve.length - 1].date);
  const counts: Record<RangeKey, number> = { '7d': 0, '30d': 0, '90d': 0, all: curve.length };
  for (const key of ['7d', '30d', '90d'] as const) {
    const days = RANGE_DAYS[key]!;
    const cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffIso = cutoff.toISOString().slice(0, 10);
    counts[key] = curve.filter((p) => p.date >= cutoffIso).length;
  }
  return counts;
}

function _markerSize(notional: number): number {
  // 按 notional 大小決定 marker 尺寸（1-3 範圍內）
  if (notional >= 10000) return 3;
  if (notional >= 1000) return 2;
  return 1;
}
