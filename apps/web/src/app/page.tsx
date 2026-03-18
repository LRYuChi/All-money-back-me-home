import Link from 'next/link';

const markets = [
  {
    key: 'tw',
    nameZh: '台股',
    description: '台灣證券交易所上市及上櫃股票',
    icon: '\u{1F1F9}\u{1F1FC}',
    color: 'from-blue-600 to-blue-800',
  },
  {
    key: 'us',
    nameZh: '美股',
    description: '美國NYSE、NASDAQ上市股票與ETF',
    icon: '\u{1F1FA}\u{1F1F8}',
    color: 'from-emerald-600 to-emerald-800',
  },
  {
    key: 'crypto',
    nameZh: '加密貨幣',
    description: '主流加密貨幣即時行情與技術分析',
    icon: '\u{20BF}',
    color: 'from-orange-600 to-orange-800',
  },
];

const tools = [
  {
    key: 'trades',
    nameZh: '模擬交易',
    description: 'Paper Trading 績效追蹤與持倉管理',
    color: 'from-purple-600 to-purple-800',
  },
  {
    key: 'backtest',
    nameZh: '策略回測',
    description: '回測歷史績效、Walk-Forward 驗證',
    color: 'from-pink-600 to-pink-800',
  },
];

export default function HomePage() {
  return (
    <div className="space-y-12">
      <section className="text-center py-16">
        <h1 className="text-5xl font-bold text-white mb-4">
          All Money Back Me Home
        </h1>
        <p className="text-2xl text-gray-400">
          交易策略輔助顧問系統
        </p>
        <p className="mt-4 text-gray-500 max-w-2xl mx-auto">
          整合多市場數據、技術指標分析與形態辨識，提供全方位的交易策略建議。
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {markets.map((market) => (
          <Link
            key={market.key}
            href={`/market/${market.key}`}
            className="group block"
          >
            <div
              className={`
                rounded-xl p-8 bg-gradient-to-br ${market.color}
                shadow-lg hover:shadow-2xl
                transform hover:-translate-y-1 transition-all duration-200
                border border-white/10
              `}
            >
              <div className="text-4xl mb-4">{market.icon}</div>
              <h2 className="text-2xl font-bold text-white mb-2">
                {market.nameZh}
              </h2>
              <p className="text-white/80 text-sm">
                {market.description}
              </p>
              <div className="mt-6 text-white/60 text-sm group-hover:text-white/90 transition-colors">
                進入市場 &rarr;
              </div>
            </div>
          </Link>
        ))}
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {tools.map((tool) => (
          <Link key={tool.key} href={`/${tool.key}`} className="group block">
            <div
              className={`
                rounded-xl p-6 bg-gradient-to-br ${tool.color}
                shadow-lg hover:shadow-2xl
                transform hover:-translate-y-1 transition-all duration-200
                border border-white/10
              `}
            >
              <h2 className="text-xl font-bold text-white mb-1">{tool.nameZh}</h2>
              <p className="text-white/80 text-sm">{tool.description}</p>
            </div>
          </Link>
        ))}
      </section>
    </div>
  );
}
