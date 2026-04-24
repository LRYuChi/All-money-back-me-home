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
  // Note: 此處不再渲染任何 nav — 導覽由各頁面的 AppShell 元件負責
  // (apps/web/src/components/layout/AppShell.tsx)。這避免雙層 nav 重疊。
  return (
    <html lang="zh-TW">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
        style={{ backgroundColor: 'oklch(12% 0.005 240)', color: 'oklch(96% 0.005 240)' }}
      >
        {children}
      </body>
    </html>
  );
}
