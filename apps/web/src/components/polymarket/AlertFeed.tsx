'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader } from './Card';
import { TierBadge } from './TierBadge';
import { ConsistencyTag, SpecialistTag } from './SpecialistTag';
import { parseServerDateStr } from './FreshnessIndicator';

interface Alert {
  wallet_address: string;
  tx_hash: string;
  event_index: number;
  tier: string;
  condition_id: string;
  market_question: string;
  market_category?: string;
  side: 'BUY' | 'SELL' | string;
  outcome: string;
  size: number;
  price: number;
  notional: number;
  match_time: string;
  alerted_at: string;
  // 1.5b additions
  specialist_categories?: string[];
  primary_category?: string | null;
  match_specialist?: boolean | null;
  is_consistent?: boolean | null;
}

type TierFilter = 'all' | 'A' | 'B' | 'C' | 'emerging';
type SpecialtyFilter = 'all' | 'specialist' | 'big';

export function AlertFeed({ alerts, windowHours }: { alerts: Alert[]; windowHours: number }) {
  const [tierFilter, setTierFilter] = useState<TierFilter>('all');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [specialtyFilter, setSpecialtyFilter] = useState<SpecialtyFilter>('all');

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const a of alerts) {
      if (a.market_category) set.add(a.market_category);
    }
    return Array.from(set).sort();
  }, [alerts]);

  const filtered = useMemo(() => {
    return alerts.filter((a) => {
      if (tierFilter !== 'all' && a.tier !== tierFilter) return false;
      if (categoryFilter !== 'all' && a.market_category !== categoryFilter) return false;
      if (specialtyFilter === 'specialist' && !(a.specialist_categories?.length ?? 0)) return false;
      if (specialtyFilter === 'big' && a.notional < 10000) return false;
      return true;
    });
  }, [alerts, tierFilter, categoryFilter, specialtyFilter]);

  return (
    <Card>
      <CardHeader
        eyebrow="鯨魚交易推播"
        subtitle={`過去 ${windowHours} 小時 · 顯示 ${filtered.length}/${alerts.length} 筆`}
        divider
      />
      <FilterBar
        tierFilter={tierFilter}
        onTierChange={setTierFilter}
        categoryFilter={categoryFilter}
        onCategoryChange={setCategoryFilter}
        specialtyFilter={specialtyFilter}
        onSpecialtyChange={setSpecialtyFilter}
        categories={categories}
        allCounts={_computeFilterCounts(alerts)}
      />
      {filtered.length === 0 && alerts.length === 0 && <EmptyAlertState />}
      {filtered.length === 0 && alerts.length > 0 && (
        <div style={{ padding: '24px', textAlign: 'center', color: fg.tertiary, fontSize: 13 }}>
          當前過濾條件下無符合推播
        </div>
      )}
      {filtered.length > 0 && (
        <ul style={{ listStyle: 'none' }}>
          {filtered.slice(0, 30).map((a, i) => (
            <AlertRow
              key={`${a.wallet_address}-${a.tx_hash}-${a.event_index}`}
              alert={a}
              first={i === 0}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

function FilterBar({
  tierFilter,
  onTierChange,
  categoryFilter,
  onCategoryChange,
  specialtyFilter,
  onSpecialtyChange,
  categories,
  allCounts,
}: {
  tierFilter: TierFilter;
  onTierChange: (t: TierFilter) => void;
  categoryFilter: string;
  onCategoryChange: (c: string) => void;
  specialtyFilter: SpecialtyFilter;
  onSpecialtyChange: (s: SpecialtyFilter) => void;
  categories: string[];
  allCounts: { tier: Record<string, number>; specialty: Record<string, number> };
}) {
  return (
    <div
      style={{
        padding: '10px 20px',
        borderBottom: `1px solid ${borderColor.hair}`,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 12,
        alignItems: 'center',
        fontSize: 11,
      }}
    >
      <span style={{ color: fg.tertiary, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
        Tier
      </span>
      <Chips
        value={tierFilter}
        options={[
          { key: 'all', label: `全部 (${Object.values(allCounts.tier).reduce((a, b) => a + b, 0)})` },
          { key: 'A', label: `A (${allCounts.tier.A ?? 0})` },
          { key: 'B', label: `B (${allCounts.tier.B ?? 0})` },
          { key: 'C', label: `C (${allCounts.tier.C ?? 0})` },
          { key: 'emerging', label: `Emerging (${allCounts.tier.emerging ?? 0})` },
        ]}
        onChange={(v) => onTierChange(v as TierFilter)}
      />

      {categories.length > 0 && (
        <>
          <span style={{ color: fg.tertiary, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            類別
          </span>
          <select
            value={categoryFilter}
            onChange={(e) => onCategoryChange(e.target.value)}
            style={{
              fontSize: 11,
              padding: '3px 8px',
              borderRadius: 4,
              border: `1px solid ${borderColor.hair}`,
              backgroundColor: layer['02'],
              color: fg.primary,
              fontFamily: 'inherit',
            }}
          >
            <option value="all">全部</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </>
      )}

      <span style={{ color: fg.tertiary, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
        快篩
      </span>
      <Chips
        value={specialtyFilter}
        options={[
          { key: 'all', label: '全部' },
          { key: 'specialist', label: `專家 (${allCounts.specialty.specialist ?? 0})` },
          { key: 'big', label: `大額 (${allCounts.specialty.big ?? 0})` },
        ]}
        onChange={(v) => onSpecialtyChange(v as SpecialtyFilter)}
      />
    </div>
  );
}

function Chips({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ key: string; label: string }>;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {options.map((opt) => {
        const active = value === opt.key;
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onChange(opt.key)}
            style={{
              fontSize: 11,
              padding: '3px 8px',
              borderRadius: 4,
              border: `1px solid ${active ? semantic.live : borderColor.hair}`,
              backgroundColor: active ? semantic.liveBg : 'transparent',
              color: active ? semantic.live : fg.secondary,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function _computeFilterCounts(alerts: Alert[]) {
  const tier: Record<string, number> = {};
  let specialist = 0;
  let big = 0;
  for (const a of alerts) {
    tier[a.tier] = (tier[a.tier] ?? 0) + 1;
    if (a.specialist_categories?.length) specialist++;
    if (a.notional >= 10000) big++;
  }
  return { tier, specialty: { specialist, big } };
}

function EmptyAlertState() {
  return (
    <div
      className="text-center"
      style={{
        padding: '40px 20px',
        color: fg.tertiary,
      }}
    >
      <div style={{ fontSize: '13px', marginBottom: '4px' }}>尚無推播</div>
      <div style={{ fontSize: '11px', opacity: 0.7, maxWidth: '420px', margin: '0 auto' }}>
        Pipeline 需識別出 A/B/C 級鯨魚才會產生推播。當前仍在累積資料。
      </div>
    </div>
  );
}

function AlertRow({ alert, first }: { alert: Alert; first: boolean }) {
  const sideColor = alert.side === 'BUY' ? semantic.yes : semantic.no;
  const big = alert.notional >= 10000;
  const matchTime = parseServerDateStr(alert.match_time);
  const specialists = alert.specialist_categories ?? [];

  return (
    <li
      className="flex items-start gap-3"
      style={{
        padding: '14px 20px',
        borderTop: first ? undefined : `1px solid ${borderColor.hair}`,
        color: fg.primary,
      }}
    >
      <TierBadge tier={alert.tier} size="md" />

      <div className="flex-1 min-w-0">
        {/* 行 1: 方向 + 價格 + 金額 + 大額/specialist tag */}
        <div className="flex items-baseline gap-2 flex-wrap mb-1">
          <span
            style={{
              color: sideColor,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontWeight: 600,
              fontSize: '13px',
            }}
          >
            {alert.side} {alert.outcome || '?'}
          </span>
          <span
            style={{
              color: fg.secondary,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: '12px',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            @ {alert.price?.toFixed(4) ?? '?'}
          </span>
          <span
            style={{
              color: big ? semantic.warn : fg.primary,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: '13px',
              fontWeight: 500,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            ${Math.round(alert.notional ?? 0).toLocaleString()}
          </span>
          {big && (
            <span
              style={{
                padding: '1px 6px',
                borderRadius: '9999px',
                fontSize: '10px',
                color: semantic.warn,
                backgroundColor: layer['02'],
                border: `1px solid color-mix(in oklab, ${semantic.warn} 40%, transparent)`,
              }}
            >
              大額
            </span>
          )}
          {specialists.length > 0 && (
            <SpecialistTag
              specialists={specialists}
              matched={alert.match_specialist ?? null}
              size="xs"
            />
          )}
          {alert.is_consistent !== undefined && alert.is_consistent !== null && (
            <ConsistencyTag isConsistent={alert.is_consistent} size="xs" />
          )}
        </div>

        {/* 行 2: 市場問題 + 類別 chip */}
        <div className="flex items-center gap-2">
          {alert.market_category && (
            <span
              style={{
                color: fg.tertiary,
                fontSize: '11px',
                padding: '1px 6px',
                borderRadius: '9999px',
                border: `1px solid ${borderColor.hair}`,
                whiteSpace: 'nowrap',
              }}
            >
              {alert.market_category}
            </span>
          )}
          <span
            style={{
              color: fg.secondary,
              fontSize: '12px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
              flex: 1,
            }}
          >
            {alert.market_question || '(未知市場)'}
          </span>
        </div>

        {/* 行 3: 錢包 + 時間 */}
        <div
          className="mt-1 flex gap-3 flex-wrap"
          style={{
            color: fg.tertiary,
            fontSize: '11px',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          }}
        >
          <Link
            href={`/polymarket/wallet/${alert.wallet_address}`}
            style={{ color: semantic.live, textDecoration: 'none' }}
          >
            {alert.wallet_address.slice(0, 8)}…{alert.wallet_address.slice(-4)}
          </Link>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>{matchTime ? matchTime.toLocaleString('zh-TW', { hour12: false }) : alert.match_time}</span>
        </div>
      </div>
    </li>
  );
}
