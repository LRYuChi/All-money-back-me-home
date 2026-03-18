'use client';

import { useEffect, useState, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { apiClient } from '@/lib/api-client';
import type { OHLCV } from '@/types/market';

const CandlestickChart = dynamic(
  () => import('@/components/charts/CandlestickChart'),
  { ssr: false, loading: () => <div className="h-[500px] bg-gray-900 rounded-lg animate-pulse" /> }
);

interface AnalysisResponse {
  symbol: string;
  timeframe: string;
  structure: {
    state: string;
    choch_detected: boolean;
    choch_direction: string | null;
    confidence: number;
    swing_highs: SwingPoint[];
    swing_lows: SwingPoint[];
  };
  indicators: IndicatorSignal[];
  signals: StrategySignal[];
}

interface SwingPoint {
  index: number;
  price: number;
  ts: string;
  type: string;
}

interface IndicatorSignal {
  name: string;
  value: number | null;
  signal: string;
  strength: number;
}

interface StrategySignal {
  strategy: string;
  direction: string;
  confidence: number;
  entry_price: number;
  stop_loss: number;
  take_profit_levels: number[];
  reason_zh: string;
  indicators_used: string[];
}

const MARKET_NAMES: Record<string, string> = {
  tw: '台股',
  us: '美股',
  crypto: '加密貨幣',
};

const STATE_ZH: Record<string, string> = {
  TRENDING_UP: '上升趨勢',
  TRENDING_DOWN: '下降趨勢',
  RANGING: '區間震盪',
};

const STATE_COLOR: Record<string, string> = {
  TRENDING_UP: 'text-green-400',
  TRENDING_DOWN: 'text-red-400',
  RANGING: 'text-yellow-400',
};

const SIGNAL_ZH: Record<string, string> = {
  long: '做多',
  short: '做空',
  neutral: '中性',
};

function fmt(n: number, d = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export default function SymbolPage({
  params,
}: {
  params: { market: string; symbol: string };
}) {
  const { market, symbol: rawSymbol } = params;
  const symbol = decodeURIComponent(rawSymbol).replace('-', '/');
  const marketName = MARKET_NAMES[market] || market.toUpperCase();

  const [timeframe, setTimeframe] = useState('1h');
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<OHLCV[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (market !== 'crypto') return;

    setLoading(true);
    try {
      const res = await apiClient.post<AnalysisResponse>(
        `/api/strategy/crypto/${encodeURIComponent(symbol)}/analyze`,
        undefined,
        { params: { timeframe } }
      );
      setAnalysis(res);

      // Fetch OHLCV for chart
      const ohlcvRes = await apiClient.get<{ data: OHLCV[] }>(
        `/api/market/crypto/${encodeURIComponent(symbol)}/ohlcv`,
        { params: { timeframe } }
      ).catch(() => ({ data: [] }));
      setOhlcv(ohlcvRes.data || []);

      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '分析失敗');
    } finally {
      setLoading(false);
    }
  }, [market, symbol, timeframe]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const timeframes = ['1h', '4h', '1d'];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <span className="px-3 py-1 bg-gray-800 rounded-full text-sm text-gray-300">
            {marketName}
          </span>
          <h1 className="text-3xl font-bold text-white">{symbol.toUpperCase()}</h1>
        </div>
        <div className="flex space-x-1 bg-gray-800 rounded-lg p-1">
          {timeframes.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-3 py-1 text-sm rounded-md transition-colors ${
                timeframe === tf ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="text-center py-12 text-gray-400 animate-pulse">分析中...</div>
      )}

      {error && (
        <div className="rounded-xl bg-red-900/20 border border-red-800 p-4 text-red-400">
          {error}
        </div>
      )}

      {/* K-line Chart */}
      {ohlcv.length > 0 && (
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h2 className="text-lg font-semibold text-gray-300 mb-4">K 線圖</h2>
          <CandlestickChart data={ohlcv} volume />
        </div>
      )}

      {analysis && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Market Structure */}
          <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h2 className="text-lg font-semibold text-gray-300 mb-4">市場結構</h2>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-gray-400">趨勢狀態</span>
                <span className={`font-bold text-lg ${STATE_COLOR[analysis.structure.state] || ''}`}>
                  {STATE_ZH[analysis.structure.state] || analysis.structure.state}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-400">信心度</span>
                <div className="flex items-center space-x-2">
                  <div className="w-24 h-2 bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full"
                      style={{ width: `${analysis.structure.confidence * 100}%` }}
                    />
                  </div>
                  <span className="text-gray-300 text-sm">
                    {(analysis.structure.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
              {analysis.structure.choch_detected && (
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">CHoCH</span>
                  <span className="text-yellow-400 font-medium">
                    {analysis.structure.choch_direction === 'long' ? '轉多信號' : '轉空信號'}
                  </span>
                </div>
              )}
              <div className="flex justify-between items-center">
                <span className="text-gray-400">Swing Highs</span>
                <span className="text-gray-300">{analysis.structure.swing_highs.length} 個</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-400">Swing Lows</span>
                <span className="text-gray-300">{analysis.structure.swing_lows.length} 個</span>
              </div>
            </div>
          </div>

          {/* Indicators */}
          <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <h2 className="text-lg font-semibold text-gray-300 mb-4">技術指標</h2>
            {analysis.indicators.length === 0 ? (
              <p className="text-gray-500">無指標資料</p>
            ) : (
              <div className="space-y-2">
                {analysis.indicators.map((ind, i) => (
                  <div key={i} className="flex justify-between items-center py-1 border-b border-gray-800 last:border-0">
                    <span className="text-gray-400 text-sm">{ind.name}</span>
                    <div className="flex items-center space-x-3">
                      {ind.value != null && (
                        <span className="text-gray-300 text-sm">{fmt(ind.value)}</span>
                      )}
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                        ind.signal === 'long' ? 'bg-green-900/50 text-green-400'
                          : ind.signal === 'short' ? 'bg-red-900/50 text-red-400'
                          : 'bg-gray-700 text-gray-400'
                      }`}>
                        {SIGNAL_ZH[ind.signal] || ind.signal}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Strategy Signals */}
          <div className="bg-gray-900 rounded-xl p-6 border border-gray-800 md:col-span-2">
            <h2 className="text-lg font-semibold text-gray-300 mb-4">策略信號</h2>
            {analysis.signals.length === 0 ? (
              <p className="text-gray-500 text-center py-4">目前無策略訊號</p>
            ) : (
              <div className="space-y-4">
                {analysis.signals.map((sig, i) => (
                  <div key={i} className="bg-gray-800/50 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center space-x-3">
                        <span className={`text-sm font-bold px-3 py-1 rounded ${
                          sig.direction === 'long'
                            ? 'bg-green-900/60 text-green-400'
                            : 'bg-red-900/60 text-red-400'
                        }`}>
                          {sig.direction === 'long' ? 'LONG' : 'SHORT'}
                        </span>
                        <span className="text-gray-400 text-sm">{sig.strategy}</span>
                      </div>
                      <span className="text-gray-300">
                        信心 {(sig.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <p className="text-gray-400 text-sm mb-3">{sig.reason_zh}</p>
                    <div className="grid grid-cols-3 gap-4 text-sm">
                      <div>
                        <span className="text-gray-500 block">入場</span>
                        <span className="text-white font-medium">${fmt(sig.entry_price)}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block">停損</span>
                        <span className="text-red-400 font-medium">${fmt(sig.stop_loss)}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block">止盈</span>
                        <span className="text-green-400 font-medium">
                          {sig.take_profit_levels.length > 0
                            ? sig.take_profit_levels.map((tp) => `$${fmt(tp)}`).join(' / ')
                            : '—'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
