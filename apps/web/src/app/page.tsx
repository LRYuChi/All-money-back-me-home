'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { TickerTape } from '@/components/dashboard/TickerTape';
import { ConfidencePanel } from '@/components/dashboard/ConfidencePanel';
import { MarketTable } from '@/components/dashboard/MarketTable';
import { MacroPanel } from '@/components/dashboard/MacroPanel';
import { BotStatus } from '@/components/dashboard/BotStatus';
import { HeatMap } from '@/components/charts/HeatMap';

interface DashboardData {
  timestamp: string;
  confidence: {
    score: number;
    regime: string;
    event_multiplier: number;
    sandboxes: Record<string, number>;
    guidance: { position_pct: number; leverage: number; threshold_mult: number };
  };
  crypto: { name: string; price?: number; change_pct?: number; rsi?: number; sparkline?: number[]; error?: string }[];
  trading: {
    capital: number; initial_capital: number; total_pnl: number; total_pnl_pct: number;
    open_positions: number; total_trades: number; win_rate: number;
  };
  macro: {
    vix?: { name: string; price: number; change_pct: number };
    yield_10y?: { name: string; price: number; change_pct: number };
    gold?: { name: string; price: number; change_pct: number };
    oil?: { name: string; price: number; change_pct: number };
    fear_greed?: { value: number; classification: string };
    btc_dominance?: number;
  };
  correlations: Record<string, { value: number; label: string }>;
  freqtrade: { state: string; strategy: string; dry_run?: boolean; trade_count?: number; profit?: number };
  next_killzone: { name: string; active?: boolean; starts_in_hours?: number; utc_start: string };
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const d = await apiClient.get<DashboardData>('/api/dashboard');
      setData(d);
      setLastUpdate(new Date().toLocaleTimeString('zh-TW'));
    } catch { /* keep stale */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-gray-500 animate-pulse">載入儀表板...</div>
      </div>
    );
  }
  if (!data) return null;

  // Prepare ticker tape items
  const tickerItems = [
    ...data.crypto.filter(c => c.price).map(c => ({ name: c.name, price: c.price!, change_pct: c.change_pct || 0 })),
    ...(data.macro.vix ? [{ name: 'VIX', price: data.macro.vix.price, change_pct: data.macro.vix.change_pct }] : []),
    ...(data.macro.gold ? [{ name: 'Gold', price: data.macro.gold.price, change_pct: data.macro.gold.change_pct }] : []),
    ...(data.macro.oil ? [{ name: 'Oil', price: data.macro.oil.price, change_pct: data.macro.oil.change_pct }] : []),
  ];

  // Prepare heatmap
  const heatmapData = Object.entries(data.correlations || {}).map(([asset, info]) => ({
    label: `BTC-${asset}`,
    value: info.value,
    detail: info.label,
  }));

  return (
    <div className="-mx-4 -mt-4">
      {/* Ticker Tape */}
      <TickerTape items={tickerItems} />

      <div className="px-4 py-3 space-y-3 max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-center">
          <h1 className="text-lg font-semibold text-white">Trading Dashboard</h1>
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>{lastUpdate}</span>
            <button onClick={fetchData} className="text-blue-400 hover:text-blue-300 transition-colors">↻</button>
          </div>
        </div>

        {/* Row 1: Confidence + Bot Status */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <ConfidencePanel data={data.confidence} />
          </div>
          <BotStatus bot={data.freqtrade} killzone={data.next_killzone} trading={data.trading} />
        </div>

        {/* Row 2: Market Table + Macro */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <MarketTable crypto={data.crypto} macro={data.macro} />
          </div>
          <MacroPanel data={data.macro} />
        </div>

        {/* Row 3: Correlations + Quick Links */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
            <h3 className="text-sm font-medium text-gray-400 mb-2">跨市場相關性 (30日)</h3>
            <HeatMap data={heatmapData} />
          </div>
          <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-5 gap-2">
            {[
              { href: '/market/crypto', label: '₿ 加密貨幣', color: 'from-orange-600/80 to-orange-900/80' },
              { href: '/market/us', label: '🇺🇸 美股', color: 'from-emerald-600/80 to-emerald-900/80' },
              { href: '/market/tw', label: '🇹🇼 台股', color: 'from-blue-600/80 to-blue-900/80' },
              { href: '/trades', label: '💰 交易紀錄', color: 'from-purple-600/80 to-purple-900/80' },
              { href: '/backtest', label: '📊 回測', color: 'from-pink-600/80 to-pink-900/80' },
            ].map((link) => (
              <Link key={link.href} href={link.href}>
                <div className={`rounded-lg p-2.5 bg-gradient-to-br ${link.color} text-center text-white text-xs font-medium hover:shadow-lg hover:scale-105 transition-all border border-white/5`}>
                  {link.label}
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
