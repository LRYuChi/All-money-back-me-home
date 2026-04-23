import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';

const geistSans = localFont({
  src: './fonts/GeistVF.woff',
  variable: '--font-geist-sans',
  weight: '100 900',
});
const geistMono = localFont({
  src: './fonts/GeistMonoVF.woff',
  variable: '--font-geist-mono',
  weight: '100 900',
});

export const metadata: Metadata = {
  title: 'All Money Back Me Home - 交易策略輔助顧問系統',
  description: '多市場交易策略輔助顧問系統，支援台股、美股、加密貨幣的技術分析與策略建議。',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-TW">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-gray-950 text-gray-100 min-h-screen`}
      >
        <nav className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16">
              <a href="/" className="text-xl font-bold text-white font-[family-name:var(--font-geist-sans)]">
                All Money Back Me Home
              </a>
              <div className="flex items-center space-x-5 font-[family-name:var(--font-geist-sans)] text-sm">
                <a href="/market/tw" className="text-gray-400 hover:text-white transition-colors">
                  台股
                </a>
                <a href="/market/us" className="text-gray-400 hover:text-white transition-colors">
                  美股
                </a>
                <a href="/market/crypto" className="text-gray-400 hover:text-white transition-colors">
                  加密貨幣
                </a>
                <a href="/trades" className="text-gray-400 hover:text-white transition-colors">
                  交易紀錄
                </a>
                <a href="/backtest" className="text-gray-400 hover:text-white transition-colors">
                  回測
                </a>
                <a href="/polymarket" className="inline-flex items-center gap-1.5 text-gray-300 hover:text-white transition-colors">
                  <span>Polymarket</span>
                  <span
                    className="rounded-full px-1.5 py-0.5 text-[10px] font-mono"
                    style={{
                      color: 'oklch(70% 0.18 290)',
                      backgroundColor: 'oklch(25% 0.08 290)',
                      border: '1px solid color-mix(in oklab, oklch(70% 0.18 290) 35%, transparent)',
                    }}
                  >
                    1.5b
                  </span>
                </a>
              </div>
            </div>
          </div>
        </nav>
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
