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
import { CryptoEnvPanel } from '@/components/dashboard/CryptoEnvPanel';

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

  // Generate market alerts
  const alerts: { level: 'danger' | 'warning' | 'info'; msg: string }[] = [];
  // VIX spike
  if (data.macro.vix && data.macro.vix.price > 30)
    alerts.push({ level: 'danger', msg: `VIX ${data.macro.vix.price.toFixed(1)} — 市場恐慌，波動極高` });
  else if (data.macro.vix && data.macro.vix.price > 25)
    alerts.push({ level: 'warning', msg: `VIX ${data.macro.vix.price.toFixed(1)} — 波動升高，謹慎交易` });
  // Fear & Greed extreme
  if (data.macro.fear_greed && data.macro.fear_greed.value <= 20)
    alerts.push({ level: 'warning', msg: `極度恐懼 (${data.macro.fear_greed.value}) — 潛在反彈機會` });
  if (data.macro.fear_greed && data.macro.fear_greed.value >= 80)
    alerts.push({ level: 'warning', msg: `極度貪婪 (${data.macro.fear_greed.value}) — 注意回調風險` });
  // BTC big move
  const btcData = data.crypto.find(c => c.name === 'BTC');
  if (btcData && btcData.change_pct && Math.abs(btcData.change_pct) > 5)
    alerts.push({ level: 'danger', msg: `BTC ${btcData.change_pct > 0 ? '暴漲' : '暴跌'} ${btcData.change_pct.toFixed(1)}%` });
  // Confidence regime
  if (data.confidence.regime === 'HIBERNATE')
    alerts.push({ level: 'danger', msg: '信心引擎: 休眠模式 — 所有交易已暫停' });
  else if (data.confidence.regime === 'DEFENSIVE')
    alerts.push({ level: 'warning', msg: '信心引擎: 防禦模式 — 僅允許高品質訊號' });
  // Event overlay
  if (data.confidence.event_multiplier < 1)
    alerts.push({ level: 'info', msg: `事件覆蓋 ×${data.confidence.event_multiplier} — FOMC/CPI 降低風險暴露` });

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
        {/* Alert Banners */}
        {alerts.map((a, i) => (
          <div key={i} className={`rounded-lg px-4 py-2 text-sm flex items-center gap-2 ${
            a.level === 'danger' ? 'bg-red-500/15 border border-red-500/30 text-red-400' :
            a.level === 'warning' ? 'bg-yellow-500/15 border border-yellow-500/30 text-yellow-400' :
            'bg-blue-500/15 border border-blue-500/30 text-blue-400'
          }`}>
            <span>{a.level === 'danger' ? '🚨' : a.level === 'warning' ? '⚠️' : 'ℹ️'}</span>
            <span>{a.msg}</span>
          </div>
        ))}

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

        {/* Row 3: Crypto Environment + Correlations */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <CryptoEnvPanel data={(data as any).crypto_env || {}} />
          <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
            <h3 className="text-sm font-medium text-gray-400 mb-2">跨市場相關性 (30日)</h3>
            <HeatMap data={heatmapData} />
          </div>
          <div className="grid grid-cols-1 gap-1.5">
            {[
              { href: '/market/crypto', label: '₿ 加密貨幣', color: 'from-orange-600/80 to-orange-900/80' },
              { href: '/market/us', label: '🇺🇸 美股', color: 'from-emerald-600/80 to-emerald-900/80' },
              { href: '/market/tw', label: '🇹🇼 台股', color: 'from-blue-600/80 to-blue-900/80' },
              { href: '/trades', label: '💰 交易紀錄', color: 'from-purple-600/80 to-purple-900/80' },
              { href: '/backtest', label: '📊 回測', color: 'from-pink-600/80 to-pink-900/80' },
            ].map((link) => (
              <Link key={link.href} href={link.href}>
                <div className={`rounded-lg p-2 bg-gradient-to-br ${link.color} text-center text-white text-xs font-medium hover:shadow-lg hover:scale-105 transition-all border border-white/5`}>
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
