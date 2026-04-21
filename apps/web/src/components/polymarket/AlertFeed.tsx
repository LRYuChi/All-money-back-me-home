'use client';

import { fg, layer, semantic } from '@/lib/polymarket/tokens';
import { TierBadge } from './TierBadge';

interface Alert {
  wallet_address: string;
  tx_hash: string;
  event_index: number;
  tier: string;
  condition_id: string;
  market_question: string;
  side: 'BUY' | 'SELL' | string;
  outcome: string;
  size: number;
  price: number;
  notional: number;
  match_time: string;
  alerted_at: string;
}

export function AlertFeed({ alerts, windowHours }: { alerts: Alert[]; windowHours: number }) {
  return (
    <div
      className="rounded-md border"
      style={{ backgroundColor: layer['01'], borderColor: 'oklch(30% 0.010 240)' }}
    >
      <div className="flex items-center justify-between p-4 pb-3">
        <div>
          <div style={{ color: fg.secondary, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            鯨魚交易推播
          </div>
          <div style={{ color: fg.tertiary, fontSize: '11px', marginTop: '2px' }}>
            過去 {windowHours}h · 共 {alerts.length} 筆
          </div>
        </div>
      </div>

      <div className="divide-y" style={{ borderColor: 'oklch(24% 0.010 240)' }}>
        {alerts.length === 0 && (
          <div className="px-4 py-8 text-center" style={{ color: fg.tertiary, fontSize: '12px' }}>
            尚無鯨魚推播（Pipeline 需識別出 A/B/C 級錢包後才會產生）
          </div>
        )}
        {alerts.slice(0, 30).map((a) => {
          const sideColor = a.side === 'BUY' ? semantic.yes : semantic.no;
          return (
            <div
              key={`${a.wallet_address}-${a.tx_hash}-${a.event_index}`}
              className="px-4 py-3 flex items-start gap-3"
              style={{ color: fg.primary }}
            >
              <TierBadge tier={a.tier} />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2 mb-1">
                  <span
                    style={{
                      color: sideColor,
                      fontFamily: 'var(--font-mono, ui-monospace)',
                      fontWeight: 600,
                      fontSize: '12px',
                    }}
                  >
                    {a.side} {a.outcome || '?'}
                  </span>
                  <span style={{ color: fg.primary, fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                    @ {a.price?.toFixed(4) ?? '?'}
                  </span>
                  <span
                    style={{
                      color: a.notional >= 10000 ? semantic.warn : fg.secondary,
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12px',
                      fontWeight: 500,
                    }}
                  >
                    ${Math.round(a.notional ?? 0).toLocaleString()}
                    {a.notional >= 10000 && ' (大額)'}
                  </span>
                </div>
                <div style={{ color: fg.secondary, fontSize: '12px' }}>{a.market_question || '(未知市場)'}</div>
                <div
                  className="mt-1 flex gap-3"
                  style={{ color: fg.tertiary, fontSize: '11px', fontFamily: 'var(--font-mono)' }}
                >
                  <span>錢包 {a.wallet_address.slice(0, 8)}…{a.wallet_address.slice(-4)}</span>
                  <span>·</span>
                  <span>{formatTime(a.match_time)}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-TW', { hour12: false });
  } catch {
    return iso;
  }
}
