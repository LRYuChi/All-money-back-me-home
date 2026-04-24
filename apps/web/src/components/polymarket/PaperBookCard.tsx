'use client';

import Link from 'next/link';
import { borderColor, fg, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from './Card';

/**
 * 主頁 HighlightCards 下方的紙上跟單摘要卡片。
 * 一眼看出：目前跟單 PnL、持倉數、勝率、距離上次觸發多久。
 *
 * 點擊進 /polymarket/paper-trades 看完整表格與細節。
 */

export interface PaperBookSummary {
  initial_capital_usdc: number;
  summary: {
    total: number;
    open: number;
    closed: number;
    wins: number;
    losses: number;
    win_rate: number;
    realized_pnl_usdc: number;
    realized_pnl_pct: number;
    unrealized_pnl_usdc: number;
    combined_pnl_usdc: number;
    combined_pnl_pct_of_capital: number;
    closed_stake_usdc: number;
    open_stake_usdc: number;
    total_stake_usdc: number;
    capital_utilization_pct: number;
  };
  last_follower_fire_at: string | null;
}

interface PaperBookCardProps {
  data: PaperBookSummary | null;
}

export function PaperBookCard({ data }: PaperBookCardProps) {
  const s = data?.summary;
  const capital = data?.initial_capital_usdc ?? 1000;
  const combined = s?.combined_pnl_usdc ?? 0;
  const isGain = combined > 0;
  const isLoss = combined < 0;

  return (
    <Card accentColor={isGain ? semantic.live : isLoss ? semantic.error : semantic.stale}>
      <CardHeader
        eyebrow="📘 Paper Book"
        title="紙上跟單"
        subtitle={
          data
            ? `起始資金 $${capital.toLocaleString()} · 持倉 ${s?.open ?? 0} · 已平 ${s?.closed ?? 0}`
            : '初始化中…'
        }
        divider
      />
      <CardBody>
        {!data && <EmptyLine text="載入中…" />}
        {data && s && (
          <>
            <Row label="Combined PnL" value={
              <span
                style={{
                  color: pnlColor(combined),
                  fontVariantNumeric: 'tabular-nums',
                  fontWeight: 600,
                }}
              >
                {fmtPnlFull(combined)}
                <span
                  style={{
                    fontSize: 11,
                    color: fg.tertiary,
                    marginLeft: 6,
                    fontWeight: 400,
                  }}
                >
                  ({fmtPctSigned((s.combined_pnl_pct_of_capital ?? 0) * 100)})
                </span>
              </span>
            } />
            <Row
              label="已實現"
              value={
                <span
                  style={{
                    color: pnlColor(s.realized_pnl_usdc),
                    fontSize: 12,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {fmtPnlFull(s.realized_pnl_usdc)}
                </span>
              }
            />
            <Row
              label="未實現"
              value={
                <span
                  style={{
                    color: pnlColor(s.unrealized_pnl_usdc),
                    fontSize: 12,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {fmtPnlFull(s.unrealized_pnl_usdc)}
                </span>
              }
            />
            <Row
              label="勝率"
              value={
                <span
                  style={{
                    fontSize: 12,
                    color: fg.primary,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {s.closed > 0
                    ? `${(s.win_rate * 100).toFixed(1)}% (${s.wins}/${s.closed})`
                    : '—'}
                </span>
              }
            />
            <Row
              label="資金使用"
              value={
                <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                  <span style={{ color: fg.primary }}>
                    ${s.open_stake_usdc.toFixed(0)}
                  </span>
                  <span style={{ color: fg.tertiary, marginLeft: 4 }}>
                    ({(s.capital_utilization_pct * 100).toFixed(1)}%)
                  </span>
                </span>
              }
            />
            <Row
              label="上次觸發"
              value={
                <span style={{ fontSize: 12, color: fg.secondary }}>
                  {relTime(data.last_follower_fire_at)}
                </span>
              }
            />
            <div
              style={{
                marginTop: 12,
                paddingTop: 12,
                borderTop: `1px solid ${borderColor.hair}`,
                textAlign: 'right',
              }}
            >
              <Link
                href="/polymarket/paper-trades"
                style={{ fontSize: 12, color: semantic.live, textDecoration: 'none' }}
              >
                查看全部紙上單 →
              </Link>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '4px 0',
        fontSize: 13,
      }}
    >
      <span style={{ color: fg.secondary }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div style={{ padding: '12px 0', textAlign: 'center', color: fg.tertiary, fontSize: 12 }}>
      {text}
    </div>
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

function relTime(iso: string | null): string {
  if (!iso) return '從未觸發';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diffSec = Math.floor((Date.now() - t) / 1000);
  if (diffSec < 60) return `${diffSec} 秒前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分鐘前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小時前`;
  return `${Math.floor(diffSec / 86400)} 天前`;
}
