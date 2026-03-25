'use client';

import { useCallback, useEffect, useState } from 'react';

interface Signal {
  symbol: string;
  close: number;
  st_15m: number;
  st_1h: number;
  st_1d: number;
  dir_4h: number;
  adx: number;
  trend_quality: number;
  all_bullish: boolean;
  all_bearish: boolean;
  status: string;
  adx_ok: boolean;
  quality_ok: boolean;
}

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  confirmed_long: { label: '⚡ 確認做多', color: 'text-green-300', bg: 'bg-green-900/30' },
  confirmed_short: { label: '⚡ 確認做空', color: 'text-red-300', bg: 'bg-red-900/30' },
  scout_long: { label: '🔍 試單做多', color: 'text-green-400', bg: 'bg-green-900/20' },
  scout_short: { label: '🔍 試單做空', color: 'text-red-400', bg: 'bg-red-900/20' },
  bullish: { label: '⬆️ 偏多', color: 'text-green-500', bg: '' },
  bearish: { label: '⬇️ 偏空', color: 'text-red-500', bg: '' },
  neutral: { label: '⏸ 觀望', color: 'text-gray-500', bg: '' },
};

function DirectionBadge({ value, label }: { value: number; label: string }) {
  const color = value > 0 ? 'text-green-400' : value < 0 ? 'text-red-400' : 'text-gray-500';
  const icon = value > 0 ? '▲' : value < 0 ? '▼' : '─';
  return (
    <div className="text-center">
      <div className="text-[10px] text-gray-500">{label}</div>
      <div className={`text-xs font-mono ${color}`}>{icon}</div>
    </div>
  );
}

export default function SupertrendSignals() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [timestamp, setTimestamp] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/dashboard/supertrend-signals');
      if (res.ok) {
        const json = await res.json();
        setSignals(json.signals || []);
        setTimestamp(json.timestamp || '');
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 60000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <h3 className="text-lg font-bold text-gray-100 mb-4">🎯 Supertrend 4L 信號</h3>
        <div className="h-48 flex items-center justify-center text-gray-500">載入中...</div>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-bold text-gray-100">🎯 Supertrend 4L 信號</h3>
        <span className="text-xs text-gray-500">
          {timestamp ? new Date(timestamp).toLocaleTimeString('zh-TW') : ''}
        </span>
      </div>

      <div className="space-y-2">
        {signals.map((s) => {
          const cfg = STATUS_CONFIG[s.status] || STATUS_CONFIG.neutral;
          return (
            <div
              key={s.symbol}
              className={`flex items-center justify-between p-3 rounded-lg border border-gray-800 ${cfg.bg}`}
            >
              {/* Left: Symbol + Price */}
              <div className="w-20">
                <div className="font-bold text-gray-100 text-sm">{s.symbol}</div>
                <div className="text-xs text-gray-400">${s.close.toLocaleString()}</div>
              </div>

              {/* Middle: 4-layer direction */}
              <div className="flex gap-2">
                <DirectionBadge value={s.st_1d} label="1D" />
                <DirectionBadge value={s.dir_4h} label="4H" />
                <DirectionBadge value={s.st_1h} label="1H" />
                <DirectionBadge value={s.st_15m} label="15m" />
              </div>

              {/* Quality indicators */}
              <div className="flex gap-2 text-xs">
                <span className={s.adx_ok ? 'text-green-400' : 'text-gray-600'}>
                  ADX {s.adx}
                </span>
                <span className={s.quality_ok ? 'text-green-400' : 'text-gray-600'}>
                  Q {s.trend_quality}
                </span>
              </div>

              {/* Status badge */}
              <div className={`text-xs font-medium ${cfg.color} w-24 text-right`}>
                {cfg.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-4 text-[10px] text-gray-500">
        <span>⚡ 確認 = 四層同向</span>
        <span>🔍 試單 = 三層同向</span>
        <span>ADX &gt; 25 = 趨勢強</span>
        <span>Q &gt; 0.5 = 品質足</span>
      </div>
    </div>
  );
}
