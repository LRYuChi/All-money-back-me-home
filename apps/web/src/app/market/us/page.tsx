import Link from 'next/link';

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
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white">美股市場</h1>
        <p className="text-gray-400 mt-1">
          美國NYSE、NASDAQ上市股票與ETF行情與技術分析
        </p>
      </div>

      <div className="rounded-xl bg-yellow-900/20 border border-yellow-800/50 p-4">
        <p className="text-yellow-400 text-sm">
          美股即時數據功能開發中 — 目前支援 yfinance 歷史資料查詢。
          提供觀察清單快速入口。
        </p>
      </div>

      <div className="rounded-xl bg-gray-900 border border-gray-800">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold text-white">觀察清單</h2>
        </div>
        <div className="divide-y divide-gray-800">
          {watchlist.map((stock) => (
            <Link
              key={stock.symbol}
              href={`/symbol/us/${stock.symbol}`}
              className="flex items-center justify-between px-6 py-4 hover:bg-gray-800/50 transition-colors"
            >
              <div>
                <span className="text-white font-medium">{stock.symbol}</span>
                <span className="text-gray-400 ml-3">{stock.name}</span>
              </div>
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                {stock.sector}
              </span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
