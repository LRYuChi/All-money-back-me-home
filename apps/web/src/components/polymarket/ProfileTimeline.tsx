'use client';

import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardBody, CardHeader } from './Card';

/**
 * Phase B.2 — wallet_profiles 時序事件時間線.
 *
 * 資料來源：GET /api/polymarket/profiles/{wallet}/timeline
 * 事件類型：tier_change / archetype_added / archetype_removed
 *          risk_flag_added / risk_flag_removed / metric_shift
 */

export interface TimelineEvent {
  at: string;
  type: string;
  // Tier change
  from?: string;
  to?: string;
  // Archetype
  archetype?: string;
  // Risk flag
  flag?: string;
  // Metric shift
  metric?: string;
  delta?: number;
}

export function ProfileTimeline({ events }: { events: TimelineEvent[] }) {
  return (
    <Card>
      <CardHeader
        eyebrow="畫像時間線"
        title="Profile State Changes"
        subtitle={events.length === 0 ? '尚無事件' : `${events.length} 個事件（最新在上）`}
        divider
      />
      <CardBody pad={false}>
        {events.length === 0 && (
          <div style={{ padding: '24px 20px', color: fg.tertiary, fontSize: 13, textAlign: 'center' }}>
            wallet_profiles 歷史不足，無事件可推。scanner 需累積 ≥ 2 次掃描才能產出 diff。
          </div>
        )}
        {events.length > 0 && (
          <ol style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 400, overflowY: 'auto' }}>
            {[...events].reverse().map((e, i) => (
              <TimelineRow key={`${e.at}-${e.type}-${i}`} event={e} />
            ))}
          </ol>
        )}
      </CardBody>
    </Card>
  );
}

function TimelineRow({ event }: { event: TimelineEvent }) {
  const { icon, color, title, subtitle } = _renderMeta(event);
  const dateStr = _formatDate(event.at);

  return (
    <li
      style={{
        padding: '10px 20px',
        borderBottom: `1px solid ${borderColor.hair}`,
        display: 'grid',
        gridTemplateColumns: '28px 1fr auto',
        gap: 10,
        alignItems: 'center',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 28,
          height: 28,
          borderRadius: 14,
          backgroundColor: layer['02'],
          border: `1px solid ${color}`,
          color,
          fontSize: 13,
        }}
        aria-label={event.type}
      >
        {icon}
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, color: fg.primary }}>{title}</div>
        {subtitle && (
          <div style={{ fontSize: 11, color: fg.tertiary, marginTop: 2 }}>{subtitle}</div>
        )}
      </div>
      <span
        style={{
          fontSize: 11,
          color: fg.tertiary,
          fontVariantNumeric: 'tabular-nums',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        }}
      >
        {dateStr}
      </span>
    </li>
  );
}

function _renderMeta(e: TimelineEvent): {
  icon: string;
  color: string;
  title: string;
  subtitle?: string;
} {
  switch (e.type) {
    case 'tier_change': {
      const up = _tierRank(e.to) > _tierRank(e.from);
      return {
        icon: up ? '↑' : '↓',
        color: up ? semantic.live : semantic.warn,
        title: `Tier ${up ? '晉升' : '降級'}`,
        subtitle: `${e.from ?? '(新)'} → ${e.to}`,
      };
    }
    case 'archetype_added':
      return {
        icon: '+',
        color: semantic.tier,
        title: '新增 archetype',
        subtitle: _archetypeLabel(e.archetype ?? ''),
      };
    case 'archetype_removed':
      return {
        icon: '−',
        color: semantic.stale,
        title: '移除 archetype',
        subtitle: _archetypeLabel(e.archetype ?? ''),
      };
    case 'risk_flag_added':
      return {
        icon: '⚠',
        color: semantic.warn,
        title: '新增風險旗標',
        subtitle: _riskLabel(e.flag ?? ''),
      };
    case 'risk_flag_removed':
      return {
        icon: '✓',
        color: semantic.live,
        title: '移除風險旗標',
        subtitle: _riskLabel(e.flag ?? ''),
      };
    case 'metric_shift': {
      const positive = (e.delta ?? 0) > 0;
      return {
        icon: positive ? '↑' : '↓',
        color: positive ? semantic.live : semantic.warn,
        title: `${_metricLabel(e.metric ?? '')} 顯著變化`,
        subtitle: `${e.from} → ${e.to}  (${positive ? '+' : ''}${e.delta})`,
      };
    }
    default:
      return { icon: '•', color: fg.tertiary, title: e.type };
  }
}

function _archetypeLabel(a: string): string {
  const map: Record<string, string> = {
    steady_grower: '穩健成長 (steady_grower)',
    domain_specialist: '領域專家 (domain_specialist)',
    consistent_trader: '一致性交易者 (consistent_trader)',
    alpha_hunter: 'Alpha 獵手 (alpha_hunter)',
  };
  return map[a] ?? a;
}

function _riskLabel(r: string): string {
  const map: Record<string, string> = {
    concentration_high: '集中度過高 (concentration_high)',
    loss_loading: '近期失效中 (loss_loading)',
    wash_trade_suspicion: '疑似對敲 (wash_trade_suspicion)',
  };
  return map[r] ?? r;
}

function _metricLabel(m: string): string {
  const map: Record<string, string> = {
    cumulative_pnl: '累積 PnL',
    smoothness_score: '平滑度',
    market_edge: 'Market edge',
  };
  return map[m] ?? m;
}

function _tierRank(t?: string): number {
  const order: Record<string, number> = {
    A: 5,
    B: 4,
    C: 3,
    emerging: 2,
    volatile: 1,
    excluded: 0,
  };
  return order[t ?? ''] ?? -1;
}

function _formatDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes()
  ).padStart(2, '0')}`;
}
