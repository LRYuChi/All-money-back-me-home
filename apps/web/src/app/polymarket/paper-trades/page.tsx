'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { AppShell } from '@/components/layout/AppShell';
import { DataPanel } from '@/components/institutional/DataPanel';
import {
  DataTable,
  PnlCell,
  PctCell,
  Address,
  TimeCell,
  type Column,
} from '@/components/institutional/DataTable';
import { StatBar, type StatItem } from '@/components/institutional/StatBar';
import { TierBadge } from '@/components/polymarket/TierBadge';
import type { PaperBookSummary } from '@/components/polymarket/PaperBookCard';

/**
 * /polymarket/paper-trades — institutional redesign
 *
 * 上: StatBar (7 格關鍵指標)
 * 中: 當 total=0 → FollowerHealthBanner 大區塊說明為何空轉
 * 主: Trades DataTable (tabs 切換 All/Open/Closed) + 右側 sidebar
 * 右: FollowerHealth / TierBreakdown / TopSourceWallets
 */

type TradeStatus = 'open' | 'closed';
type TabKey = 'all' | 'open' | 'closed';

const REFRESH_MS = 20_000;

interface PaperTrade {
  id: number;
  follower_name: string;
  source_wallet: string;
  source_tier: string | null;
  condition_id: string;
  token_id: string | null;
  market_question: string | null;
  market_category: string | null;
  outcome: string | null;
  side: string;
  entry_price: number;
  entry_size: number;
  entry_notional: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
  status: TradeStatus;
  mark_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  market_closed: boolean | null;
  market_end_date: string | null;
}

interface TradesPayload {
  count: number;
  total: number;
  trades: PaperTrade[];
}

interface StatsPayload extends PaperBookSummary {
  by_tier: Array<{
    tier: string;
    total: number;
    closed: number;
    wins: number;
    win_rate: number;
    realized_pnl: number;
    realized_pnl_pct: number;
  }>;
  by_follower: Array<{
    follower_name: string;
    total: number;
    open: number;
    closed: number;
    wins: number;
    win_rate: number;
    realized_pnl: number;
  }>;
  top_source_wallets: Array<{
    source_wallet: string;
    source_tier: string | null;
    trades: number;
    closed: number;
    wins: number;
    realized_pnl: number;
  }>;
}

interface MissInfo {
  field: string;
  have: number;
  need: number;
  gap_pct?: number;
}

interface NearMissWhale {
  wallet_address: string;
  tier: string;
  trade_count_90d: number;
  win_rate: number;
  cumulative_pnl: number;
  avg_trade_size: number;
  resolved_count: number;
  last_trade_at: string | null;
  misses: MissInfo[];
  misses_count: number;
}

interface HealthPayload {
  health: 'green' | 'yellow' | 'red' | 'dormant';
  last_follower_fire_at: string | null;
  last_decision_at: string | null;
  hours_since_last_fire: number | null;
  total_paper_trades: number;
  total_decisions: number;
  tier_distribution: Record<string, number>;
  qualifying_whales: number;
  near_miss: NearMissWhale[];
  thresholds_ref: {
    tier_C: {
      min_trades_90d: number;
      min_win_rate: number;
      min_cumulative_pnl_usdc: number;
      min_avg_trade_size_usdc: number;
    };
  };
}

export default function PaperTradesPage() {
  const [tab, setTab] = useState<TabKey>('all');
  const [trades, setTrades] = useState<TradesPayload | null>(null);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const statusParam = tab === 'all' ? 'all' : tab;

  const fetchAll = useCallback(async () => {
    try {
      const [t, s, h] = await Promise.all([
        apiClient.get<TradesPayload>('/api/polymarket/paper-trades', {
          params: { status: statusParam, limit: '200' },
        }),
        apiClient.get<StatsPayload>('/api/polymarket/paper-trades/stats'),
        apiClient
          .get<HealthPayload>('/api/polymarket/paper-trades/follower-health')
          .catch(() => null),
      ]);
      setTrades(t);
      setStats(s);
      setHealth(h);
      setLastUpdate(new Date());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [statusParam]);

  useEffect(() => {
    setLoading(true);
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  const statBarItems: StatItem[] = stats
    ? [
        {
          label: 'Capital',
          value: `$${stats.initial_capital_usdc.toLocaleString()}`,
          sub: 'paper',
          tone: 'neutral',
        },
        {
          label: 'Combined PnL',
          value: fmtPnl(stats.summary.combined_pnl_usdc),
          sub: fmtPctSigned((stats.summary.combined_pnl_pct_of_capital ?? 0) * 100),
          tone: toneFor(stats.summary.combined_pnl_usdc),
        },
        {
          label: 'Realized',
          value: fmtPnl(stats.summary.realized_pnl_usdc),
          sub:
            stats.summary.closed > 0
              ? fmtPctSigned((stats.summary.realized_pnl_pct ?? 0) * 100)
              : '—',
          tone: toneFor(stats.summary.realized_pnl_usdc),
        },
        {
          label: 'Unrealized',
          value: fmtPnl(stats.summary.unrealized_pnl_usdc),
          sub: `${stats.summary.open} open`,
          tone: toneFor(stats.summary.unrealized_pnl_usdc),
        },
        {
          label: 'Win Rate',
          value: stats.summary.closed > 0 ? `${(stats.summary.win_rate * 100).toFixed(1)}%` : '—',
          sub: `${stats.summary.wins}W / ${stats.summary.losses}L`,
          tone: 'neutral',
        },
        {
          label: 'Utilization',
          value: `$${stats.summary.open_stake_usdc.toFixed(0)}`,
          sub: `${(stats.summary.capital_utilization_pct * 100).toFixed(1)}%`,
          tone: 'neutral',
        },
        {
          label: 'Total Trades',
          value: stats.summary.total.toString(),
          sub: `${stats.summary.open} • ${stats.summary.closed}`,
          tone: 'neutral',
        },
      ]
    : [];

  return (
    <AppShell
      pageTitle="Paper Trading · Polymarket 鯨魚跟單紙上簿"
      dataFreshness={{ lastUpdate, refreshMs: REFRESH_MS, onRefresh: fetchAll }}
    >
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorBanner message={error} />}

        {stats && <StatBar stats={statBarItems} minColWidth={140} />}

        {health && stats && stats.summary.total === 0 && (
          <FollowerHealthBanner health={health} />
        )}

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 2.4fr) minmax(260px, 1fr)',
            gap: 12,
          }}
        >
          <DataPanel
            title="Paper Trades"
            subtitle={trades ? `${trades.total} total` : '—'}
            statusDot={health ? dotForHealth(health.health) : undefined}
            density="none"
            actions={<Tabs tab={tab} setTab={setTab} stats={stats} />}
          >
            <TradesTableWrapper trades={trades?.trades ?? []} tab={tab} />
          </DataPanel>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {health && <FollowerHealthPanel health={health} />}
            {stats && <TierBreakdownPanel data={stats.by_tier} />}
            {stats && <TopSourceWalletsPanel data={stats.top_source_wallets} />}
            {stats && stats.by_follower.length > 1 && (
              <FollowerBreakdownPanel data={stats.by_follower} />
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Tabs in DataPanel header
// ─────────────────────────────────────────────────────────────────────
function Tabs({
  tab,
  setTab,
  stats,
}: {
  tab: TabKey;
  setTab: (t: TabKey) => void;
  stats: StatsPayload | null;
}) {
  const s = stats?.summary;
  const tabs: { key: TabKey; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: s?.total ?? 0 },
    { key: 'open', label: 'Open', count: s?.open ?? 0 },
    { key: 'closed', label: 'Closed', count: s?.closed ?? 0 },
  ];
  return (
    <div style={{ display: 'flex', gap: 2 }}>
      {tabs.map((t) => {
        const active = t.key === tab;
        return (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '4px 10px',
              fontSize: 11,
              fontWeight: active ? 600 : 400,
              color: active ? fg.primary : fg.tertiary,
              backgroundColor: active ? layer['03'] : 'transparent',
              border: `1px solid ${active ? borderColor.base : borderColor.hair}`,
              borderRadius: 2,
              cursor: 'pointer',
              fontFamily: 'inherit',
              letterSpacing: 0.3,
            }}
          >
            {t.label}
            <span
              style={{
                marginLeft: 5,
                color: active ? fg.secondary : fg.tertiary,
                fontVariantNumeric: 'tabular-nums',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                fontWeight: 400,
              }}
            >
              {t.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Main trades table
// ─────────────────────────────────────────────────────────────────────
function TradesTableWrapper({ trades, tab }: { trades: PaperTrade[]; tab: TabKey }) {
  const columns: Column<PaperTrade>[] = [
    {
      key: 'market',
      header: 'Market',
      render: (t) => (
        <div style={{ maxWidth: 340 }}>
          <div
            style={{
              color: fg.primary,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={t.market_question ?? t.condition_id}
          >
            {t.market_question ?? t.condition_id.substring(0, 20) + '…'}
          </div>
          <div style={{ fontSize: 10, color: fg.tertiary, marginTop: 2 }}>
            {t.outcome ?? '—'} · {t.market_category ?? '—'}
          </div>
        </div>
      ),
    },
    {
      key: 'side',
      header: 'Side',
      render: (t) => <SideTag side={t.side} />,
    },
    {
      key: 'entry',
      header: 'Entry',
      align: 'right',
      mono: true,
      render: (t) => t.entry_price.toFixed(4),
    },
    {
      key: 'current',
      header: 'Current / Exit',
      align: 'right',
      mono: true,
      render: (t) => {
        const p = t.status === 'open' ? t.mark_price : t.exit_price;
        return p != null ? p.toFixed(4) : <span style={{ color: fg.tertiary }}>—</span>;
      },
    },
    {
      key: 'size',
      header: 'Size',
      align: 'right',
      mono: true,
      render: (t) => `$${t.entry_notional.toFixed(2)}`,
    },
    {
      key: 'pnl',
      header: 'P&L',
      align: 'right',
      mono: true,
      render: (t) => {
        const pnl = t.status === 'open' ? t.unrealized_pnl : t.realized_pnl;
        const pct = t.status === 'open' ? t.unrealized_pnl_pct : t.realized_pnl_pct;
        return (
          <div>
            <PnlCell value={pnl} />
            {pct != null && (
              <div style={{ fontSize: 10, color: fg.tertiary }}>
                <PctCell value={pct} precision={2} />
              </div>
            )}
          </div>
        );
      },
    },
    {
      key: 'source',
      header: 'Source',
      render: (t) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Link
            href={`/polymarket/wallet/${t.source_wallet}`}
            style={{ textDecoration: 'none' }}
            onClick={(e) => e.stopPropagation()}
          >
            <Address addr={t.source_wallet} />
          </Link>
          {t.source_tier && <TierBadge tier={t.source_tier} size="sm" />}
        </div>
      ),
    },
    {
      key: 'time',
      header: 'Time',
      mono: true,
      render: (t) => (
        <div style={{ fontSize: 10 }}>
          <div>
            <TimeCell iso={t.entry_time} />
          </div>
          {t.exit_time && (
            <div style={{ color: fg.tertiary }}>
              → <TimeCell iso={t.exit_time} />
            </div>
          )}
        </div>
      ),
    },
  ];

  return (
    <DataTable
      rows={trades}
      columns={columns}
      rowKey={(t) => String(t.id)}
      emptyMessage={
        <div style={{ padding: '32px 16px' }}>
          <div style={{ fontSize: 24, marginBottom: 8 }}>▢</div>
          <div style={{ color: fg.secondary, fontSize: 12, lineHeight: 1.6 }}>
            {tab === 'open'
              ? '目前沒有持倉中的紙上單。Follower 要等鯨魚觸發訊號才會進場。'
              : tab === 'closed'
                ? '尚無已結算的紙上單。'
                : '尚無任何紙上單。Follower 空轉中 — 檢視右側 Follower Health 了解原因。'}
          </div>
        </div>
      }
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar panels
// ─────────────────────────────────────────────────────────────────────
function FollowerHealthPanel({ health }: { health: HealthPayload }) {
  return (
    <DataPanel
      title="Follower Health"
      statusDot={dotForHealth(health.health)}
      density="comfortable"
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          gap: '4px 12px',
          fontSize: 11,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <span style={{ color: fg.tertiary }}>狀態</span>
        <span style={{ color: colorForHealth(health.health), fontWeight: 500 }}>
          {labelForHealth(health.health)}
        </span>

        <span style={{ color: fg.tertiary }}>上次觸發</span>
        <span>
          {health.last_follower_fire_at
            ? `${formatAge(health.hours_since_last_fire)} ago`
            : 'never'}
        </span>

        <span style={{ color: fg.tertiary }}>決策累計</span>
        <span>{health.total_decisions}</span>

        <span style={{ color: fg.tertiary }}>白名單</span>
        <span>{health.qualifying_whales}</span>
      </div>
    </DataPanel>
  );
}

function TierBreakdownPanel({ data }: { data: StatsPayload['by_tier'] }) {
  return (
    <DataPanel title="By Tier" density="none">
      <DataTable
        compact
        stickyHeader={false}
        rows={data}
        columns={[
          {
            key: 'tier',
            header: 'Tier',
            render: (r) => <TierBadge tier={r.tier} size="sm" />,
          },
          {
            key: 'n',
            header: 'N',
            align: 'right',
            mono: true,
            render: (r) => r.total.toString(),
          },
          {
            key: 'wr',
            header: 'Win%',
            align: 'right',
            mono: true,
            render: (r) =>
              r.closed > 0 ? `${(r.win_rate * 100).toFixed(0)}%` : '—',
          },
          {
            key: 'pnl',
            header: 'PnL',
            align: 'right',
            mono: true,
            render: (r) => <PnlCell value={r.realized_pnl} />,
          },
        ]}
        rowKey={(r) => r.tier}
        emptyMessage="無資料"
      />
    </DataPanel>
  );
}

function TopSourceWalletsPanel({ data }: { data: StatsPayload['top_source_wallets'] }) {
  return (
    <DataPanel title="Top Sources" subtitle="by PnL" density="none">
      <DataTable
        compact
        stickyHeader={false}
        rows={data}
        columns={[
          {
            key: 'addr',
            header: 'Wallet',
            render: (r) => (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <Link
                  href={`/polymarket/wallet/${r.source_wallet}`}
                  style={{ textDecoration: 'none' }}
                >
                  <Address addr={r.source_wallet} />
                </Link>
                {r.source_tier && <TierBadge tier={r.source_tier} size="sm" />}
              </div>
            ),
          },
          {
            key: 'stats',
            header: 'N/W',
            align: 'right',
            mono: true,
            render: (r) =>
              r.closed > 0 ? `${r.wins}/${r.closed}` : `${r.trades}×0`,
          },
          {
            key: 'pnl',
            header: 'PnL',
            align: 'right',
            mono: true,
            render: (r) => <PnlCell value={r.realized_pnl} />,
          },
        ]}
        rowKey={(r) => r.source_wallet}
        emptyMessage="尚無資料"
      />
    </DataPanel>
  );
}

function FollowerBreakdownPanel({ data }: { data: StatsPayload['by_follower'] }) {
  return (
    <DataPanel title="By Follower" density="none">
      <DataTable
        compact
        stickyHeader={false}
        rows={data}
        columns={[
          {
            key: 'name',
            header: 'Follower',
            render: (r) => (
              <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>
                {r.follower_name}
              </span>
            ),
          },
          {
            key: 'open',
            header: 'Open',
            align: 'right',
            mono: true,
            render: (r) => r.open.toString(),
          },
          {
            key: 'wr',
            header: 'Win%',
            align: 'right',
            mono: true,
            render: (r) =>
              r.closed > 0 ? `${(r.win_rate * 100).toFixed(0)}%` : '—',
          },
          {
            key: 'pnl',
            header: 'PnL',
            align: 'right',
            mono: true,
            render: (r) => <PnlCell value={r.realized_pnl} />,
          },
        ]}
        rowKey={(r) => r.follower_name}
        emptyMessage="無 follower"
      />
    </DataPanel>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Follower Health large banner (shown when total=0)
// ─────────────────────────────────────────────────────────────────────
function FollowerHealthBanner({ health }: { health: HealthPayload }) {
  const tierC = health.thresholds_ref.tier_C;
  return (
    <DataPanel
      title="Follower 空轉中 — 為何還沒有紙上單"
      statusDot={dotForHealth(health.health)}
      subtitle={
        health.qualifying_whales > 0
          ? `${health.qualifying_whales} 鯨魚已進白名單，等交易訊號`
          : `0 / ${Object.values(health.tier_distribution).reduce((a, b) => a + b, 0)} 達門檻`
      }
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(180px, 1fr) minmax(400px, 3fr)',
          gap: 24,
          padding: '4px 0',
        }}
      >
        <div>
          <div
            style={{
              fontSize: 10,
              color: fg.tertiary,
              letterSpacing: 0.5,
              textTransform: 'uppercase',
              marginBottom: 8,
            }}
          >
            Tier 分布
          </div>
          <table style={{ width: '100%', fontSize: 11 }}>
            <tbody>
              {Object.entries(health.tier_distribution)
                .sort((a, b) => b[1] - a[1])
                .map(([tier, count]) => (
                  <tr key={tier}>
                    <td style={{ padding: '3px 0' }}>
                      <TierBadge tier={tier} size="sm" />
                    </td>
                    <td
                      style={{
                        textAlign: 'right',
                        fontFamily: 'ui-monospace, monospace',
                        fontVariantNumeric: 'tabular-nums',
                        color: fg.primary,
                      }}
                    >
                      {count}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        <div>
          <div
            style={{
              fontSize: 10,
              color: fg.tertiary,
              letterSpacing: 0.5,
              textTransform: 'uppercase',
              marginBottom: 4,
            }}
          >
            距 Tier C 最近的 {Math.min(6, health.near_miss.length)} 錢包
          </div>
          <div style={{ fontSize: 10, color: fg.tertiary, marginBottom: 8 }}>
            門檻：≥{tierC.min_trades_90d} trades · ≥{(tierC.min_win_rate * 100).toFixed(0)}% winrate ·
            ≥${tierC.min_cumulative_pnl_usdc} pnl · ≥${tierC.min_avg_trade_size_usdc} avg
          </div>
          {health.near_miss.length === 0 ? (
            <div style={{ color: fg.tertiary, fontSize: 11 }}>
              無錢包接近門檻 — 系統在等活躍鯨魚累積。
            </div>
          ) : (
            <NearMissTable rows={health.near_miss.slice(0, 6)} />
          )}
        </div>
      </div>
    </DataPanel>
  );
}

function NearMissTable({ rows }: { rows: NearMissWhale[] }) {
  return (
    <DataTable
      compact
      stickyHeader={false}
      rows={rows}
      columns={[
        {
          key: 'addr',
          header: 'Wallet',
          render: (r) => (
            <Link
              href={`/polymarket/wallet/${r.wallet_address}`}
              style={{ textDecoration: 'none' }}
            >
              <Address addr={r.wallet_address} />
            </Link>
          ),
        },
        {
          key: 'tier',
          header: '',
          render: (r) => <TierBadge tier={r.tier} size="sm" />,
        },
        {
          key: 'tr',
          header: 'Tr',
          align: 'right',
          mono: true,
          render: (r) => r.trade_count_90d.toString(),
        },
        {
          key: 'wr',
          header: 'Wr%',
          align: 'right',
          mono: true,
          render: (r) => `${(r.win_rate * 100).toFixed(0)}`,
        },
        {
          key: 'pnl',
          header: 'PnL',
          align: 'right',
          mono: true,
          render: (r) => `$${Math.round(r.cumulative_pnl).toLocaleString()}`,
        },
        {
          key: 'avg',
          header: 'Avg',
          align: 'right',
          mono: true,
          render: (r) => `$${r.avg_trade_size.toFixed(0)}`,
        },
        {
          key: 'misses',
          header: 'Missing',
          render: (r) => (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {r.misses.length === 0 ? (
                <span
                  style={{
                    padding: '1px 6px',
                    borderRadius: 2,
                    fontSize: 10,
                    color: semantic.live,
                    border: `1px solid ${semantic.liveBorder}`,
                    fontFamily: 'ui-monospace, monospace',
                  }}
                >
                  ALL OK
                </span>
              ) : (
                r.misses.map((m) => (
                  <span
                    key={m.field}
                    style={{
                      padding: '1px 6px',
                      borderRadius: 2,
                      fontSize: 10,
                      color: semantic.warn,
                      border: `1px solid ${semantic.warnBorder}`,
                      fontFamily: 'ui-monospace, monospace',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {fieldLabel(m.field)}
                    {m.gap_pct != null ? `-${m.gap_pct.toFixed(0)}%` : ''}
                  </span>
                ))
              )}
            </div>
          ),
        },
      ]}
      rowKey={(r) => r.wallet_address}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// tiny helpers
// ─────────────────────────────────────────────────────────────────────
function SideTag({ side }: { side: string }) {
  const isBuy = side.toUpperCase() === 'BUY';
  return (
    <span
      style={{
        padding: '1px 6px',
        borderRadius: 2,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.5,
        fontFamily: 'ui-monospace, monospace',
        color: isBuy ? semantic.no : semantic.yes,
        border: `1px solid ${isBuy ? semantic.noBorder : semantic.yesBorder}`,
      }}
    >
      {side}
    </span>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: '8px 12px',
        backgroundColor: semantic.errorBg,
        border: `1px solid ${semantic.errorBorder}`,
        borderRadius: 2,
        color: semantic.error,
        fontSize: 12,
      }}
    >
      載入失敗：{message}
    </div>
  );
}

function toneFor(v: number): 'up' | 'down' | 'neutral' {
  if (v > 0) return 'up';
  if (v < 0) return 'down';
  return 'neutral';
}

function dotForHealth(h: HealthPayload['health']): 'green' | 'yellow' | 'red' | 'gray' {
  return h === 'green' ? 'green' : h === 'yellow' ? 'yellow' : h === 'red' ? 'red' : 'gray';
}

function colorForHealth(h: HealthPayload['health']): string {
  return h === 'green'
    ? semantic.live
    : h === 'yellow'
      ? semantic.warn
      : h === 'red'
        ? semantic.error
        : fg.tertiary;
}

function labelForHealth(h: HealthPayload['health']): string {
  switch (h) {
    case 'green':
      return 'ACTIVE';
    case 'yellow':
      return 'SLOW';
    case 'red':
      return 'STALE';
    case 'dormant':
    default:
      return 'DORMANT';
  }
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  const abs = Math.abs(v);
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPctSigned(v: number): string {
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function formatAge(hours: number | null): string {
  if (hours == null) return '—';
  if (hours < 1) return `${Math.floor(hours * 60)}m`;
  if (hours < 24) return `${Math.floor(hours)}h`;
  return `${Math.floor(hours / 24)}d`;
}

function fieldLabel(f: string): string {
  const m: Record<string, string> = {
    trade_count_90d: 'tr',
    win_rate: 'wr',
    cumulative_pnl: 'pnl',
    avg_trade_size: 'avg',
  };
  return m[f] ?? f;
}
