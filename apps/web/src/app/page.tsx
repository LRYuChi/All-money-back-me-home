'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { RadarChart } from '@/components/charts/RadarChart';
import { GaugeChart } from '@/components/charts/GaugeChart';
import { Sparkline } from '@/components/charts/Sparkline';
import { HeatMap } from '@/components/charts/HeatMap';

interface DashboardData {
  timestamp: string;
  confidence: ConfidenceData;
  crypto: CryptoItem[];
  trading: TradingData;
  macro: MacroData;
  correlations: Record<string, { value: number; label: string }>;
  freqtrade: FreqtradeData;
  next_killzone: KillzoneData;
}

interface ConfidenceData {
  score: number;
  regime: string;
  event_multiplier: number;
  sandboxes: Record<string, number>;
  guidance: { position_pct: number; leverage: number; threshold_mult: number };
}

interface CryptoItem {
  name: string;
  price?: number;
  change_pct?: number;
  rsi?: number;
  sparkline?: number[];
  error?: string;
}

interface TradingData {
  capital: number;
  initial_capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  open_positions: number;
  total_trades: number;
  win_rate: number;
}

interface MacroData {
  vix?: { name: string; price: number; change_pct: number };
  yield_10y?: { name: string; price: number; change_pct: number };
  gold?: { name: string; price: number; change_pct: number };
  oil?: { name: string; price: number; change_pct: number };
  fear_greed?: { value: number; classification: string };
  btc_dominance?: number;
}

interface FreqtradeData {
  state: string;
  strategy: string;
  dry_run?: boolean;
  trade_count?: number;
  profit?: number;
}

interface KillzoneData {
  name: string;
  active?: boolean;
  starts_in_hours?: number;
  utc_start: string;
}

const REGIME_ZH: Record<string, { label: string; color: string; emoji: string }> = {
  AGGRESSIVE: { label: '積極', color: 'text-green-400', emoji: '🔥' },
  NORMAL: { label: '正常', color: 'text-blue-400', emoji: '✅' },
  CAUTIOUS: { label: '謹慎', color: 'text-yellow-400', emoji: '⚠️' },
  DEFENSIVE: { label: '防禦', color: 'text-orange-400', emoji: '🛡️' },
  HIBERNATE: { label: '休眠', color: 'text-red-400', emoji: '❄️' },
};

function pctColor(v: number): string {
  return v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-400';
}

function fmt(n: number, d = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function ProgressBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.min(value / max * 100, 100);
  const color = pct > 60 ? 'bg-green-500' : pct > 40 ? 'bg-yellow-500' : pct > 20 ? 'bg-orange-500' : 'bg-red-500';
  return (
    <div className="w-full bg-gray-700 rounded-full h-3">
      <div className={`${color} h-3 rounded-full transition-all`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<string>('');

  const fetchData = useCallback(async () => {
    try {
      const d = await apiClient.get<DashboardData>('/api/dashboard');
      setData(d);
      setLastUpdate(new Date().toLocaleTimeString('zh-TW'));
    } catch {
      // Keep stale data
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000); // 5 min
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-gray-400 text-xl">載入儀表板...</div>
      </div>
    );
  }

  if (!data) return null;

  const regime = REGIME_ZH[data.confidence.regime] || REGIME_ZH.CAUTIOUS;
  const g = data.confidence.guidance || { position_pct: 0, leverage: 0 };

  // Prepare radar data
  const radarData = Object.entries(data.confidence.sandboxes || {}).map(([k, v]) => ({
    label: { macro: '宏觀', sentiment: '情緒', capital: '資金', haven: '避險' }[k] || k,
    value: v,
  }));

  // Prepare correlation heatmap data
  const heatmapData = Object.entries(data.correlations || {}).map(([asset, info]) => ({
    label: `BTC-${asset}`,
    value: info.value,
    detail: info.label,
  }));

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-white">交易決策儀表板</h1>
        <div className="text-sm text-gray-500">
          上次更新: {lastUpdate}
          <button onClick={fetchData} className="ml-3 text-blue-400 hover:text-blue-300">↻ 刷新</button>
        </div>
      </div>

      {/* Row 1: Confidence + Freqtrade */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Confidence Engine */}
        <div className="md:col-span-2 bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="flex justify-between items-start mb-3">
            <h2 className="text-lg font-semibold text-white">🎯 信心引擎</h2>
            <div className={`text-xl font-bold ${regime.color}`}>
              {regime.emoji} {regime.label} {fmt(data.confidence.score)}
            </div>
          </div>
          <ProgressBar value={data.confidence.score} />
          <div className="flex items-center gap-4 mt-3">
            <div className="flex-shrink-0">
              <RadarChart data={radarData} size={160} />
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm flex-1">
              {Object.entries(data.confidence.sandboxes || {}).map(([k, v]) => {
                const names: Record<string, string> = {
                  macro: '宏觀', sentiment: '情緒', capital: '資金', haven: '避險',
                };
                return (
                  <div key={k} className="flex justify-between">
                    <span className="text-gray-500">{names[k] || k}</span>
                    <span className={pctColor(v - 0.5)}>{fmt(v)}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="mt-3 text-sm text-gray-400 flex gap-4">
            <span>建議倉位: <strong className="text-white">{g.position_pct}%</strong></span>
            <span>槓桿: <strong className="text-white">{g.leverage}x</strong></span>
            {data.confidence.event_multiplier < 1 && (
              <span className="text-yellow-400">
                ⚠️ 事件覆蓋 ×{data.confidence.event_multiplier}
              </span>
            )}
          </div>
        </div>

        {/* Freqtrade Status */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-lg font-semibold text-white mb-3">🤖 交易機器人</h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-400">狀態</span>
              <span className={data.freqtrade.state === 'running' ? 'text-green-400' : 'text-red-400'}>
                ● {data.freqtrade.state === 'running' ? '運行中' : '已停止'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">策略</span>
              <span className="text-white">{data.freqtrade.strategy}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">模式</span>
              <span className="text-yellow-400">{data.freqtrade.dry_run ? '模擬' : '實盤'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">交易數</span>
              <span className="text-white">{data.freqtrade.trade_count || 0}</span>
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-gray-700 text-sm">
            <div className="text-gray-400">下次 Killzone</div>
            <div className="text-white">
              {data.next_killzone.active ? (
                <span className="text-green-400">🟢 {data.next_killzone.name} (進行中)</span>
              ) : (
                <span>{data.next_killzone.name} (UTC {data.next_killzone.utc_start}, {data.next_killzone.starts_in_hours}h 後)</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Row 2: Crypto Overview + Trading */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Crypto Market */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="flex justify-between items-center mb-3">
            <h2 className="text-lg font-semibold text-white">📊 加密貨幣</h2>
            <Link href="/market/crypto" className="text-sm text-blue-400 hover:text-blue-300">詳細 →</Link>
          </div>
          <div className="space-y-3">
            {data.crypto.map((c) => (
              <div key={c.name} className="flex items-center gap-3">
                <span className="text-white font-medium w-12">{c.name}</span>
                {c.error ? (
                  <span className="text-gray-500 text-sm">{c.error}</span>
                ) : (
                  <>
                    <span className="text-white flex-shrink-0">${fmt(c.price || 0)}</span>
                    <span className={`text-sm flex-shrink-0 ${pctColor(c.change_pct || 0)}`}>
                      {(c.change_pct || 0) > 0 ? '+' : ''}{fmt(c.change_pct || 0)}%
                    </span>
                    {c.sparkline && <Sparkline data={c.sparkline} width={80} height={24} />}
                    <span className={`text-xs flex-shrink-0 ${(c.rsi || 50) > 70 ? 'text-red-400' : (c.rsi || 50) < 30 ? 'text-green-400' : 'text-gray-400'}`}>
                      RSI:{Math.round(c.rsi || 0)}
                    </span>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Trading Status */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <div className="flex justify-between items-center mb-3">
            <h2 className="text-lg font-semibold text-white">💰 模擬交易</h2>
            <Link href="/trades" className="text-sm text-blue-400 hover:text-blue-300">詳細 →</Link>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-gray-400 text-sm">資金</div>
              <div className="text-white text-xl font-bold">${fmt(data.trading.capital)}</div>
            </div>
            <div>
              <div className="text-gray-400 text-sm">損益</div>
              <div className={`text-xl font-bold ${pctColor(data.trading.total_pnl)}`}>
                ${fmt(data.trading.total_pnl)} ({fmt(data.trading.total_pnl_pct)}%)
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-sm">持倉</div>
              <div className="text-white text-lg">{data.trading.open_positions} 筆</div>
            </div>
            <div>
              <div className="text-gray-400 text-sm">交易 / 勝率</div>
              <div className="text-white text-lg">
                {data.trading.total_trades} / {data.trading.win_rate > 0 ? `${fmt(data.trading.win_rate, 1)}%` : '—'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Row 3: Macro + Correlations */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Macro Indicators */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-lg font-semibold text-white mb-3">🌍 宏觀指標</h2>
          {/* Gauge charts row */}
          <div className="flex justify-around mb-3">
            {data.macro.vix && (
              <GaugeChart
                value={data.macro.vix.price}
                min={10} max={50}
                label="VIX 波動率"
                size={110}
                thresholds={{ low: 20, high: 30 }}
              />
            )}
            {data.macro.fear_greed && (
              <GaugeChart
                value={data.macro.fear_greed.value}
                min={0} max={100}
                label={`恐懼貪婪 (${data.macro.fear_greed.classification})`}
                size={110}
                thresholds={{ low: 30, high: 70 }}
              />
            )}
          </div>
          {/* Text indicators */}
          <div className="grid grid-cols-2 gap-2 text-sm">
            {data.macro.yield_10y && (
              <div className="flex justify-between">
                <span className="text-gray-400">10Y殖利率</span>
                <span className="text-white">{fmt(data.macro.yield_10y.price)}%</span>
              </div>
            )}
            {data.macro.gold && (
              <div className="flex justify-between">
                <span className="text-gray-400">黃金</span>
                <span className={pctColor(data.macro.gold.change_pct)}>
                  ${fmt(data.macro.gold.price, 0)}
                </span>
              </div>
            )}
            {data.macro.oil && (
              <div className="flex justify-between">
                <span className="text-gray-400">原油</span>
                <span className={pctColor(data.macro.oil.change_pct)}>
                  ${fmt(data.macro.oil.price)}
                </span>
              </div>
            )}
            {data.macro.btc_dominance && (
              <div className="flex justify-between">
                <span className="text-gray-400">BTC.D</span>
                <span className="text-white">{data.macro.btc_dominance}%</span>
              </div>
            )}
          </div>
        </div>

        {/* Cross-Market Correlations */}
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h2 className="text-lg font-semibold text-white mb-3">📈 跨市場相關性 (30日)</h2>
          <HeatMap data={heatmapData} />
        </div>
      </div>

      {/* Quick Links */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { href: '/market/crypto', label: '₿ 加密貨幣', color: 'from-orange-600 to-orange-800' },
          { href: '/market/us', label: '🇺🇸 美股', color: 'from-emerald-600 to-emerald-800' },
          { href: '/market/tw', label: '🇹🇼 台股', color: 'from-blue-600 to-blue-800' },
          { href: '/trades', label: '💰 模擬交易', color: 'from-purple-600 to-purple-800' },
          { href: '/backtest', label: '📊 策略回測', color: 'from-pink-600 to-pink-800' },
        ].map((link) => (
          <Link key={link.href} href={link.href}>
            <div className={`rounded-lg p-3 bg-gradient-to-br ${link.color} text-center text-white text-sm font-medium hover:shadow-lg transition-all`}>
              {link.label}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
