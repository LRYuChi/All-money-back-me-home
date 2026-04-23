'use client';

import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';
import { TierBadge } from './TierBadge';
import { ConsistencyTag, SpecialistTag } from './SpecialistTag';
import { parseServerDateStr } from './FreshnessIndicator';

interface Alert {
  wallet_address: string;
  tx_hash: string;
  event_index: number;
  tier: string;
  condition_id: string;
  market_question: string;
  market_category?: string;
  side: 'BUY' | 'SELL' | string;
  outcome: string;
  size: number;
  price: number;
  notional: number;
  match_time: string;
  alerted_at: string;
  // 1.5b additions
  specialist_categories?: string[];
  primary_category?: string | null;
  match_specialist?: boolean | null;
  is_consistent?: boolean | null;
}

export function AlertFeed({ alerts, windowHours }: { alerts: Alert[]; windowHours: number }) {
  return (
    <Card>
      <CardHeader
        eyebrow="鯨魚交易推播"
        subtitle={`過去 ${windowHours} 小時 · 共 ${alerts.length} 筆`}
        divider
      />
      {alerts.length === 0 && <EmptyAlertState />}
      {alerts.length > 0 && (
        <ul style={{ listStyle: 'none' }}>
          {alerts.slice(0, 30).map((a, i) => (
            <AlertRow key={`${a.wallet_address}-${a.tx_hash}-${a.event_index}`} alert={a} first={i === 0} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function EmptyAlertState() {
  return (
    <div
      className="text-center"
      style={{
        padding: '40px 20px',
        color: fg.tertiary,
      }}
    >
      <div style={{ fontSize: '13px', marginBottom: '4px' }}>尚無推播</div>
      <div style={{ fontSize: '11px', opacity: 0.7, maxWidth: '420px', margin: '0 auto' }}>
        Pipeline 需識別出 A/B/C 級鯨魚才會產生推播。當前仍在累積資料。
      </div>
    </div>
  );
}

function AlertRow({ alert, first }: { alert: Alert; first: boolean }) {
  const sideColor = alert.side === 'BUY' ? semantic.yes : semantic.no;
  const big = alert.notional >= 10000;
  const matchTime = parseServerDateStr(alert.match_time);
  const specialists = alert.specialist_categories ?? [];

  return (
    <li
      className="flex items-start gap-3"
      style={{
        padding: '14px 20px',
        borderTop: first ? undefined : `1px solid ${borderColor.hair}`,
        color: fg.primary,
      }}
    >
      <TierBadge tier={alert.tier} size="md" />

      <div className="flex-1 min-w-0">
        {/* 行 1: 方向 + 價格 + 金額 + 大額/specialist tag */}
        <div className="flex items-baseline gap-2 flex-wrap mb-1">
          <span
            style={{
              color: sideColor,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontWeight: 600,
              fontSize: '13px',
            }}
          >
            {alert.side} {alert.outcome || '?'}
          </span>
          <span
            style={{
              color: fg.secondary,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: '12px',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            @ {alert.price?.toFixed(4) ?? '?'}
          </span>
          <span
            style={{
              color: big ? semantic.warn : fg.primary,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: '13px',
              fontWeight: 500,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            ${Math.round(alert.notional ?? 0).toLocaleString()}
          </span>
          {big && (
            <span
              style={{
                padding: '1px 6px',
                borderRadius: '9999px',
                fontSize: '10px',
                color: semantic.warn,
                backgroundColor: layer['02'],
                border: `1px solid color-mix(in oklab, ${semantic.warn} 40%, transparent)`,
              }}
            >
              大額
            </span>
          )}
          {specialists.length > 0 && (
            <SpecialistTag
              specialists={specialists}
              matched={alert.match_specialist ?? null}
              size="xs"
            />
          )}
          {alert.is_consistent !== undefined && alert.is_consistent !== null && (
            <ConsistencyTag isConsistent={alert.is_consistent} size="xs" />
          )}
        </div>

        {/* 行 2: 市場問題 + 類別 chip */}
        <div className="flex items-center gap-2">
          {alert.market_category && (
            <span
              style={{
                color: fg.tertiary,
                fontSize: '11px',
                padding: '1px 6px',
                borderRadius: '9999px',
                border: `1px solid ${borderColor.hair}`,
                whiteSpace: 'nowrap',
              }}
            >
              {alert.market_category}
            </span>
          )}
          <span
            style={{
              color: fg.secondary,
              fontSize: '12px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
              flex: 1,
            }}
          >
            {alert.market_question || '(未知市場)'}
          </span>
        </div>

        {/* 行 3: 錢包 + 時間 */}
        <div
          className="mt-1 flex gap-3 flex-wrap"
          style={{
            color: fg.tertiary,
            fontSize: '11px',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          }}
        >
          <span>
            {alert.wallet_address.slice(0, 8)}…{alert.wallet_address.slice(-4)}
          </span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>{matchTime ? matchTime.toLocaleString('zh-TW', { hour12: false }) : alert.match_time}</span>
        </div>
      </div>
    </li>
  );
}
