'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type LineData,
  ColorType,
  CrosshairMode,
} from 'lightweight-charts';
import type { OHLCV } from '@/types/market';
import type { IndicatorData } from '@/types/analysis';

interface CandlestickChartProps {
  data: OHLCV[];
  indicators?: IndicatorData[];
  volume?: boolean;
  height?: number;
}

const INDICATOR_COLORS = [
  '#2196F3',
  '#FF9800',
  '#E91E63',
  '#4CAF50',
  '#9C27B0',
  '#00BCD4',
];

export default function CandlestickChart({
  data,
  indicators,
  volume = true,
  height = 500,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const lineSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#1a1a2e' },
        textColor: '#d1d5db',
      },
      grid: {
        vertLines: { color: '#2d2d44' },
        horzLines: { color: '#2d2d44' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: '#2d2d44',
      },
      timeScale: {
        borderColor: '#2d2d44',
        timeVisible: true,
      },
    });

    chartRef.current = chart;

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#ef5350',
      downColor: '#26a69a',
      borderDownColor: '#26a69a',
      borderUpColor: '#ef5350',
      wickDownColor: '#26a69a',
      wickUpColor: '#ef5350',
    });
    candlestickSeriesRef.current = candlestickSeries;

    if (volume) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
      });

      volumeSeries.priceScale().applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      });

      volumeSeriesRef.current = volumeSeries;
    }

    // Resize observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        chart.applyOptions({ width });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candlestickSeriesRef.current = null;
      volumeSeriesRef.current = null;
      lineSeriesRefs.current = [];
    };
  }, [height, volume]);

  // Update candlestick and volume data
  useEffect(() => {
    if (!candlestickSeriesRef.current || data.length === 0) return;

    const candlestickData: CandlestickData[] = data.map((d) => ({
      time: d.ts as CandlestickData['time'],
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    candlestickSeriesRef.current.setData(candlestickData);

    if (volumeSeriesRef.current) {
      const volumeData: HistogramData[] = data.map((d) => ({
        time: d.ts as HistogramData['time'],
        value: d.volume,
        color: d.close >= d.open ? 'rgba(239, 83, 80, 0.5)' : 'rgba(38, 166, 154, 0.5)',
      }));
      volumeSeriesRef.current.setData(volumeData);
    }
  }, [data]);

  // Update indicator overlays
  useEffect(() => {
    if (!chartRef.current || !indicators) return;

    // Remove old line series
    for (const series of lineSeriesRefs.current) {
      chartRef.current.removeSeries(series);
    }
    lineSeriesRefs.current = [];

    // Add new line series
    indicators.forEach((indicator, idx) => {
      if (!chartRef.current) return;
      const color = INDICATOR_COLORS[idx % INDICATOR_COLORS.length];
      const lineSeries = chartRef.current.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        title: indicator.name,
      });

      const lineData: LineData[] = indicator.values.map((v) => ({
        time: v.ts as LineData['time'],
        value: v.value,
      }));

      lineSeries.setData(lineData);
      lineSeriesRefs.current.push(lineSeries);
    });
  }, [indicators]);

  return (
    <div className="w-full rounded-lg overflow-hidden border border-gray-700">
      <div ref={containerRef} className="w-full" />
    </div>
  );
}
