'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from '@/components/polymarket/Card';
import { TierBadge } from '@/components/polymarket/TierBadge';
import type { PaperBookSummary } from '@/components/polymarket/PaperBookCard';

/**
 * /polymarket/paper-trades — 紙上跟單完整視圖
 *
 * 三個 tab: All / Open / Closed
 * 每個 tab 的表格欄位略有差異（Open 秀未實現 PnL、Closed 秀已實現 PnL）
 * 右側 sidebar 顯示按 tier 拆分、top 來源鯨魚排行
 */

type TradeStatus = 'open' | 'closed';

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
  mark_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  market_closed: boolean | null;
  market_end_date: string | null;
}

interface TradesPayload {
  count: number;
  total: number;
  limit: number;
  offset: number;
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

type TabKey = 'all' | 'open' | 'closed';

const REFRESH_MS = 20_000;

export default function PaperTradesPage() {
  const [tab, setTab] = useState<TabKey>('all');
  const [trades, setTrades] = useState<TradesPayload | null>(null);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
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

        {loading && !trades && <LoadingBanner />}
        {error && <ErrorBanner message={error} />}

        {stats && <StatsBar stats={stats} />}

        {health && stats && stats.summary.total === 0 && (
          <div style={{ marginTop: 16 }}>
            <FollowerHealthBanner health={health} />
          </div>
        )}

        {stats && (
          <div style={{ marginTop: 16 }}>
            <Tabs tab={tab} setTab={setTab} stats={stats} />
          </div>
        )}

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 2.2fr) minmax(260px, 1fr)',
            gap: 16,
            marginTop: 16,
          }}
        >
          <TradesTable trades={trades?.trades ?? []} tab={tab} loading={loading} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {health && <FollowerHealthCard health={health} />}
            {stats && <TierBreakdown data={stats.by_tier} />}
            {stats && <TopSourceWallets data={stats.top_source_wallets} />}
            {stats && stats.by_follower.length > 1 && (
              <FollowerBreakdown data={stats.by_follower} />
            )}
          </div>
        </div>

        <footer
          className="mt-8 pt-4"
          style={{
            borderTop: `1px solid ${borderColor.hair}`,
            color: fg.tertiary,
            fontSize: 11,
          }}
        >
          紙上跟單 · 絕無真實下單 · 每 {REFRESH_MS / 1000} 秒重新整理
        </footer>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Header
// ─────────────────────────────────────────────────────────────────────
function Header({ lastUpdate, onRefresh }: { lastUpdate: Date | null; onRefresh: () => void }) {
  return (
    <header className="flex items-start justify-between" style={{ marginBottom: 16 }}>
      <div>
        <div style={{ fontSize: 11, color: fg.tertiary, letterSpacing: 1 }}>POLYMARKET</div>
        <h1 style={{ fontSize: 24, fontWeight: 600, color: fg.primary, marginTop: 4 }}>
          📘 紙上跟單 <span style={{ color: fg.tertiary, fontSize: 14, fontWeight: 400 }}>Paper Trading</span>
        </h1>
        <Link
          href="/polymarket"
          style={{ fontSize: 12, color: semantic.live, textDecoration: 'none' }}
        >
          ← 回主頁
        </Link>
      </div>
      <div style={{ textAlign: 'right', fontSize: 11, color: fg.tertiary }}>
        {lastUpdate && <div>最後更新 {lastUpdate.toLocaleTimeString()}</div>}
        <button
          onClick={onRefresh}
          style={{
            marginTop: 6,
            padding: '4px 10px',
            borderRadius: 4,
            border: `1px solid ${borderColor.hair}`,
            backgroundColor: layer['01'],
            color: fg.primary,
            fontSize: 11,
            cursor: 'pointer',
          }}
        >
          重新整理
        </button>
      </div>
    </header>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Stats Bar
// ─────────────────────────────────────────────────────────────────────
function StatsBar({ stats }: { stats: StatsPayload }) {
  const s = stats.summary;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 12,
      }}
    >
      <Stat
        label="起始資金"
        value={`$${stats.initial_capital_usdc.toLocaleString()}`}
        tone="neutral"
      />
      <Stat
        label="Combined PnL"
        value={fmtPnlFull(s.combined_pnl_usdc)}
        sub={fmtPctSigned((s.combined_pnl_pct_of_capital ?? 0) * 100)}
        tone={toneFor(s.combined_pnl_usdc)}
      />
      <Stat
        label="已實現 PnL"
        value={fmtPnlFull(s.realized_pnl_usdc)}
        sub={s.closed > 0 ? fmtPctSigned((s.realized_pnl_pct ?? 0) * 100) : '—'}
        tone={toneFor(s.realized_pnl_usdc)}
      />
      <Stat
        label="未實現 PnL"
        value={fmtPnlFull(s.unrealized_pnl_usdc)}
        sub={`持倉 ${s.open}`}
        tone={toneFor(s.unrealized_pnl_usdc)}
      />
      <Stat
        label="勝率"
        value={s.closed > 0 ? `${(s.win_rate * 100).toFixed(1)}%` : '—'}
        sub={`${s.wins}W / ${s.losses}L`}
        tone="neutral"
      />
      <Stat
        label="資金使用"
        value={`$${s.open_stake_usdc.toFixed(0)}`}
        sub={`${(s.capital_utilization_pct * 100).toFixed(1)}% of $1k`}
        tone="neutral"
      />
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone: 'neutral' | 'gain' | 'loss';
}) {
  const color =
    tone === 'gain' ? semantic.live : tone === 'loss' ? semantic.error : fg.primary;
  return (
    <div
      style={{
        backgroundColor: layer['01'],
        border: `1px solid ${borderColor.hair}`,
        borderRadius: 8,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 10, color: fg.tertiary, letterSpacing: 0.5, textTransform: 'uppercase' }}>
        {label}
      </div>
      <div
        style={{
          marginTop: 4,
          fontSize: 20,
          fontWeight: 600,
          color,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ marginTop: 2, fontSize: 11, color: fg.tertiary, fontVariantNumeric: 'tabular-nums' }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Tabs
// ─────────────────────────────────────────────────────────────────────
function Tabs({
  tab,
  setTab,
  stats,
}: {
  tab: TabKey;
  setTab: (t: TabKey) => void;
  stats: StatsPayload;
}) {
  const s = stats.summary;
  const tabs: { key: TabKey; label: string; count: number }[] = [
    { key: 'all', label: '全部', count: s.total },
    { key: 'open', label: '持倉中', count: s.open },
    { key: 'closed', label: '已結算', count: s.closed },
  ];
  return (
    <div style={{ display: 'flex', gap: 4, borderBottom: `1px solid ${borderColor.hair}` }}>
      {tabs.map((t) => {
        const active = t.key === tab;
        return (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '8px 14px',
              fontSize: 13,
              fontWeight: active ? 600 : 400,
              color: active ? fg.primary : fg.secondary,
              backgroundColor: 'transparent',
              border: 'none',
              borderBottom: `2px solid ${active ? semantic.live : 'transparent'}`,
              marginBottom: -1,
              cursor: 'pointer',
            }}
          >
            {t.label}
            <span style={{ marginLeft: 6, color: fg.tertiary, fontWeight: 400 }}>{t.count}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Trades Table
// ─────────────────────────────────────────────────────────────────────
function TradesTable({
  trades,
  tab,
  loading,
}: {
  trades: PaperTrade[];
  tab: TabKey;
  loading: boolean;
}) {
  if (loading && trades.length === 0) {
    return (
      <Card>
        <CardBody>
          <div style={{ padding: 40, textAlign: 'center', color: fg.tertiary }}>載入中…</div>
        </CardBody>
      </Card>
    );
  }

  if (trades.length === 0) {
    return (
      <Card>
        <CardHeader title="紙上單" subtitle="尚無資料" divider />
        <CardBody>
          <EmptyState tab={tab} />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="紙上單明細" subtitle={`${trades.length} 筆`} divider />
      <CardBody pad={false}>
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 12,
            }}
          >
            <thead>
              <tr style={{ borderBottom: `1px solid ${borderColor.hair}` }}>
                <Th>市場</Th>
                <Th>Side</Th>
                <Th align="right">進場</Th>
                <Th align="right">{tab === 'closed' ? '出場' : '現價'}</Th>
                <Th align="right">規模 (USDC)</Th>
                <Th align="right">PnL</Th>
                <Th>來源鯨魚</Th>
                <Th>時間</Th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <TradeRow key={t.id} trade={t} />
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

function TradeRow({ trade }: { trade: PaperTrade }) {
  const isOpen = trade.status === 'open';
  const displayPrice = isOpen ? trade.mark_price : trade.exit_price;
  const pnl = isOpen ? trade.unrealized_pnl : trade.realized_pnl;
  const pnlPct = isOpen ? trade.unrealized_pnl_pct : trade.realized_pnl_pct;

  return (
    <tr
      style={{
        borderBottom: `1px solid ${borderColor.hair}`,
      }}
    >
      <Td>
        <div
          style={{
            color: fg.primary,
            display: 'block',
            maxWidth: 360,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={trade.market_question ?? trade.condition_id}
        >
          {trade.market_question ?? trade.condition_id.substring(0, 20) + '…'}
        </div>
        {trade.outcome && (
          <div style={{ fontSize: 10, color: fg.tertiary, marginTop: 2 }}>
            {trade.outcome} · {trade.market_category ?? 'uncategorized'}
          </div>
        )}
      </Td>
      <Td>
        <SideBadge side={trade.side} />
      </Td>
      <Td align="right" mono>
        {fmtPrice(trade.entry_price)}
      </Td>
      <Td align="right" mono>
        {displayPrice != null ? fmtPrice(displayPrice) : <span style={{ color: fg.tertiary }}>—</span>}
      </Td>
      <Td align="right" mono>
        ${trade.entry_notional.toFixed(2)}
      </Td>
      <Td align="right" mono>
        {pnl != null ? (
          <div>
            <div style={{ color: pnlColor(pnl) }}>{fmtPnlSm(pnl)}</div>
            {pnlPct != null && (
              <div style={{ fontSize: 10, color: fg.tertiary }}>
                {fmtPctSigned(pnlPct * 100)}
              </div>
            )}
          </div>
        ) : (
          <span style={{ color: fg.tertiary }}>—</span>
        )}
      </Td>
      <Td>
        <Link
          href={`/polymarket/wallet/${trade.source_wallet}`}
          style={{
            color: semantic.live,
            textDecoration: 'none',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: 11,
          }}
        >
          {shortAddr(trade.source_wallet)}
        </Link>
        {trade.source_tier && (
          <span style={{ marginLeft: 6 }}>
            <TierBadge tier={trade.source_tier} size="sm" />
          </span>
        )}
      </Td>
      <Td>
        <div style={{ fontSize: 11 }}>
          <div style={{ color: fg.primary }}>{fmtShortTime(trade.entry_time)}</div>
          {trade.exit_time && (
            <div style={{ color: fg.tertiary, fontSize: 10 }}>→ {fmtShortTime(trade.exit_time)}</div>
          )}
        </div>
      </Td>
    </tr>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Follower Health — 0 紙上單時的「透明度」區塊
// ─────────────────────────────────────────────────────────────────────
function FollowerHealthBanner({ health }: { health: HealthPayload }) {
  const tierC = health.thresholds_ref.tier_C;
  return (
    <Card accentColor={healthColor(health.health)}>
      <CardHeader
        eyebrow={`⚙️ Follower 狀態 · ${healthLabel(health.health)}`}
        title="為何還沒有紙上單"
        subtitle={
          health.qualifying_whales > 0
            ? `${health.qualifying_whales} 個錢包已進入跟單白名單，等下次交易訊號`
            : `${Object.values(health.tier_distribution).reduce((a, b) => a + b, 0)} 個錢包分類中，0 個達 A/B/C/emerging 門檻`
        }
        divider
      />
      <CardBody>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
          <div style={{ flex: '1 1 300px' }}>
            <div style={{ fontSize: 12, color: fg.secondary, lineHeight: 1.6 }}>
              <p style={{ marginBottom: 8 }}>
                Follower 僅對已分類為 <strong>A / B / C / emerging</strong> tier 的錢包跟單。
                目前鯨魚分布：
              </p>
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {Object.entries(health.tier_distribution)
                  .sort((a, b) => b[1] - a[1])
                  .map(([tier, count]) => (
                    <li
                      key={tier}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        padding: '3px 0',
                      }}
                    >
                      <span>
                        <TierBadge tier={tier} size="sm" />
                      </span>
                      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{count}</span>
                    </li>
                  ))}
              </ul>
            </div>
          </div>

          <div style={{ flex: '2 1 500px' }}>
            <div
              style={{
                fontSize: 11,
                color: fg.tertiary,
                marginBottom: 8,
                textTransform: 'uppercase',
                letterSpacing: 0.5,
              }}
            >
              Tier C 最低門檻 · 距離最近的 {health.near_miss.length} 個錢包
            </div>
            <div style={{ fontSize: 11, color: fg.secondary, marginBottom: 8 }}>
              {tierC.min_trades_90d} trades · winrate ≥{' '}
              {(tierC.min_win_rate * 100).toFixed(0)}% · pnl ≥ ${tierC.min_cumulative_pnl_usdc} ·
              avg ≥ ${tierC.min_avg_trade_size_usdc}
            </div>
            {health.near_miss.length === 0 ? (
              <div style={{ padding: 12, color: fg.tertiary, fontSize: 12 }}>
                無錢包接近門檻 — 系統在等活躍鯨魚累積。
              </div>
            ) : (
              <NearMissList near_miss={health.near_miss.slice(0, 6)} />
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function FollowerHealthCard({ health }: { health: HealthPayload }) {
  const color = healthColor(health.health);
  return (
    <Card accentColor={color}>
      <CardHeader title="Follower 健康度" divider />
      <CardBody>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 10,
          }}
        >
          <span
            style={{
              width: 12,
              height: 12,
              borderRadius: '50%',
              backgroundColor: color,
              display: 'inline-block',
            }}
          />
          <span style={{ fontSize: 13, fontWeight: 500, color: fg.primary }}>
            {healthLabel(health.health)}
          </span>
        </div>
        <div style={{ fontSize: 12, color: fg.secondary, lineHeight: 1.6 }}>
          <div>
            上次觸發：
            <span style={{ color: fg.primary }}>
              {health.last_follower_fire_at
                ? `${formatAge(health.hours_since_last_fire)} 前`
                : '從未'}
            </span>
          </div>
          <div>
            決策累計：
            <span style={{ color: fg.primary, fontVariantNumeric: 'tabular-nums' }}>
              {health.total_decisions}
            </span>
          </div>
          <div>
            白名單：
            <span style={{ color: fg.primary, fontVariantNumeric: 'tabular-nums' }}>
              {health.qualifying_whales} 錢包
            </span>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function NearMissList({ near_miss }: { near_miss: NearMissWhale[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {near_miss.map((w) => (
        <div
          key={w.wallet_address}
          style={{
            padding: '10px 12px',
            borderRadius: 6,
            border: `1px solid ${borderColor.hair}`,
            backgroundColor: layer['01'],
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            flexWrap: 'wrap',
          }}
        >
          <Link
            href={`/polymarket/wallet/${w.wallet_address}`}
            style={{
              fontFamily: 'ui-monospace, monospace',
              fontSize: 11,
              color: semantic.live,
              textDecoration: 'none',
              minWidth: 110,
            }}
          >
            {shortAddr(w.wallet_address)}
          </Link>
          <TierBadge tier={w.tier} size="sm" />
          <div
            style={{
              fontSize: 11,
              color: fg.secondary,
              fontVariantNumeric: 'tabular-nums',
              flex: 1,
            }}
          >
            tr={w.trade_count_90d} · wr={(w.win_rate * 100).toFixed(0)}% · pnl=$
            {Math.round(w.cumulative_pnl).toLocaleString()} · avg=$
            {w.avg_trade_size.toFixed(0)}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {w.misses.length === 0 ? (
              <span
                style={{
                  padding: '3px 8px',
                  borderRadius: 10,
                  fontSize: 10,
                  color: semantic.live,
                  backgroundColor: 'oklch(95% 0.04 150 / 0.15)',
                  border: `1px solid ${semantic.live}`,
                }}
              >
                符合 tier C ✓（等 stability）
              </span>
            ) : (
              w.misses.map((m) => (
                <span
                  key={m.field}
                  style={{
                    padding: '3px 8px',
                    borderRadius: 10,
                    fontSize: 10,
                    color: semantic.error,
                    backgroundColor: 'oklch(95% 0.04 25 / 0.1)',
                    border: `1px solid ${semantic.error}`,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {fieldLabel(m.field)} {m.gap_pct != null ? `-${m.gap_pct.toFixed(0)}%` : '缺'}
                </span>
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function healthColor(h: HealthPayload['health']): string {
  switch (h) {
    case 'green':
      return semantic.live;
    case 'yellow':
      return 'oklch(75% 0.15 80)';
    case 'red':
      return semantic.error;
    case 'dormant':
    default:
      return fg.tertiary;
  }
}

function healthLabel(h: HealthPayload['health']): string {
  switch (h) {
    case 'green':
      return '活躍 (24h 內有觸發)';
    case 'yellow':
      return '緩慢 (1-7 天未觸發)';
    case 'red':
      return '停滯 (> 7 天未觸發)';
    case 'dormant':
    default:
      return '尚未觸發過';
  }
}

function formatAge(hours: number | null): string {
  if (hours == null) return '—';
  if (hours < 1) return `${Math.floor(hours * 60)} 分鐘`;
  if (hours < 24) return `${Math.floor(hours)} 小時`;
  return `${Math.floor(hours / 24)} 天`;
}

function fieldLabel(f: string): string {
  const m: Record<string, string> = {
    trade_count_90d: '筆數',
    win_rate: '勝率',
    cumulative_pnl: 'PnL',
    avg_trade_size: '平均尺寸',
  };
  return m[f] ?? f;
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar breakdowns
// ─────────────────────────────────────────────────────────────────────
function TierBreakdown({ data }: { data: StatsPayload['by_tier'] }) {
  if (data.length === 0) {
    return (
      <Card>
        <CardHeader title="按 Tier 拆分" divider />
        <CardBody>
          <SmallEmpty text="無資料" />
        </CardBody>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title="按 Tier 拆分" subtitle={`${data.length} 層`} divider />
      <CardBody pad={false}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${borderColor.hair}` }}>
              <Th>Tier</Th>
              <Th align="right">筆數</Th>
              <Th align="right">勝率</Th>
              <Th align="right">PnL</Th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.tier} style={{ borderBottom: `1px solid ${borderColor.hair}` }}>
                <Td>
                  <TierBadge tier={row.tier} size="sm" />
                </Td>
                <Td align="right" mono>
                  {row.total}
                </Td>
                <Td align="right" mono>
                  {row.closed > 0 ? `${(row.win_rate * 100).toFixed(0)}%` : '—'}
                </Td>
                <Td align="right" mono>
                  <span style={{ color: pnlColor(row.realized_pnl) }}>
                    {fmtPnlSm(row.realized_pnl)}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

function TopSourceWallets({ data }: { data: StatsPayload['top_source_wallets'] }) {
  if (data.length === 0) {
    return (
      <Card>
        <CardHeader title="來源鯨魚排行" divider />
        <CardBody>
          <SmallEmpty text="無資料" />
        </CardBody>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title="來源鯨魚排行" subtitle="按 PnL 貢獻排序" divider />
      <CardBody pad={false}>
        {data.map((w, i) => (
          <div
            key={w.source_wallet}
            style={{
              padding: '10px 14px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              borderBottom: i < data.length - 1 ? `1px solid ${borderColor.hair}` : 'none',
              fontSize: 12,
            }}
          >
            <div>
              <Link
                href={`/polymarket/wallet/${w.source_wallet}`}
                style={{
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  fontSize: 11,
                  color: semantic.live,
                  textDecoration: 'none',
                }}
              >
                {shortAddr(w.source_wallet)}
              </Link>
              {w.source_tier && (
                <span style={{ marginLeft: 6 }}>
                  <TierBadge tier={w.source_tier} size="sm" />
                </span>
              )}
              <div style={{ marginTop: 2, fontSize: 10, color: fg.tertiary }}>
                {w.closed > 0 ? `${w.wins}W / ${w.closed}` : `${w.trades} 筆 (0 平)`}
              </div>
            </div>
            <div
              style={{
                fontVariantNumeric: 'tabular-nums',
                color: pnlColor(w.realized_pnl),
                fontWeight: 500,
              }}
            >
              {fmtPnlSm(w.realized_pnl)}
            </div>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function FollowerBreakdown({ data }: { data: StatsPayload['by_follower'] }) {
  return (
    <Card>
      <CardHeader title="按 Follower 拆分" divider />
      <CardBody pad={false}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${borderColor.hair}` }}>
              <Th>Follower</Th>
              <Th align="right">Open</Th>
              <Th align="right">勝率</Th>
              <Th align="right">PnL</Th>
            </tr>
          </thead>
          <tbody>
            {data.map((f) => (
              <tr key={f.follower_name} style={{ borderBottom: `1px solid ${borderColor.hair}` }}>
                <Td>
                  <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>
                    {f.follower_name}
                  </span>
                </Td>
                <Td align="right" mono>
                  {f.open}
                </Td>
                <Td align="right" mono>
                  {f.closed > 0 ? `${(f.win_rate * 100).toFixed(0)}%` : '—'}
                </Td>
                <Td align="right" mono>
                  <span style={{ color: pnlColor(f.realized_pnl) }}>
                    {fmtPnlSm(f.realized_pnl)}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────
function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      style={{
        padding: '10px 14px',
        textAlign: align,
        fontSize: 10,
        fontWeight: 500,
        color: fg.tertiary,
        letterSpacing: 0.5,
        textTransform: 'uppercase',
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = 'left',
  mono = false,
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
}) {
  return (
    <td
      style={{
        padding: '10px 14px',
        textAlign: align,
        fontFamily: mono
          ? 'ui-monospace, SFMono-Regular, Menlo, monospace'
          : 'inherit',
        fontVariantNumeric: mono ? 'tabular-nums' : undefined,
        verticalAlign: 'top',
      }}
    >
      {children}
    </td>
  );
}

function SideBadge({ side }: { side: string }) {
  const isBuy = side.toUpperCase() === 'BUY';
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.5,
        color: isBuy ? semantic.live : semantic.error,
        backgroundColor: isBuy ? 'oklch(95% 0.04 150 / 0.15)' : 'oklch(95% 0.04 25 / 0.15)',
        border: `1px solid ${isBuy ? semantic.live : semantic.error}`,
      }}
    >
      {side}
    </span>
  );
}

function EmptyState({ tab }: { tab: TabKey }) {
  const text =
    tab === 'open'
      ? '目前沒有持倉中的紙上單。Follower 要等鯨魚觸發訊號才會進場。'
      : tab === 'closed'
        ? '尚無已結算的紙上單。需等持倉的市場結算後才會關倉。'
        : '尚無任何紙上單。Follower 尚未觸發過 — 可能原因：鯨魚 tier 都是 excluded/volatile。';
  return (
    <div style={{ padding: 40, textAlign: 'center' }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>📭</div>
      <div style={{ color: fg.secondary, fontSize: 13, lineHeight: 1.6, maxWidth: 400, margin: '0 auto' }}>
        {text}
      </div>
    </div>
  );
}

function SmallEmpty({ text }: { text: string }) {
  return (
    <div style={{ padding: 16, textAlign: 'center', color: fg.tertiary, fontSize: 12 }}>
      {text}
    </div>
  );
}

function LoadingBanner() {
  return (
    <div style={{ padding: 16, color: fg.tertiary, fontSize: 13 }}>載入中…</div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      style={{
        marginTop: 12,
        padding: '10px 14px',
        backgroundColor: 'oklch(95% 0.04 25 / 0.15)',
        border: `1px solid ${semantic.error}`,
        borderRadius: 8,
        color: semantic.error,
        fontSize: 12,
      }}
    >
      載入失敗：{message}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// formatters
// ─────────────────────────────────────────────────────────────────────
function toneFor(v: number): 'gain' | 'loss' | 'neutral' {
  if (v > 0) return 'gain';
  if (v < 0) return 'loss';
  return 'neutral';
}

function pnlColor(v: number): string {
  if (v > 0) return semantic.live;
  if (v < 0) return semantic.error;
  return fg.secondary;
}

function fmtPrice(v: number): string {
  return v.toFixed(4);
}

function fmtPnlFull(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  const abs = Math.abs(v);
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPnlSm(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  const abs = Math.abs(v);
  return `${sign}$${abs.toFixed(2)}`;
}

function fmtPctSigned(v: number): string {
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function shortAddr(a: string): string {
  if (a.length <= 10) return a;
  return `${a.substring(0, 6)}…${a.substring(a.length - 4)}`;
}

function fmtShortTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}
