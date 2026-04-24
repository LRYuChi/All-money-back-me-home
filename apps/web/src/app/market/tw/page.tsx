'use client';

import Link from 'next/link';
import { AppShell } from '@/components/layout/AppShell';

const watchlist = [
  { symbol: '2330', name: '台積電', sector: '半導體' },
  { symbol: '2317', name: '鴻海', sector: '電子代工' },
  { symbol: '2454', name: '聯發科', sector: '半導體' },
  { symbol: '2881', name: '富邦金', sector: '金融' },
  { symbol: '0050', name: '元大台灣50', sector: 'ETF' },
];

export default function TaiwanMarketPage() {
  return (
    <AppShell pageTitle="Markets · 台股">
      <div style={{ padding: 16 }} className="space-y-4">
        <p className="text-gray-400 text-sm">台灣證券交易所上市及上櫃股票行情與技術分析</p>

        <div className="rounded-md bg-yellow-900/20 border border-yellow-800/50 p-3">
          <p className="text-yellow-400 text-sm">
            台股即時數據功能開發中 — 需要串接 Fugle API 取得即時報價。目前提供觀察清單快速入口。
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
                href={`/symbol/tw/${stock.symbol}`}
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
