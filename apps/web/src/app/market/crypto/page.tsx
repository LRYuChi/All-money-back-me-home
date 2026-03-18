'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';

interface MarketInfo {
  symbol: string;
  displayName: string;
  structure?: StructureData;
  signal?: SignalData;
  loading: boolean;
  error?: string;
}

interface StructureData {
  state: string;
  choch_detected: boolean;
  choch_direction: string | null;
  confidence: number;
}

interface SignalData {
  strategy: string;
  direction: string;
  confidence: number;
  entry_price: number;
  stop_loss: number;
  reason_zh: string;
}

interface AnalysisResponse {
  symbol: string;
  timeframe: string;
  structure: StructureData;
  signals: SignalData[];
}

const SYMBOLS = [
  { raw: 'BTCUSDT', display: 'BTC/USDT' },
  { raw: 'ETHUSDT', display: 'ETH/USDT' },
  { raw: 'SOLUSDT', display: 'SOL/USDT' },
  { raw: 'BNBUSDT', display: 'BNB/USDT' },
];

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

function fmt(n: number, d = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export default function CryptoMarketPage() {
  const [markets, setMarkets] = useState<MarketInfo[]>(
    SYMBOLS.map((s) => ({ symbol: s.raw, displayName: s.display, loading: true }))
  );
  const [timeframe, setTimeframe] = useState('1h');

  const fetchAnalysis = useCallback(
    async (sym: string, idx: number) => {
      try {
        const res = await apiClient.post<AnalysisResponse>(
          `/api/strategy/crypto/${encodeURIComponent(sym)}/analyze`,
          undefined,
          { params: { timeframe } }
        );
        setMarkets((prev) => {
          const next = [...prev];
          next[idx] = {
            ...next[idx],
            structure: res.structure,
            signal: res.signals?.[0] || undefined,
            loading: false,
            error: undefined,
          };
          return next;
        });
      } catch (err) {
        setMarkets((prev) => {
          const next = [...prev];
          next[idx] = {
            ...next[idx],
            loading: false,
            error: err instanceof Error ? err.message : '載入失敗',
          };
          return next;
        });
      }
    },
    [timeframe]
  );

  useEffect(() => {
    setMarkets((prev) => prev.map((m) => ({ ...m, loading: true })));
    SYMBOLS.forEach((s, i) => fetchAnalysis(s.raw, i));
  }, [fetchAnalysis]);

  const timeframes = ['1h', '4h', '1d'];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">加密貨幣市場</h1>
          <p className="text-gray-400 mt-1">即時市場結構 & 策略信號</p>
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

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {markets.map((m) => (
          <Link
            key={m.symbol}
            href={`/symbol/crypto/${encodeURIComponent(m.symbol.replace('/', '-'))}`}
            className="block group"
          >
            <div className="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-gray-600 transition-colors">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-bold text-white">{m.displayName}</h2>
                {m.loading && (
                  <span className="text-xs text-gray-500 animate-pulse">分析中...</span>
                )}
              </div>

              {m.error && (
                <p className="text-red-400 text-sm">{m.error}</p>
              )}

              {m.structure && (
                <div className="space-y-3">
                  {/* Market State */}
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">市場結構</span>
                    <div className="flex items-center space-x-2">
                      <span className={`font-medium ${STATE_COLOR[m.structure.state] || 'text-gray-300'}`}>
                        {STATE_ZH[m.structure.state] || m.structure.state}
                      </span>
                      <span className="text-xs text-gray-500">
                        {(m.structure.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>

                  {/* CHoCH */}
                  {m.structure.choch_detected && (
                    <div className="flex items-center justify-between">
                      <span className="text-gray-400 text-sm">CHoCH</span>
                      <span className="text-yellow-400 text-sm font-medium">
                        {m.structure.choch_direction === 'long' ? '轉多' : '轉空'}
                      </span>
                    </div>
                  )}

                  {/* Signal */}
                  {m.signal ? (
                    <div className="mt-3 pt-3 border-t border-gray-800">
                      <div className="flex items-center justify-between">
                        <span className={`text-sm font-medium px-2 py-0.5 rounded ${
                          m.signal.direction === 'long'
                            ? 'bg-green-900/50 text-green-400'
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {m.signal.direction === 'long' ? 'LONG' : 'SHORT'}
                        </span>
                        <span className="text-xs text-gray-400">
                          信心 {(m.signal.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mt-2">{m.signal.reason_zh}</p>
                      <div className="flex justify-between text-xs text-gray-500 mt-1">
                        <span>入場 ${fmt(m.signal.entry_price)}</span>
                        <span>停損 ${fmt(m.signal.stop_loss)}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="mt-3 pt-3 border-t border-gray-800">
                      <span className="text-xs text-gray-500">無策略訊號</span>
                    </div>
                  )}
                </div>
              )}

              {!m.structure && !m.loading && !m.error && (
                <p className="text-gray-500 text-sm">無資料</p>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
