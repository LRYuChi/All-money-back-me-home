'use client';

import Link from 'next/link';
import { AppShell } from '@/components/layout/AppShell';

const watchlist = [
  { symbol: 'SPY', name: 'S&P 500 ETF', sector: 'ETF' },
  { symbol: 'QQQ', name: 'Nasdaq 100 ETF', sector: 'ETF' },
  { symbol: 'AAPL', name: 'Apple', sector: 'Technology' },
  { symbol: 'NVDA', name: 'NVIDIA', sector: 'Semiconductor' },
  { symbol: 'TSLA', name: 'Tesla', sector: 'EV/Energy' },
  { symbol: 'GLD', name: 'Gold ETF', sector: 'Commodity' },
];

export default function USMarketPage() {
  return (
    <AppShell pageTitle="Markets · 美股">
      <div style={{ padding: 16 }} className="space-y-4">
        <p className="text-gray-400 text-sm">
          美國 NYSE、NASDAQ 上市股票與 ETF 行情與技術分析
        </p>

        <div className="rounded-md bg-yellow-900/20 border border-yellow-800/50 p-3">
          <p className="text-yellow-400 text-sm">
            美股即時數據功能開發中 — 目前支援 yfinance 歷史資料查詢。提供觀察清單快速入口。
          </p>
        </div>

        <div className="rounded-md bg-gray-900 border border-gray-800">
          <div className="px-5 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">觀察清單</h2>
          </div>
          <div className="divide-y divide-gray-800">
            {watchlist.map((stock) => (
              <Link
                key={stock.symbol}
                href={`/symbol/us/${stock.symbol}`}
                className="flex items-center justify-between px-5 py-3 hover:bg-gray-800/50 transition-colors"
              >
                <div>
                  <span className="text-white font-mono">{stock.symbol}</span>
                  <span className="text-gray-400 ml-3 text-sm">{stock.name}</span>
                </div>
                <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                  {stock.sector}
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
