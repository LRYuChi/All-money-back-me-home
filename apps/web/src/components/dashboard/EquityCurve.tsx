'use client';

import { useCallback, useEffect, useState } from 'react';

interface EquityPoint {
  date: string;
  profit: number;
  trade_profit: number;
  pair: string;
  side: string;
}

export default function EquityCurve() {
  const [data, setData] = useState<EquityPoint[]>([]);
  const [totalProfit, setTotalProfit] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/dashboard/equity-curve');
      if (res.ok) {
        const json = await res.json();
        setData(json.curve || []);
        setTotalProfit(json.total_profit || 0);
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
        <h3 className="text-lg font-bold text-gray-100 mb-4">📈 資金曲線</h3>
        <div className="h-48 flex items-center justify-center text-gray-500">載入中...</div>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <h3 className="text-lg font-bold text-gray-100 mb-4">📈 資金曲線</h3>
        <div className="h-48 flex items-center justify-center text-gray-500">尚無交易紀錄</div>
      </div>
    );
  }

  // Calculate chart dimensions
  const maxProfit = Math.max(...data.map(d => d.profit), 0);
  const minProfit = Math.min(...data.map(d => d.profit), 0);
  const range = maxProfit - minProfit || 1;
  const width = 100;
  const height = 100;
  const padding = 5;

  // Generate SVG path
  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1 || 1)) * (width - 2 * padding);
    const y = height - padding - ((d.profit - minProfit) / range) * (height - 2 * padding);
    return `${x},${y}`;
  });

  const linePath = `M ${points.join(' L ')}`;
  const areaPath = `${linePath} L ${padding + ((data.length - 1) / (data.length - 1 || 1)) * (width - 2 * padding)},${height - padding} L ${padding},${height - padding} Z`;

  const isPositive = totalProfit >= 0;
  const color = isPositive ? '#10b981' : '#ef4444';

  // Recent trades for mini table
  const recent = data.slice(-5).reverse();

  return (
    <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-bold text-gray-100">📈 資金曲線</h3>
        <span className={`text-xl font-bold ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {totalProfit >= 0 ? '+' : ''}{totalProfit.toFixed(2)} USDT
        </span>
      </div>

      {/* SVG Chart */}
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-48" preserveAspectRatio="none">
        {/* Zero line */}
        {minProfit < 0 && maxProfit > 0 && (
          <line
            x1={padding} y1={height - padding - ((0 - minProfit) / range) * (height - 2 * padding)}
            x2={width - padding} y2={height - padding - ((0 - minProfit) / range) * (height - 2 * padding)}
            stroke="#374151" strokeWidth="0.3" strokeDasharray="2,2"
          />
        )}
        {/* Area fill */}
        <path d={areaPath} fill={color} fillOpacity="0.1" />
        {/* Line */}
        <path d={linePath} fill="none" stroke={color} strokeWidth="0.8" />
        {/* Dots for last 3 trades */}
        {data.slice(-3).map((d, i) => {
          const idx = data.length - 3 + i;
          const x = padding + (idx / (data.length - 1 || 1)) * (width - 2 * padding);
          const y = height - padding - ((d.profit - minProfit) / range) * (height - 2 * padding);
          return <circle key={i} cx={x} cy={y} r="1.2" fill={d.trade_profit > 0 ? '#10b981' : '#ef4444'} />;
        })}
      </svg>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 mt-3 text-center text-xs">
        <div>
          <div className="text-gray-500">交易數</div>
          <div className="text-gray-200 font-bold">{data.length}</div>
        </div>
        <div>
          <div className="text-gray-500">勝率</div>
          <div className="text-gray-200 font-bold">
            {data.length > 0 ? Math.round(data.filter(d => d.trade_profit > 0).length / data.length * 100) : 0}%
          </div>
        </div>
        <div>
          <div className="text-gray-500">最大回撤</div>
          <div className="text-gray-200 font-bold">
            {Math.min(...data.map(d => d.profit), 0).toFixed(1)}
          </div>
        </div>
      </div>

      {/* Recent trades */}
      {recent.length > 0 && (
        <div className="mt-4 space-y-1">
          <div className="text-xs text-gray-500 mb-1">最近交易</div>
          {recent.map((t, i) => (
            <div key={i} className="flex justify-between text-xs">
              <span className="text-gray-400">{t.pair.split('/')[0]} {t.side === 'long' ? '📈' : '📉'}</span>
              <span className={t.trade_profit > 0 ? 'text-green-400' : 'text-red-400'}>
                {t.trade_profit > 0 ? '+' : ''}{t.trade_profit.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
