'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { FreshnessIndicator, parseServerDateStr } from '@/components/polymarket/FreshnessIndicator';

/**
 * SystemHealthBar — 主頁頂部，並排顯示兩個系統的健康狀態.
 *
 * 設計目的：讓使用者一打開首頁就知道「兩個並行系統各自的狀態」，
 * 並提供 1-click 進入詳情頁的捷徑。
 */

interface CryptoHealthProps {
  botState?: string;
  openPositions?: number;
  totalPnlPct?: number;
  totalTrades?: number;
}

interface PolymarketStatus {
  last_run_end: string | null;
  duration_seconds: number | null;
  result: 'ok' | 'fail' | 'never_run' | null;
  mode: string | null;
}

interface PolymarketOverview {
  tier_distribution: Record<string, number>;
  totals: { active_markets: number; whales: number };
  activity_24h: { trades: number; alerts: number };
}

export function SystemHealthBar({
  crypto,
}: {
  crypto: CryptoHealthProps;
}) {
  return (
    <section
      className="grid grid-cols-1 md:grid-cols-2 gap-3"
      style={{ marginBottom: '12px' }}
    >
      <CryptoCard {...crypto} />
      <PolymarketCard />
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 加密交易（來自父層的 dashboard 資料）
// ─────────────────────────────────────────────────────────────────────
function CryptoCard({ botState, openPositions, totalPnlPct, totalTrades }: CryptoHealthProps) {
  const running = (botState || '').toUpperCase() === 'RUNNING';
  const accent = running ? semantic.live : semantic.warn;
  const pnlColor =
    totalPnlPct == null ? fg.tertiary : totalPnlPct >= 0 ? semantic.live : semantic.error;

  return (
    <Link href="/trades" className="block group">
      <article
        className="rounded-lg overflow-hidden h-full transition-colors group-hover:border-[color:var(--hover)]"
        style={
          {
            backgroundColor: layer['01'],
            border: `1px solid ${borderColor.hair}`,
            borderLeft: `2px solid ${accent}`,
            color: fg.primary,
            ['--hover' as string]: borderColor.base,
          } as React.CSSProperties
        }
      >
        <div className="flex items-start justify-between" style={{ padding: '14px 18px 6px' }}>
          <div>
            <div
              style={{
                color: fg.tertiary,
                fontSize: '10px',
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
              }}
            >
              加密交易系統
            </div>
            <div className="flex items-baseline gap-2 mt-1">
              <span
                style={{
                  color: accent,
                  fontSize: '20px',
                  fontWeight: 600,
                  letterSpacing: '-0.01em',
                }}
              >
                {running ? '運行中' : botState || '未連線'}
              </span>
              <span style={{ color: fg.tertiary, fontSize: '12px' }}>· Freqtrade · OKX 永續</span>
            </div>
          </div>
          <span
            style={{
              color: fg.tertiary,
              fontSize: '11px',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
            className="group-hover:underline"
          >
            交易紀錄 →
          </span>
        </div>
        <dl
          className="grid grid-cols-3"
          style={{ borderTop: `1px solid ${borderColor.hair}`, margin: 0 }}
        >
          <KV label="持倉" value={openPositions != null ? String(openPositions) : '—'} />
          <KV
            label="總筆數"
            value={totalTrades != null ? totalTrades.toLocaleString() : '—'}
            border
          />
          <KV
            label="累積 PnL"
            value={totalPnlPct != null ? `${totalPnlPct >= 0 ? '+' : ''}${totalPnlPct.toFixed(2)}%` : '—'}
            valueColor={pnlColor}
            border
          />
        </dl>
      </article>
    </Link>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Polymarket 情報（自行 fetch 兩個 API）
// ─────────────────────────────────────────────────────────────────────
function PolymarketCard() {
  const [status, setStatus] = useState<PolymarketStatus | null>(null);
  const [overview, setOverview] = useState<PolymarketOverview | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const [s, o] = await Promise.all([
        apiClient.get<PolymarketStatus>('/api/polymarket/status').catch(() => null),
        apiClient.get<PolymarketOverview>('/api/polymarket/overview').catch(() => null),
      ]);
      setStatus(s);
      setOverview(o);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 30_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const ok = status?.result === 'ok';
  const fail = status?.result === 'fail';
  const accent = ok ? semantic.live : fail ? semantic.error : semantic.warn;

  const dist = overview?.tier_distribution ?? {};
  const abc = (dist.A ?? 0) + (dist.B ?? 0) + (dist.C ?? 0);
  const totalWhales = overview?.totals?.whales ?? 0;
  const alerts24h = overview?.activity_24h?.alerts ?? 0;

  return (
    <Link href="/polymarket" className="block group">
      <article
        className="rounded-lg overflow-hidden h-full transition-colors group-hover:border-[color:var(--hover)]"
        style={
          {
            backgroundColor: layer['01'],
            border: `1px solid ${borderColor.hair}`,
            borderLeft: `2px solid ${accent}`,
            color: fg.primary,
            ['--hover' as string]: borderColor.base,
          } as React.CSSProperties
        }
      >
        <div className="flex items-start justify-between" style={{ padding: '14px 18px 6px' }}>
          <div>
            <div className="flex items-center gap-2">
              <span
                style={{
                  color: fg.tertiary,
                  fontSize: '10px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.1em',
                }}
              >
                Polymarket 情報
              </span>
              <span
                className="rounded-full px-1.5"
                style={{
                  fontSize: '9px',
                  color: semantic.whale,
                  backgroundColor: 'color-mix(in oklab, ' + semantic.whale + ' 12%, transparent)',
                  border: '1px solid color-mix(in oklab, ' + semantic.whale + ' 30%, transparent)',
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                }}
              >
                Phase 1.5b
              </span>
            </div>
            <div className="flex items-baseline gap-2 mt-1">
              <span
                style={{
                  color: accent,
                  fontSize: '20px',
                  fontWeight: 600,
                  letterSpacing: '-0.01em',
                }}
              >
                {loading ? '查詢中…' : ok ? '運行正常' : fail ? '失敗' : '尚未執行'}
              </span>
              {!loading && status && (
                <FreshnessIndicator
                  lastUpdate={parseServerDateStr(status.last_run_end)}
                />
              )}
            </div>
          </div>
          <span
            style={{
              color: fg.tertiary,
              fontSize: '11px',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
            className="group-hover:underline"
          >
            鯨魚追蹤 →
          </span>
        </div>
        <dl
          className="grid grid-cols-3"
          style={{ borderTop: `1px solid ${borderColor.hair}`, margin: 0 }}
        >
          <KV
            label="A/B/C 鯨魚"
            value={`${abc} / ${totalWhales}`}
            valueColor={abc > 0 ? semantic.whale : fg.primary}
          />
          <KV label="24h 推播" value={alerts24h.toString()} border />
          <KV
            label="掃描模式"
            value={status?.mode ? (status.duration_seconds != null ? `${status.mode} · ${status.duration_seconds}s` : status.mode) : '—'}
            border
          />
        </dl>
      </article>
    </Link>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Shared key-value cell
// ─────────────────────────────────────────────────────────────────────
function KV({
  label,
  value,
  border,
  valueColor,
}: {
  label: string;
  value: string;
  border?: boolean;
  valueColor?: string;
}) {
  return (
    <div
      style={{
        padding: '10px 16px',
        borderLeft: border ? `1px solid ${borderColor.hair}` : undefined,
      }}
    >
      <dt
        style={{
          color: fg.tertiary,
          fontSize: '10px',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        {label}
      </dt>
      <dd
        style={{
          color: valueColor || fg.primary,
          fontSize: '14px',
          marginTop: '3px',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </dd>
    </div>
  );
}
