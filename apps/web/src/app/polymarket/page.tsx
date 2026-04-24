'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { PipelineStatusCard } from '@/components/polymarket/PipelineStatusCard';
import { OverviewCards } from '@/components/polymarket/OverviewCards';
import { WhaleDirectoryTable } from '@/components/polymarket/WhaleDirectoryTable';
import { AlertFeed } from '@/components/polymarket/AlertFeed';
import { ActiveMarketsTable } from '@/components/polymarket/ActiveMarketsTable';
import {
  HighlightCards,
  type TierMover,
  type EmergingWhale,
  type SteadyGrower,
} from '@/components/polymarket/HighlightCards';
import {
  PaperBookCard,
  type PaperBookSummary,
} from '@/components/polymarket/PaperBookCard';

interface StatusPayload {
  last_run_start: string | null;
  last_run_end: string | null;
  duration_seconds: number | null;
  result: 'ok' | 'fail' | 'never_run' | null;
  exit_code: number | null;
  mode: string | null;
  markets_limit: number | null;
  wallets_cap: number | null;
}

interface OverviewPayload {
  tier_distribution: Record<string, number>;
  totals: { markets: number; active_markets: number; whales: number; trades: number };
  activity_24h: { trades: number; alerts: number };
  latest_tier_change: {
    wallet_address: string;
    from_tier: string | null;
    to_tier: string;
    changed_at: string;
    reason: string;
  } | null;
}

interface WhaleRow {
  wallet_address: string;
  tier: string;
  trade_count_90d: number;
  win_rate: number;
  cumulative_pnl: number;
  avg_trade_size: number;
  segment_win_rates: number[];
  stability_pass: boolean;
  resolved_count: number;
  last_trade_at: string | null;
  last_computed_at: string;
}

interface AlertRow {
  wallet_address: string;
  tx_hash: string;
  event_index: number;
  tier: string;
  condition_id: string;
  market_question: string;
  side: string;
  outcome: string;
  size: number;
  price: number;
  notional: number;
  match_time: string;
  alerted_at: string;
}

interface MarketRow {
  condition_id: string;
  question: string;
  market_slug: string;
  category: string;
  end_date_iso: string | null;
  active: boolean;
  closed: boolean;
  tokens: { token_id: string; outcome: string; price: number | null }[];
  trades_24h: number;
}

interface PageData {
  status: StatusPayload | null;
  overview: OverviewPayload | null;
  whales: { count: number; whales: WhaleRow[] } | null;
  alerts: { count: number; alerts: AlertRow[]; window_hours: number } | null;
  markets: { count: number; markets: MarketRow[] } | null;
  movers: { count: number; window_hours: number; movers: TierMover[] } | null;
  emerging: { count: number; whales: EmergingWhale[] } | null;
  growers: { count: number; growers: SteadyGrower[] } | null;
  paperBook: PaperBookSummary | null;
}

const REFRESH_INTERVAL_MS = 30_000;

export default function PolymarketPage() {
  const [data, setData] = useState<PageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [status, overview, whales, alerts, markets, movers, emerging, growers, paperBook] =
        await Promise.all([
          apiClient.get<StatusPayload>('/api/polymarket/status'),
          apiClient.get<OverviewPayload>('/api/polymarket/overview').catch(() => null),
          apiClient
            .get<{ count: number; whales: WhaleRow[] }>('/api/polymarket/whales', {
              // 1.5c.4 擴大 tier 覆蓋 — 包含 emerging 讓使用者在真鯨魚累積前
              // 也有錢包可點擊進詳情頁（驗證新的 UI）
              params: { tier: 'A,B,C,volatile,emerging', limit: '100' },
            })
            .catch(() => ({ count: 0, whales: [] })),
          apiClient
            .get<{ count: number; alerts: AlertRow[]; window_hours: number }>('/api/polymarket/alerts', {
              params: { hours: '24', limit: '50' },
            })
            .catch(() => ({ count: 0, alerts: [], window_hours: 24 })),
          apiClient
            .get<{ count: number; markets: MarketRow[] }>('/api/polymarket/markets', {
              params: { active: 'true', limit: '20' },
            })
            .catch(() => ({ count: 0, markets: [] })),
          apiClient
            .get<{ count: number; window_hours: number; movers: TierMover[] }>(
              '/api/polymarket/tier-movers',
              { params: { hours: '24', limit: '10' } }
            )
            .catch(() => ({ count: 0, window_hours: 24, movers: [] })),
          apiClient
            .get<{ count: number; whales: EmergingWhale[] }>('/api/polymarket/emerging-whales', {
              params: { limit: '10' },
            })
            .catch(() => ({ count: 0, whales: [] })),
          apiClient
            .get<{ count: number; growers: SteadyGrower[] }>('/api/polymarket/steady-growers', {
              params: { limit: '10' },
            })
            .catch(() => ({ count: 0, growers: [] })),
          apiClient
            .get<PaperBookSummary>('/api/polymarket/paper-trades/stats')
            .catch(() => null),
        ]);
      setData({ status, overview, whales, alerts, markets, movers, emerging, growers, paperBook });
      setLastUpdate(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  return (
    <div
      className="min-h-screen"
      style={{
        backgroundColor: layer['00'],
        color: fg.primary,
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      }}
    >
      <div className="max-w-[1400px] mx-auto" style={{ padding: '24px 28px' }}>
        <Header lastUpdate={lastUpdate} onRefresh={fetchAll} />

        {loading && !data && <LoadingState />}
        {error && <ErrorBanner message={error} />}

        {data && (
          <div className="flex flex-col gap-4">
            <PipelineStatusCard status={data.status} />
            <OverviewCards overview={data.overview} />
            <HighlightCards
              movers={data.movers?.movers ?? []}
              emerging={data.emerging?.whales ?? []}
              growers={data.growers?.growers ?? []}
              windowHours={data.movers?.window_hours ?? 24}
            />
            <PaperBookCard data={data.paperBook} />
            <WhaleDirectoryTable whales={data.whales?.whales ?? []} />
            <AlertFeed
              alerts={data.alerts?.alerts ?? []}
              windowHours={data.alerts?.window_hours ?? 24}
            />
            <ActiveMarketsTable markets={data.markets?.markets ?? []} />
          </div>
        )}

        <footer
          className="mt-8 pt-4"
          style={{ borderTop: `1px solid ${borderColor.hair}`, color: fg.tertiary, fontSize: '11px' }}
        >
          Polymarket 情報系統 · Phase 1 鯨魚追蹤 · 每 5 分鐘自動收集，每 30 秒重新整理介面
        </footer>
      </div>
    </div>
  );
}

function Header({ lastUpdate, onRefresh }: { lastUpdate: Date | null; onRefresh: () => void }) {
  return (
    <header
      className="flex items-end justify-between flex-wrap gap-4"
      style={{ marginBottom: '24px' }}
    >
      <div>
        <Link
          href="/"
          style={{
            color: fg.tertiary,
            fontSize: '11px',
            textDecoration: 'none',
          }}
        >
          ← 主儀表板
        </Link>
        <div className="flex items-baseline gap-3 mt-1">
          <h1
            style={{
              color: fg.primary,
              fontSize: '28px',
              fontWeight: 700,
              letterSpacing: '-0.02em',
              lineHeight: 1.1,
            }}
          >
            Polymarket 情報
          </h1>
          <span
            className="rounded-full border"
            style={{
              padding: '3px 10px',
              fontSize: '11px',
              color: semantic.whale,
              backgroundColor: layer['02'],
              borderColor: 'color-mix(in oklab, ' + semantic.whale + ' 30%, transparent)',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
          >
            Phase 1 · 鯨魚追蹤
          </span>
        </div>
        <div style={{ color: fg.tertiary, fontSize: '12px', marginTop: '6px' }}>
          基於 Polymarket CLOB + Data API · 讀取本地 SQLite 快照
        </div>
      </div>

      <div className="flex items-center gap-3">
        {lastUpdate && (
          <span
            style={{
              color: fg.tertiary,
              fontSize: '11px',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
          >
            UI 更新 {lastUpdate.toLocaleTimeString('zh-TW', { hour12: false })}
          </span>
        )}
        <button
          onClick={onRefresh}
          className="rounded-lg border transition-colors hover:border-opacity-50"
          style={{
            padding: '6px 14px',
            backgroundColor: layer['02'],
            borderColor: borderColor.base,
            color: fg.primary,
            fontSize: '12px',
            fontWeight: 500,
          }}
        >
          手動刷新
        </button>
      </div>
    </header>
  );
}

function LoadingState() {
  return (
    <div
      className="rounded-lg text-center"
      style={{
        backgroundColor: layer['01'],
        color: fg.tertiary,
        border: `1px solid ${borderColor.hair}`,
        padding: '80px 20px',
        fontSize: '13px',
      }}
    >
      載入中…
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg border"
      style={{
        backgroundColor: semantic.errorBg,
        borderColor: semantic.errorBorder,
        color: semantic.error,
        padding: '14px 18px',
        marginBottom: '16px',
      }}
    >
      <div style={{ fontWeight: 600, fontSize: '13px' }}>API 錯誤</div>
      <div
        style={{
          fontSize: '11px',
          marginTop: '4px',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          color: semantic.error,
          opacity: 0.85,
        }}
      >
        {message}
      </div>
    </div>
  );
}
