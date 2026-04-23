'use client';

import Link from 'next/link';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from './Card';
import { TierBadge } from './TierBadge';

/**
 * Phase A3 — 主頁三張亮點卡片：
 *   1. Top movers — 最近 24h tier 升階錢包
 *   2. Emerging whales — emerging tier 錢包
 *   3. Steady growers — is_steady_grower=true 錢包
 *
 * 每一張都獨立載入 API，失敗不影響其他卡片。
 */

export interface TierMover {
  wallet_address: string;
  from_tier: string | null;
  to_tier: string;
  changed_at: string;
  reason: string | null;
  cumulative_pnl: number;
  win_rate: number;
  trade_count_90d: number;
}

export interface EmergingWhale {
  wallet_address: string;
  tier: string;
  trade_count_90d: number;
  win_rate: number;
  cumulative_pnl: number;
  avg_trade_size: number;
  resolved_count: number;
  last_trade_at: string | null;
}

export interface SteadyGrower {
  wallet_address: string;
  tier: string;
  cumulative_pnl: number;
  win_rate: number;
  trade_count_90d: number;
  resolved_count: number;
  smoothness_score: number;
  max_drawdown_ratio: number;
}

interface HighlightCardsProps {
  movers: TierMover[];
  emerging: EmergingWhale[];
  growers: SteadyGrower[];
  windowHours: number;
}

export function HighlightCards({ movers, emerging, growers, windowHours }: HighlightCardsProps) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
        gap: '16px',
      }}
    >
      <TopMoversCard movers={movers} windowHours={windowHours} />
      <EmergingWhalesCard emerging={emerging} />
      <SteadyGrowersCard growers={growers} />
    </div>
  );
}

function TopMoversCard({ movers, windowHours }: { movers: TierMover[]; windowHours: number }) {
  return (
    <Card accentColor={semantic.tier}>
      <CardHeader
        eyebrow="🚀 Top Movers"
        title="最近晉升"
        subtitle={`過去 ${windowHours} 小時 · ${movers.length} 筆`}
        divider
      />
      <CardBody pad={false}>
        {movers.length === 0 && <Empty text="無晉升紀錄" />}
        {movers.map((m) => (
          <Row
            key={`${m.wallet_address}-${m.changed_at}`}
            wallet={m.wallet_address}
            left={
              <span style={{ fontSize: 12, color: fg.secondary }}>
                <TierLabel tier={m.from_tier ?? '(新)'} /> →{' '}
                <strong style={{ color: fg.primary }}>
                  <TierLabel tier={m.to_tier} />
                </strong>
              </span>
            }
            right={
              <span style={{ fontSize: 11, color: pnlColor(m.cumulative_pnl) }}>
                {fmtPnl(m.cumulative_pnl)}
              </span>
            }
          />
        ))}
      </CardBody>
    </Card>
  );
}

function EmergingWhalesCard({ emerging }: { emerging: EmergingWhale[] }) {
  return (
    <Card accentColor={semantic.live}>
      <CardHeader
        eyebrow="🌱 Emerging"
        title="新崛起鯨魚"
        subtitle={`${emerging.length} 個 emerging tier 錢包`}
        divider
      />
      <CardBody pad={false}>
        {emerging.length === 0 && <Empty text="暫無崛起候選" />}
        {emerging.slice(0, 10).map((w) => (
          <Row
            key={w.wallet_address}
            wallet={w.wallet_address}
            left={
              <span style={{ fontSize: 11, color: fg.tertiary }}>
                {w.trade_count_90d} 筆 · {(w.win_rate * 100).toFixed(0)}% 勝率
              </span>
            }
            right={
              <span style={{ fontSize: 11, color: pnlColor(w.cumulative_pnl) }}>
                {fmtPnl(w.cumulative_pnl)}
              </span>
            }
          />
        ))}
      </CardBody>
    </Card>
  );
}

function SteadyGrowersCard({ growers }: { growers: SteadyGrower[] }) {
  return (
    <Card accentColor={semantic.whale}>
      <CardHeader
        eyebrow="📈 Steady Growers"
        title="穩健策略源"
        subtitle={`${growers.length} 個曲線平滑度達標`}
        divider
      />
      <CardBody pad={false}>
        {growers.length === 0 && <Empty text="暫無穩健策略源" />}
        {growers.slice(0, 10).map((g) => (
          <Row
            key={g.wallet_address}
            wallet={g.wallet_address}
            left={
              <span style={{ fontSize: 11, color: fg.tertiary }}>
                平滑度{' '}
                <strong style={{ color: semantic.live }}>{g.smoothness_score.toFixed(2)}</strong>
                {' · '}DD {(g.max_drawdown_ratio * 100).toFixed(1)}%
              </span>
            }
            right={
              <span style={{ fontSize: 11, color: pnlColor(g.cumulative_pnl) }}>
                {fmtPnl(g.cumulative_pnl)}
              </span>
            }
          />
        ))}
      </CardBody>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Row / utilities
// ─────────────────────────────────────────────────────────────────────

function Row({
  wallet,
  left,
  right,
}: {
  wallet: string;
  left?: React.ReactNode;
  right?: React.ReactNode;
}) {
  const short = `${wallet.slice(0, 6)}…${wallet.slice(-4)}`;
  return (
    <Link
      href={`/polymarket/wallet/${wallet}`}
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr auto',
        gap: 10,
        padding: '10px 20px',
        borderBottom: `1px solid ${borderColor.hair}`,
        textDecoration: 'none',
        color: fg.primary,
        alignItems: 'center',
      }}
    >
      <code
        style={{
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontSize: 12,
          color: semantic.live,
        }}
      >
        {short}
      </code>
      <span>{left}</span>
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{right}</span>
    </Link>
  );
}

function TierLabel({ tier }: { tier: string }) {
  return <TierBadge tier={tier} size="sm" />;
}

function Empty({ text }: { text: string }) {
  return (
    <div style={{ padding: '20px', textAlign: 'center', color: fg.tertiary, fontSize: 13 }}>
      {text}
    </div>
  );
}

function pnlColor(v: number): string {
  if (v > 0) return semantic.live;
  if (v < 0) return semantic.error;
  return fg.secondary;
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  return `${sign}$${Math.abs(Math.round(v)).toLocaleString()}`;
}
