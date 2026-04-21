'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { layer, fg, semantic } from '@/lib/polymarket/tokens';
import { PipelineStatusCard } from '@/components/polymarket/PipelineStatusCard';
import { OverviewCards } from '@/components/polymarket/OverviewCards';
import { WhaleDirectoryTable } from '@/components/polymarket/WhaleDirectoryTable';
import { AlertFeed } from '@/components/polymarket/AlertFeed';
import { ActiveMarketsTable } from '@/components/polymarket/ActiveMarketsTable';

interface PageData {
  status: any;
  overview: any;
  whales: any;
  alerts: any;
  markets: any;
}

const REFRESH_INTERVAL_MS = 30_000;

export default function PolymarketPage() {
  const [data, setData] = useState<PageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [status, overview, whales, alerts, markets] = await Promise.all([
        apiClient.get('/api/polymarket/status'),
        apiClient.get('/api/polymarket/overview').catch(() => null),
        apiClient
          .get('/api/polymarket/whales', { params: { tier: 'A,B,C,volatile', limit: '100' } })
          .catch(() => ({ whales: [] })),
        apiClient.get('/api/polymarket/alerts', { params: { hours: '24', limit: '50' } }).catch(() => ({
          alerts: [],
          window_hours: 24,
        })),
        apiClient.get('/api/polymarket/markets', { params: { active: 'true', limit: '20' } }).catch(() => ({
          markets: [],
        })),
      ]);
      setData({ status, overview, whales, alerts, markets });
      setLastUpdate(new Date());
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? String(e));
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
        fontFamily:
          'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      }}
    >
      <div className="max-w-7xl mx-auto px-4 py-6">
        <header className="flex items-center justify-between mb-6">
          <div>
            <div className="flex items-baseline gap-3">
              <Link
                href="/"
                style={{ color: fg.tertiary, fontSize: '12px', textDecoration: 'underline' }}
              >
                ← 主儀表板
              </Link>
              <h1 style={{ color: fg.primary, fontSize: '24px', fontWeight: 600 }}>
                Polymarket 情報
              </h1>
              <span
                style={{
                  color: fg.tertiary,
                  fontSize: '12px',
                  fontFamily: 'var(--font-mono, ui-monospace)',
                }}
              >
                Phase 1
              </span>
            </div>
            <div style={{ color: fg.tertiary, fontSize: '12px', marginTop: '4px' }}>
              鯨魚分層追蹤 · 5 分鐘自動更新 · 自動重新整理 30s
            </div>
          </div>
          <div className="flex items-center gap-3">
            {lastUpdate && (
              <span style={{ color: fg.tertiary, fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
                UI 更新 {lastUpdate.toLocaleTimeString('zh-TW', { hour12: false })}
              </span>
            )}
            <button
              onClick={fetchAll}
              className="rounded border px-3 py-1"
              style={{
                backgroundColor: layer['02'],
                borderColor: 'oklch(30% 0.010 240)',
                color: fg.primary,
                fontSize: '12px',
              }}
            >
              手動刷新
            </button>
          </div>
        </header>

        {loading && !data && (
          <div
            className="rounded-md p-8 text-center"
            style={{ backgroundColor: layer['01'], color: fg.tertiary }}
          >
            載入中…
          </div>
        )}

        {error && (
          <div
            className="rounded-md p-4 mb-4 border"
            style={{
              backgroundColor: semantic.errorBg,
              borderColor: semantic.errorBorder,
              color: semantic.error,
            }}
          >
            <div style={{ fontWeight: 600 }}>API 錯誤</div>
            <div style={{ fontSize: '12px', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
              {error}
            </div>
          </div>
        )}

        {data && (
          <div className="space-y-4">
            <PipelineStatusCard status={data.status} />
            <OverviewCards overview={data.overview} />
            <WhaleDirectoryTable whales={data.whales?.whales ?? []} />
            <AlertFeed
              alerts={data.alerts?.alerts ?? []}
              windowHours={data.alerts?.window_hours ?? 24}
            />
            <ActiveMarketsTable markets={data.markets?.markets ?? []} />
          </div>
        )}
      </div>
    </div>
  );
}
