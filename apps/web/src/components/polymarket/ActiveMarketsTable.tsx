'use client';

import { borderColor, fg, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';

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
    <Card>
      <CardHeader
        eyebrow="活躍市場"
        subtitle={`依 24 小時成交筆數排序 · 共 ${markets.length} 個`}
        divider
      />

      <div className="overflow-x-auto">
        <table
          className="w-full text-left"
          style={{
            fontSize: '12px',
            fontVariantNumeric: 'tabular-nums',
            borderCollapse: 'separate',
            borderSpacing: 0,
          }}
        >
          <thead>
            <tr style={{ color: fg.tertiary, fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              <Th first>類別</Th>
              <Th>市場</Th>
              <Th right>選項 / 價格</Th>
              <Th right>24h 成交</Th>
              <Th>結算日</Th>
            </tr>
          </thead>
          <tbody>
            {markets.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  style={{ padding: '32px 20px', textAlign: 'center', color: fg.tertiary, fontSize: '12px' }}
                >
                  尚無市場資料
                </td>
              </tr>
            )}
            {markets.slice(0, 20).map((m) => (
              <tr key={m.condition_id} style={{ color: fg.primary }}>
                <Td first>
                  <span
                    style={{
                      color: fg.secondary,
                      fontSize: '11px',
                      padding: '2px 8px',
                      borderRadius: '9999px',
                      border: `1px solid ${borderColor.hair}`,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {m.category || '—'}
                  </span>
                </Td>
                <Td style={{ maxWidth: '380px' }}>
                  <div
                    title={m.question}
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {m.question}
                  </div>
                </Td>
                <Td right>
                  <MarketPrices tokens={m.tokens} />
                </Td>
                <Td
                  right
                  mono
                  style={{
                    color: m.trades_24h > 10 ? semantic.live : fg.secondary,
                    fontWeight: m.trades_24h > 10 ? 500 : 400,
                  }}
                >
                  {m.trades_24h}
                </Td>
                <Td style={{ color: fg.tertiary, fontSize: '11px' }}>{formatEndDate(m.end_date_iso)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Th({ children, right, first }: { children: React.ReactNode; right?: boolean; first?: boolean }) {
  return (
    <th
      style={{
        padding: '10px 12px',
        paddingLeft: first ? '20px' : '12px',
        fontWeight: 500,
        textAlign: right ? 'right' : 'left',
        borderBottom: `1px solid ${borderColor.hair}`,
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  mono,
  first,
  style,
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  first?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td
      style={{
        padding: '10px 12px',
        paddingLeft: first ? '20px' : '12px',
        borderBottom: `1px solid ${borderColor.hair}`,
        textAlign: right ? 'right' : 'left',
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
        ...style,
      }}
    >
      {children}
    </td>
  );
}

function MarketPrices({ tokens }: { tokens: Token[] }) {
  const yes = tokens.find((t) => t.outcome === 'Yes');
  const no = tokens.find((t) => t.outcome === 'No');
  if (yes && no && tokens.length === 2) {
    return (
      <span
        style={{
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{ color: semantic.yes }}>{yes.price != null ? yes.price.toFixed(2) : '—'}</span>
        <span style={{ color: fg.tertiary, margin: '0 4px' }}>/</span>
        <span style={{ color: semantic.no }}>{no.price != null ? no.price.toFixed(2) : '—'}</span>
      </span>
    );
  }
  return (
    <span style={{ color: fg.tertiary, fontSize: '11px' }}>
      多選 · {tokens.length} 項
    </span>
  );
}

function formatEndDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const daysLeft = (d.getTime() - Date.now()) / 86400_000;
    if (daysLeft < 0) return '已結束';
    if (daysLeft < 1) return `${Math.max(0, Math.floor(daysLeft * 24))} 小時後`;
    if (daysLeft < 30) return `${Math.floor(daysLeft)} 天後`;
    return d.toLocaleDateString('zh-TW');
  } catch {
    return iso;
  }
}
