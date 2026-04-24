'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from '@/components/polymarket/Card';
import { TierBadge } from '@/components/polymarket/TierBadge';
import { AppShell } from '@/components/layout/AppShell';

/**
 * /polymarket/paper-trades/[id] — 單筆紙上跟單詳情
 *
 * 呈現：
 *   1. 進出場時間 / 價格 / 規模 / PnL（realized 或 unrealized）
 *   2. 關聯市場快照 + 來源鯨魚
 *   3. 關聯 follower_decision（reason + proposed stake）
 */

interface PaperTradeFull {
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
  status: 'open' | 'closed';
  mark_price: number | null;
  mark_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  market_closed: boolean | null;
  market_active: boolean | null;
  market_end_date: string | null;
  market_slug: string | null;
  token_winner: boolean | null;
  created_at: string;
  updated_at: string;
}

interface DecisionRow {
  id: number;
  follower_name: string;
  decided_at: string;
  source_wallet: string;
  source_tier: string | null;
  decision: string;
  reason: string | null;
  proposed_stake_pct: number | null;
  proposed_size_usdc: number | null;
}

interface SourceWhale {
  wallet_address: string;
  tier: string;
  trade_count_90d: number;
  win_rate: number;
  cumulative_pnl: number;
  avg_trade_size: number;
  resolved_count: number;
  last_trade_at: string | null;
}

interface DetailPayload {
  trade: PaperTradeFull;
  decision: DecisionRow | null;
  source_whale: SourceWhale | null;
}

export default function PaperTradeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const [data, setData] = useState<DetailPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!id) return;
    try {
      const d = await apiClient.get<DetailPayload>(
        `/api/polymarket/paper-trades/${id}`
      );
      setData(d);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchDetail();
    const timer = setInterval(fetchDetail, 15_000);
    return () => clearInterval(timer);
  }, [fetchDetail]);

  return (
    <AppShell pageTitle={`Paper Trade #${id ?? ''}`}>
      <div style={{ padding: 16 }}>
        <Link
          href="/polymarket/paper-trades"
          style={{ fontSize: 11, color: semantic.live, textDecoration: 'none' }}
        >
          ← 回紙上單列表
        </Link>

        {loading && !data && (
          <div style={{ padding: 40, color: fg.tertiary, fontSize: 13 }}>載入中…</div>
        )}
        {error && (
          <div
            style={{
              padding: '10px 14px',
              backgroundColor: 'oklch(95% 0.04 25 / 0.15)',
              border: `1px solid ${semantic.error}`,
              borderRadius: 8,
              color: semantic.error,
              fontSize: 12,
            }}
          >
            載入失敗：{error}
          </div>
        )}

        {data && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)',
              gap: 16,
              marginTop: 16,
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <TradeSummary trade={data.trade} />
              <MarketSnapshot trade={data.trade} />
              {data.decision && <DecisionCard decision={data.decision} />}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {data.source_whale && <SourceWhaleCard whale={data.source_whale} />}
              <TimelineCard trade={data.trade} />
            </div>
          </div>
        )}

        <footer
          className="mt-8 pt-4"
          style={{
            borderTop: `1px solid ${borderColor.hair}`,
            color: fg.tertiary,
            fontSize: 11,
          }}
        >
          紙上跟單細節 · 絕無真實下單
        </footer>
      </div>
    </AppShell>
  );
}

function TradeSummary({ trade }: { trade: PaperTradeFull }) {
  const isOpen = trade.status === 'open';
  const pnl = isOpen ? trade.unrealized_pnl : trade.realized_pnl;
  const pnlPct = isOpen ? trade.unrealized_pnl_pct : trade.realized_pnl_pct;
  const displayPrice = isOpen ? trade.mark_price : trade.exit_price;

  return (
    <Card accentColor={pnl == null ? semantic.stale : pnl >= 0 ? semantic.live : semantic.error}>
      <CardHeader
        eyebrow={isOpen ? '持倉中' : `已結算 · ${trade.exit_reason ?? ''}`}
        title={trade.market_question ?? trade.condition_id}
        subtitle={`${trade.outcome ?? '—'} · ${trade.market_category ?? 'uncategorized'}`}
        divider
      />
      <CardBody>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 16,
          }}
        >
          <Kv label="Side" value={<SideBadge side={trade.side} />} />
          <Kv label="Follower" value={trade.follower_name} mono />
          <Kv label="進場價" value={fmtPrice(trade.entry_price)} mono />
          <Kv
            label={isOpen ? '現價' : '出場價'}
            value={displayPrice != null ? fmtPrice(displayPrice) : '—'}
            mono
          />
          <Kv label="數量" value={trade.entry_size.toFixed(4)} mono />
          <Kv label="規模 (USDC)" value={`$${trade.entry_notional.toFixed(2)}`} mono />
          <Kv
            label={isOpen ? '未實現 PnL' : '已實現 PnL'}
            value={
              pnl != null ? (
                <span style={{ color: pnlColor(pnl) }}>
                  {fmtPnlFull(pnl)}{' '}
                  {pnlPct != null && (
                    <span style={{ fontSize: 11, color: fg.tertiary }}>
                      ({fmtPctSigned(pnlPct * 100)})
                    </span>
                  )}
                </span>
              ) : (
                '—'
              )
            }
            mono
          />
          <Kv label="狀態" value={trade.status === 'open' ? '持倉中' : '已結算'} />
        </div>
      </CardBody>
    </Card>
  );
}

function MarketSnapshot({ trade }: { trade: PaperTradeFull }) {
  return (
    <Card>
      <CardHeader title="市場快照" divider />
      <CardBody>
        <Kv label="Condition ID" value={trade.condition_id} mono small />
        <Kv label="Token ID" value={trade.token_id ?? '—'} mono small />
        <Kv
          label="狀態"
          value={
            trade.market_closed ? (
              <span style={{ color: fg.tertiary }}>已結算</span>
            ) : trade.market_active ? (
              <span style={{ color: semantic.live }}>活躍中</span>
            ) : (
              <span style={{ color: fg.tertiary }}>非活躍</span>
            )
          }
        />
        <Kv label="截止日" value={trade.market_end_date ?? '—'} />
        <Kv
          label="Winner"
          value={
            trade.token_winner === true
              ? '此 token 勝'
              : trade.token_winner === false
                ? '此 token 敗'
                : '未結算'
          }
        />
        {trade.market_slug && (
          <Kv
            label="Polymarket"
            value={
              <a
                href={`https://polymarket.com/market/${trade.market_slug}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: semantic.live, textDecoration: 'none', fontSize: 11 }}
              >
                在 Polymarket 開啟 ↗
              </a>
            }
          />
        )}
      </CardBody>
    </Card>
  );
}

function DecisionCard({ decision }: { decision: DecisionRow }) {
  return (
    <Card>
      <CardHeader
        title="Follower 決策"
        subtitle={decision.decision === 'follow' ? '跟單' : decision.decision}
        divider
      />
      <CardBody>
        <Kv label="時間" value={fmtDateTime(decision.decided_at)} />
        <Kv label="Follower" value={decision.follower_name} mono />
        <Kv
          label="決策"
          value={
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 600,
                color: decision.decision === 'follow' ? semantic.live : fg.tertiary,
                border: `1px solid ${decision.decision === 'follow' ? semantic.live : borderColor.hair}`,
              }}
            >
              {decision.decision}
            </span>
          }
        />
        {decision.reason && <Kv label="理由" value={decision.reason} />}
        {decision.proposed_stake_pct != null && (
          <Kv
            label="建議倉位"
            value={`${(decision.proposed_stake_pct * 100).toFixed(1)}% ($${decision.proposed_size_usdc?.toFixed(2) ?? '—'})`}
            mono
          />
        )}
      </CardBody>
    </Card>
  );
}

function SourceWhaleCard({ whale }: { whale: SourceWhale }) {
  return (
    <Card accentColor={semantic.whale}>
      <CardHeader title="來源鯨魚" divider />
      <CardBody>
        <div style={{ marginBottom: 10 }}>
          <Link
            href={`/polymarket/wallet/${whale.wallet_address}`}
            style={{
              fontFamily: 'ui-monospace, monospace',
              fontSize: 12,
              color: semantic.live,
              textDecoration: 'none',
              wordBreak: 'break-all',
            }}
          >
            {whale.wallet_address}
          </Link>
          <div style={{ marginTop: 6 }}>
            <TierBadge tier={whale.tier} size="sm" />
          </div>
        </div>
        <Kv label="90d 交易數" value={whale.trade_count_90d.toString()} mono />
        <Kv label="勝率" value={`${(whale.win_rate * 100).toFixed(1)}%`} mono />
        <Kv label="累積 PnL" value={fmtPnlFull(whale.cumulative_pnl)} mono />
        <Kv label="平均尺寸" value={`$${whale.avg_trade_size.toFixed(2)}`} mono />
        <Kv label="已結算" value={whale.resolved_count.toString()} mono />
        <Kv label="最新交易" value={fmtDateTime(whale.last_trade_at)} />
      </CardBody>
    </Card>
  );
}

function TimelineCard({ trade }: { trade: PaperTradeFull }) {
  const events: { label: string; time: string | null; tone?: 'gain' | 'loss' | 'neutral' }[] = [
    { label: '進場', time: trade.entry_time },
    { label: '寫入 DB', time: trade.created_at },
  ];
  if (trade.exit_time) {
    events.push({
      label: `結算 (${trade.exit_reason ?? ''})`,
      time: trade.exit_time,
      tone: trade.realized_pnl != null && trade.realized_pnl >= 0 ? 'gain' : 'loss',
    });
  }

  return (
    <Card>
      <CardHeader title="時間軸" divider />
      <CardBody>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {events.map((e) => (
            <div key={e.label} style={{ fontSize: 12 }}>
              <div style={{ color: fg.tertiary, fontSize: 10, letterSpacing: 0.3 }}>
                {e.label}
              </div>
              <div
                style={{
                  color:
                    e.tone === 'gain'
                      ? semantic.live
                      : e.tone === 'loss'
                        ? semantic.error
                        : fg.primary,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {fmtDateTime(e.time)}
              </div>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function Kv({
  label,
  value,
  mono = false,
  small = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  small?: boolean;
}) {
  return (
    <div style={{ padding: '4px 0', fontSize: 12 }}>
      <div style={{ color: fg.tertiary, fontSize: 10, letterSpacing: 0.3 }}>{label}</div>
      <div
        style={{
          color: fg.primary,
          fontFamily: mono
            ? 'ui-monospace, SFMono-Regular, Menlo, monospace'
            : 'inherit',
          fontSize: small ? 10 : 12,
          fontVariantNumeric: mono ? 'tabular-nums' : undefined,
          wordBreak: 'break-all',
        }}
      >
        {value}
      </div>
    </div>
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

function pnlColor(v: number): string {
  if (v > 0) return semantic.live;
  if (v < 0) return semantic.error;
  return fg.secondary;
}

function fmtPnlFull(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  const abs = Math.abs(v);
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPctSigned(v: number): string {
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function fmtPrice(v: number): string {
  return v.toFixed(4);
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return iso;
  }
}
