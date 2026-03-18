'use client';

import { Sparkline } from '@/components/charts/Sparkline';

interface CryptoItem {
  name: string;
  price?: number;
  change_pct?: number;
  rsi?: number;
  sparkline?: number[];
  error?: string;
}

interface MacroData {
  gold?: { price: number; change_pct: number };
  oil?: { price: number; change_pct: number };
  btc_dominance?: number;
}

function pct(v: number) {
  return v >= 0 ? 'text-green-400' : 'text-red-400';
}

export function MarketTable({ crypto, macro }: { crypto: CryptoItem[]; macro: MacroData }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-800 flex justify-between items-center">
        <h3 className="text-sm font-medium text-gray-400">市場概覽</h3>
        {macro.btc_dominance && (
          <span className="text-[10px] text-gray-500">BTC.D: {macro.btc_dominance}%</span>
        )}
      </div>

      {/* Header */}
      <div className="grid grid-cols-[80px_1fr_80px_80px_50px] gap-1 px-3 py-1.5 text-[10px] text-gray-600 border-b border-gray-800/50">
        <span>Symbol</span>
        <span className="text-right">Price</span>
        <span className="text-right">24h</span>
        <span className="text-center">Trend</span>
        <span className="text-right">RSI</span>
      </div>

      {/* Crypto rows */}
      {crypto.map((c) => (
        <div key={c.name} className="grid grid-cols-[80px_1fr_80px_80px_50px] gap-1 px-3 py-1.5 items-center hover:bg-gray-800/30 transition-colors text-xs border-b border-gray-800/30">
          <span className="text-white font-medium">{c.name}</span>
          {c.error ? (
            <span className="text-gray-600 col-span-4">{c.error}</span>
          ) : (
            <>
              <span className="text-white text-right font-mono">
                ${(c.price || 0).toLocaleString('en-US', { maximumFractionDigits: c.price && c.price > 100 ? 0 : 2 })}
              </span>
              <span className={`text-right font-mono ${pct(c.change_pct || 0)}`}>
                {(c.change_pct || 0) >= 0 ? '+' : ''}{(c.change_pct || 0).toFixed(2)}%
              </span>
              <div className="flex justify-center">
                {c.sparkline && <Sparkline data={c.sparkline} width={60} height={20} />}
              </div>
              <span className={`text-right font-mono ${
                (c.rsi || 50) > 70 ? 'text-red-400' : (c.rsi || 50) < 30 ? 'text-green-400' : 'text-gray-500'
              }`}>
                {c.rsi ? Math.round(c.rsi) : '—'}
              </span>
            </>
          )}
        </div>
      ))}

      {/* Macro rows */}
      {macro.gold && (
        <div className="grid grid-cols-[80px_1fr_80px_80px_50px] gap-1 px-3 py-1.5 items-center text-xs border-b border-gray-800/30">
          <span className="text-yellow-500 font-medium">Gold</span>
          <span className="text-white text-right font-mono">${macro.gold.price.toLocaleString()}</span>
          <span className={`text-right font-mono ${pct(macro.gold.change_pct)}`}>
            {macro.gold.change_pct >= 0 ? '+' : ''}{macro.gold.change_pct.toFixed(2)}%
          </span>
          <span />
          <span />
        </div>
      )}
      {macro.oil && (
        <div className="grid grid-cols-[80px_1fr_80px_80px_50px] gap-1 px-3 py-1.5 items-center text-xs">
          <span className="text-gray-400 font-medium">Oil</span>
          <span className="text-white text-right font-mono">${macro.oil.price.toFixed(2)}</span>
          <span className={`text-right font-mono ${pct(macro.oil.change_pct)}`}>
            {macro.oil.change_pct >= 0 ? '+' : ''}{macro.oil.change_pct.toFixed(2)}%
          </span>
          <span />
          <span />
        </div>
      )}
    </div>
  );
}
