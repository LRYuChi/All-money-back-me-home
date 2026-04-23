'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from '@/components/polymarket/Card';
import { TierBadge } from '@/components/polymarket/TierBadge';
import { ConsistencyTag } from '@/components/polymarket/SpecialistTag';
import { EquityCurveChart, type CurvePoint, type CurveEvent } from '@/components/polymarket/EquityCurveChart';
import { CategoryBreakdownChart } from '@/components/polymarket/CategoryBreakdownChart';
import { TimeSliceBarChart } from '@/components/polymarket/TimeSliceBarChart';
import { ProfileTimeline, type TimelineEvent } from '@/components/polymarket/ProfileTimeline';
import { MetricSparklineGrid, type HistoryEntry } from '@/components/polymarket/MetricSparklineGrid';

interface WalletDetailPayload {
  wallet_address: string;
  stats: {
    tier: string;
    trade_count_90d: number;
    resolved_count: number;
    win_rate: number;
    cumulative_pnl: number;
    avg_trade_size: number;
    last_trade_at: string | null;
    last_computed_at: string | null;
  };
  scanner_version: string | null;
  scanned_at: string | null;
  passed_coarse_filter: boolean | null;
  coarse_filter_reasons: string[];
  archetypes: string[];
  risk_flags: string[];
  sample_size_warning: boolean;
  features: {
    core_stats: Feature | null;
    steady_growth: Feature | null;
    category_specialization: Feature | null;
    time_slice_consistency: Feature | null;
  };
  curve: CurvePoint[];
  events: CurveEvent[];
  recent_trades: RecentTrade[];
  tier_history: TierHistoryRow[];
}

interface Feature {
  feature_version: string | null;
  value: Record<string, unknown> | null;
  confidence: string | null;
  sample_size: number | null;
  notes: string;
}

interface RecentTrade {
  id: string;
  condition_id: string;
  price: number | null;
  size: number | null;
  notional: number | null;
  side: string;
  match_time: string;
  market_question: string | null;
  market_category: string | null;
}

interface TierHistoryRow {
  from_tier: string | null;
  to_tier: string;
  changed_at: string;
  reason: string | null;
}

interface HistoryPayload {
  wallet_address: string;
  count: number;
  profiles: HistoryEntry[];
}

interface TimelinePayload {
  wallet_address: string;
  event_count: number;
  events: TimelineEvent[];
}

export default function WalletDetailPage() {
  const params = useParams<{ address: string }>();
  const address = params?.address ?? '';

  const [data, setData] = useState<WalletDetailPayload | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!address) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, hist, tl] = await Promise.all([
        apiClient.get<WalletDetailPayload>(`/api/polymarket/wallet/${address}`),
        apiClient
          .get<HistoryPayload>(`/api/polymarket/profiles/${address}/history`, {
            params: { limit: '60' },
          })
          .catch(() => ({ wallet_address: address, count: 0, profiles: [] })),
        apiClient
          .get<TimelinePayload>(`/api/polymarket/profiles/${address}/timeline`, {
            params: { limit: '100' },
          })
          .catch(() => ({ wallet_address: address, event_count: 0, events: [] })),
      ]);
      setData(detail);
      setHistory(hist.profiles ?? []);
      setTimeline(tl.events ?? []);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [address]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !data) {
    return <CenteredMessage>讀取錢包資料中…</CenteredMessage>;
  }
  if (error) {
    return <CenteredMessage error>錯誤：{error}</CenteredMessage>;
  }
  if (!data) return null;

  return (
    <main
      style={{
        backgroundColor: layer['00'],
        minHeight: '100vh',
        padding: '24px 32px 48px',
        color: fg.primary,
      }}
    >
      {/* Breadcrumb */}
      <nav style={{ fontSize: '12px', color: fg.tertiary, marginBottom: '16px' }}>
        <Link href="/polymarket" style={{ color: fg.tertiary }}>
          ← 鯨魚目錄
        </Link>
      </nav>

      {/* Header */}
      <WalletHeader data={data} />

      {/* Two-column: curve + side panel */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: '16px',
          marginTop: '16px',
        }}
      >
        <Card>
          <CardHeader
            eyebrow="資金曲線"
            title="累積已實現 PnL"
            subtitle={`${data.curve.length} 天資料 · 來源 steady_growth v${
              data.features.steady_growth?.feature_version ?? '-'
            }`}
            divider
          />
          <CardBody>
            <EquityCurveChart curve={data.curve} events={data.events} height={340} />
          </CardBody>
        </Card>

        <SmoothnessPanel feature={data.features.steady_growth} />
      </div>

      {/* Feature row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '16px',
          marginTop: '16px',
        }}
      >
        <CategoryPanel
          feature={data.features.category_specialization}
          baselineWinRate={data.stats.win_rate}
        />
        <TimeSlicePanel feature={data.features.time_slice_consistency} />
      </div>

      {/* Phase B.2: 指標演進 sparklines */}
      <div style={{ marginTop: '16px' }}>
        <MetricSparklineGrid history={history} />
      </div>

      {/* Phase B.2: 畫像時間線 + tier history（並排） */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '16px',
          marginTop: '16px',
        }}
      >
        <ProfileTimeline events={timeline} />
        <TierHistoryCard history={data.tier_history} />
      </div>

      {/* Recent trades — 獨占一行因為資訊量大 */}
      <div style={{ marginTop: '16px' }}>
        <RecentTradesCard trades={data.recent_trades} />
      </div>
    </main>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function WalletHeader({ data }: { data: WalletDetailPayload }) {
  const { stats, wallet_address } = data;
  const short = `${wallet_address.slice(0, 8)}…${wallet_address.slice(-6)}`;
  const pnlColor =
    stats.cumulative_pnl > 0 ? semantic.live : stats.cumulative_pnl < 0 ? semantic.error : fg.secondary;

  const isSteadyGrower =
    (data.features.steady_growth?.value as { is_steady_grower?: boolean } | null)
      ?.is_steady_grower === true;

  const sv = data.features.steady_growth?.value as Record<string, unknown> | null;
  const smoothness = typeof sv?.smoothness_score === 'number' ? (sv.smoothness_score as number) : null;

  return (
    <Card>
      <div style={{ padding: '20px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <code
            style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: '20px',
              fontWeight: 600,
            }}
          >
            {short}
          </code>
          <TierBadge tier={stats.tier} />
          {isSteadyGrower && (
            <Tag color={semantic.tier} bg={semantic.tierBg} border={semantic.tierBorder}>
              ⭐ 穩健策略源
            </Tag>
          )}
          {data.archetypes.map((a) => (
            <Tag key={a} color={semantic.whale} bg={semantic.whaleBg} border={semantic.whaleBorder}>
              {a}
            </Tag>
          ))}
          {data.risk_flags.map((r) => (
            <Tag key={r} color={semantic.warn} bg={semantic.warnBg} border={semantic.warnBorder}>
              ⚠ {r}
            </Tag>
          ))}
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '16px',
            marginTop: '16px',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <Stat label="累積 PnL" value={formatPnl(stats.cumulative_pnl)} color={pnlColor} />
          <Stat label="勝率" value={`${(stats.win_rate * 100).toFixed(1)}%`} />
          <Stat label="交易數 (90d)" value={stats.trade_count_90d.toLocaleString()} />
          <Stat label="已結算倉位" value={stats.resolved_count.toLocaleString()} />
          <Stat label="平均尺寸" value={`$${Math.round(stats.avg_trade_size).toLocaleString()}`} />
          {smoothness !== null && (
            <Stat label="平滑度" value={smoothness.toFixed(2)} color={semantic.live} />
          )}
        </div>
      </div>
    </Card>
  );
}

function SmoothnessPanel({ feature }: { feature: Feature | null }) {
  if (!feature || !feature.value) {
    return (
      <Card>
        <CardHeader eyebrow="Steady Growth" title="平滑度分析" divider />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: '13px' }}>尚未產出 — 等待下一次掃描</div>
        </CardBody>
      </Card>
    );
  }

  const v = feature.value as {
    is_steady_grower?: boolean;
    smoothness_score?: number;
    components?: {
      r_squared?: number;
      gain_to_pain_ratio?: number;
      gain_to_pain_normalized?: number;
      new_high_frequency_30d?: number;
    };
    max_drawdown_ratio?: number;
    max_drawdown_amount_usdc?: number;
    longest_losing_streak?: number;
    segment_pnls_usdc?: number[];
    all_segments_positive?: boolean;
  };

  const score = v.smoothness_score ?? 0;
  const passThreshold = 0.70;
  const passColor = score >= passThreshold ? semantic.live : semantic.warn;

  return (
    <Card>
      <CardHeader
        eyebrow="Steady Growth"
        title="平滑度分析"
        subtitle={feature.confidence === 'ok' ? 'confidence: ok' : `confidence: ${feature.confidence ?? '—'}`}
        divider
      />
      <CardBody>
        {/* Gauge-like score display */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '12px' }}>
          <span style={{ fontSize: '36px', fontWeight: 600, color: passColor, fontVariantNumeric: 'tabular-nums' }}>
            {score.toFixed(2)}
          </span>
          <span style={{ fontSize: '13px', color: fg.tertiary }}>
            / 1.00 （門檻 {passThreshold.toFixed(2)}）
          </span>
        </div>
        {/* Bar */}
        <div
          style={{
            height: 6,
            background: layer['02'],
            borderRadius: 3,
            marginBottom: '16px',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${Math.min(score, 1) * 100}%`,
              height: '100%',
              background: passColor,
              transition: 'width 400ms ease',
            }}
          />
        </div>

        {/* Components */}
        <Row label="R²（趨勢擬合）" value={v.components?.r_squared?.toFixed(3) ?? '—'} />
        <Row
          label="Gain/Pain"
          value={`${(v.components?.gain_to_pain_ratio ?? 0).toFixed(2)} (${((v.components?.gain_to_pain_normalized ?? 0) * 100).toFixed(0)}%)`}
        />
        <Row
          label="新高頻率 30d"
          value={`${((v.components?.new_high_frequency_30d ?? 0) * 100).toFixed(0)}%`}
        />

        <Divider />

        <Row
          label="最大回撤"
          value={`${((v.max_drawdown_ratio ?? 0) * 100).toFixed(1)}% ($${Math.round(v.max_drawdown_amount_usdc ?? 0).toLocaleString()})`}
        />
        <Row label="最長連敗" value={`${v.longest_losing_streak ?? 0} 場`} />
        <Row
          label="三段 PnL"
          value={(v.segment_pnls_usdc ?? []).map((x) => `$${Math.round(x).toLocaleString()}`).join(' / ') || '—'}
          mono
        />
      </CardBody>
    </Card>
  );
}

function CategoryPanel({ feature, baselineWinRate }: { feature: Feature | null; baselineWinRate: number }) {
  if (!feature || !feature.value) {
    return (
      <Card>
        <CardHeader eyebrow="領域專精" title="Category Specialization" divider />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: '13px' }}>尚未產出</div>
        </CardBody>
      </Card>
    );
  }
  const v = feature.value as {
    categories?: Record<string, { trades?: number; resolved: number; win_rate?: number; is_specialist?: boolean }>;
    primary_category?: string | null;
    specialist_categories?: string[];
    category_count?: number;
  };

  return (
    <Card>
      <CardHeader
        eyebrow="領域專精"
        title="Category Specialization"
        subtitle={`主領域：${v.primary_category ?? '—'} · ${v.specialist_categories?.length ?? 0} 個 specialist`}
        divider
      />
      <CardBody>
        <CategoryBreakdownChart
          categories={v.categories ?? {}}
          baselineWinRate={baselineWinRate}
        />
      </CardBody>
    </Card>
  );
}

function TimeSlicePanel({ feature }: { feature: Feature | null }) {
  if (!feature || !feature.value) {
    return (
      <Card>
        <CardHeader eyebrow="時間切片一致性" title="Time-Slice Consistency" divider />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: '13px' }}>尚未產出</div>
        </CardBody>
      </Card>
    );
  }
  const v = feature.value as {
    segments?: Array<{ index: number; days_back?: [number, number]; resolved: number; win_rate?: number }>;
    win_rate_mean?: number;
    win_rate_std?: number;
    consistent?: boolean | null;
    valid_segments?: number;
  };

  return (
    <Card>
      <CardHeader
        eyebrow="時間切片一致性"
        title="Time-Slice Consistency"
        subtitle={`Std: ${(v.win_rate_std ?? 0).toFixed(3)} · 有效段 ${v.valid_segments ?? 0}/3`}
        trailing={<ConsistencyTag isConsistent={v.consistent ?? null} />}
        divider
      />
      <CardBody>
        <TimeSliceBarChart
          segments={v.segments ?? []}
          meanWinRate={v.win_rate_mean}
          isConsistent={v.consistent ?? null}
        />
      </CardBody>
    </Card>
  );
}

function RecentTradesCard({ trades }: { trades: RecentTrade[] }) {
  return (
    <Card>
      <CardHeader eyebrow="近期交易" title={`最近 ${trades.length} 筆`} divider />
      <CardBody pad={false}>
        {trades.length === 0 && (
          <div style={{ padding: '16px 20px', color: fg.tertiary, fontSize: '13px' }}>無交易記錄</div>
        )}
        <div style={{ maxHeight: 320, overflowY: 'auto' }}>
          {trades.slice(0, 30).map((t) => (
            <div
              key={t.id}
              style={{
                padding: '10px 20px',
                borderBottom: `1px solid ${borderColor.hair}`,
                display: 'grid',
                gridTemplateColumns: '80px 1fr auto',
                gap: 12,
                alignItems: 'center',
                fontSize: 12,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              <span
                style={{
                  color: t.side === 'BUY' ? semantic.yes : semantic.no,
                  fontWeight: 500,
                }}
              >
                {t.side}
              </span>
              <span style={{ color: fg.secondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {t.market_question ?? t.condition_id}
              </span>
              <span style={{ color: fg.primary, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>
                ${Math.round(t.notional ?? 0).toLocaleString()} @ {(t.price ?? 0).toFixed(3)}
              </span>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function TierHistoryCard({ history }: { history: TierHistoryRow[] }) {
  return (
    <Card>
      <CardHeader eyebrow="層級變動" title="Tier History" divider />
      <CardBody pad={false}>
        {history.length === 0 && (
          <div style={{ padding: '16px 20px', color: fg.tertiary, fontSize: '13px' }}>無變動紀錄</div>
        )}
        <div>
          {history.slice(0, 10).map((h, i) => (
            <div
              key={i}
              style={{
                padding: '10px 20px',
                borderBottom: `1px solid ${borderColor.hair}`,
                fontSize: 12,
              }}
            >
              <div style={{ color: fg.secondary }}>
                {h.from_tier ?? '(新)'} → <strong style={{ color: fg.primary }}>{h.to_tier}</strong>
                <span style={{ color: fg.tertiary, marginLeft: 8 }}>{h.reason}</span>
              </div>
              <div style={{ color: fg.tertiary, fontSize: 11 }}>{h.changed_at}</div>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Utility components
// ─────────────────────────────────────────────────────────────────────

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{ color: fg.tertiary, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {label}
      </div>
      <div style={{ color: color ?? fg.primary, fontSize: 20, fontWeight: 500, marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '6px 0',
        fontSize: 13,
        color: fg.secondary,
      }}
    >
      <span>{label}</span>
      <span
        style={{
          color: fg.primary,
          fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </span>
    </div>
  );
}

function Divider() {
  return <div style={{ height: 1, background: borderColor.hair, margin: '10px 0' }} />;
}

function Tag({
  children,
  color,
  bg,
  border,
}: {
  children: React.ReactNode;
  color: string;
  bg: string;
  border: string;
}) {
  return (
    <span
      style={{
        display: 'inline-block',
        fontSize: 11,
        fontWeight: 500,
        padding: '3px 8px',
        borderRadius: 4,
        color,
        backgroundColor: bg,
        border: `1px solid ${border}`,
      }}
    >
      {children}
    </span>
  );
}

function CenteredMessage({ children, error }: { children: React.ReactNode; error?: boolean }) {
  return (
    <div
      style={{
        minHeight: '60vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: layer['00'],
        color: error ? semantic.error : fg.tertiary,
      }}
    >
      {children}
    </div>
  );
}

function formatPnl(v: number): string {
  const sign = v >= 0 ? '+' : '-';
  return `${sign}$${Math.abs(Math.round(v)).toLocaleString()}`;
}
