'use client';

import { fg, layer, semantic } from '@/lib/polymarket/tokens';

interface Token {
  token_id: string;
  outcome: string;
  price: number | null;
}

interface Market {
  condition_id: string;
  question: string;
  category: string;
  end_date_iso: string | null;
  active: boolean;
  closed: boolean;
  tokens: Token[];
  trades_24h: number;
}

export function ActiveMarketsTable({ markets }: { markets: Market[] }) {
  return (
    <div
      className="rounded-md border"
      style={{ backgroundColor: layer['01'], borderColor: 'oklch(30% 0.010 240)' }}
    >
      <div className="p-4 pb-3">
        <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          活躍市場
        </div>
        <div style={{ color: fg.tertiary, fontSize: '11px', marginTop: '2px' }}>
          依 24h 成交筆數排序 · 共 {markets.length} 個
        </div>
      </div>

      <div className="overflow-x-auto">
        <table
          className="w-full text-left"
          style={{ fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}
        >
          <thead>
            <tr style={{ color: fg.tertiary, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              <th className="px-4 py-2 font-normal">類別</th>
              <th className="px-2 py-2 font-normal">市場</th>
              <th className="px-2 py-2 font-normal text-right">選項</th>
              <th className="px-2 py-2 font-normal text-right">24h 成交</th>
              <th className="px-2 py-2 font-normal">結算日</th>
            </tr>
          </thead>
          <tbody>
            {markets.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center" style={{ color: fg.tertiary }}>
                  尚無市場資料
                </td>
              </tr>
            )}
            {markets.slice(0, 20).map((m, i) => (
              <tr
                key={m.condition_id}
                style={{
                  backgroundColor: i % 2 === 0 ? layer['01'] : layer['02'],
                  borderTop: '1px solid oklch(24% 0.010 240)',
                  color: fg.primary,
                }}
              >
                <td className="px-4 py-2" style={{ color: fg.secondary, fontSize: '11px' }}>
                  {m.category || '—'}
                </td>
                <td className="px-2 py-2 max-w-[400px] truncate" title={m.question}>
                  {m.question}
                </td>
                <td className="px-2 py-2 text-right">
                  <MarketPrices tokens={m.tokens} />
                </td>
                <td
                  className="px-2 py-2 text-right"
                  style={{
                    fontFamily: 'var(--font-mono, ui-monospace)',
                    color: m.trades_24h > 10 ? semantic.live : fg.secondary,
                  }}
                >
                  {m.trades_24h}
                </td>
                <td className="px-2 py-2" style={{ color: fg.tertiary, fontSize: '11px' }}>
                  {formatEndDate(m.end_date_iso)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MarketPrices({ tokens }: { tokens: Token[] }) {
  const yes = tokens.find((t) => t.outcome === 'Yes');
  const no = tokens.find((t) => t.outcome === 'No');
  if (yes && no && tokens.length === 2) {
    return (
      <div
        className="inline-flex gap-1 items-baseline"
        style={{ fontFamily: 'var(--font-mono, ui-monospace)' }}
      >
        <span style={{ color: semantic.yes }}>{yes.price?.toFixed(2) ?? '—'}</span>
        <span style={{ color: fg.tertiary }}>/</span>
        <span style={{ color: semantic.no }}>{no.price?.toFixed(2) ?? '—'}</span>
      </div>
    );
  }
  return (
    <span style={{ color: fg.tertiary, fontSize: '11px' }}>
      多選 ({tokens.length})
    </span>
  );
}

function formatEndDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const daysLeft = (d.getTime() - Date.now()) / 86400_000;
    if (daysLeft < 0) return '已結束';
    if (daysLeft < 1) return `${Math.floor(daysLeft * 24)}h`;
    return `${Math.floor(daysLeft)}d`;
  } catch {
    return iso;
  }
}
