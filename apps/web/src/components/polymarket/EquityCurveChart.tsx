'use client';

import { useEffect, useRef } from 'react';
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
  type UTCTimestamp,
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

interface EquityCurveChartProps {
  curve: CurvePoint[];
  events?: CurveEvent[];
  height?: number;
  showDrawdown?: boolean;
}

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
}: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    if (curve.length === 0) return;

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
    const lineData: LineData[] = curve.map((p) => ({
      time: p.date as Time,
      value: p.value,
    }));
    mainSeries.setData(lineData);

    // 2. Drawdown underwater area (peak - current, negative shading)
    if (showDrawdown) {
      let peak = curve[0]?.value ?? 0;
      const ddData: AreaData[] = curve.map((p) => {
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
    if (events.length > 0) {
      const markers: SeriesMarker<Time>[] = events.map((e) => ({
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
  }, [curve, events, height, showDrawdown]);

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

  return <div ref={containerRef} style={{ width: '100%', height }} />;
}

function _markerSize(notional: number): number {
  // 按 notional 大小決定 marker 尺寸（1-3 範圍內）
  if (notional >= 10000) return 3;
  if (notional >= 1000) return 2;
  return 1;
}
