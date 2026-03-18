'use client';

import { GaugeChart } from '@/components/charts/GaugeChart';

interface MacroData {
  vix?: { price: number; change_pct: number };
  yield_10y?: { price: number; change_pct: number };
  fear_greed?: { value: number; classification: string };
  btc_dominance?: number;
}

export function MacroPanel({ data }: { data: MacroData }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
      <h3 className="text-sm font-medium text-gray-400 mb-3">宏觀情緒</h3>
      <div className="flex justify-around">
        {data.vix && (
          <GaugeChart
            value={data.vix.price}
            min={10} max={50}
            label="VIX"
            size={100}
            thresholds={{ low: 20, high: 30 }}
          />
        )}
        {data.fear_greed && (
          <GaugeChart
            value={data.fear_greed.value}
            min={0} max={100}
            label={data.fear_greed.classification}
            size={100}
            thresholds={{ low: 30, high: 70 }}
          />
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
        {data.yield_10y && (
          <div className="flex justify-between">
            <span className="text-gray-500">10Y</span>
            <span className="text-white">{data.yield_10y.price.toFixed(2)}%</span>
          </div>
        )}
        {data.btc_dominance && (
          <div className="flex justify-between">
            <span className="text-gray-500">BTC.D</span>
            <span className="text-white">{data.btc_dominance}%</span>
          </div>
        )}
      </div>
    </div>
  );
}
