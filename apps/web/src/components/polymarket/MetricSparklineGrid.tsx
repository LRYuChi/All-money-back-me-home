'use client';

import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardBody, CardHeader } from './Card';

/**
 * Phase B.2 — 關鍵指標隨 scanner 掃描演進的 sparklines.
 *
 * 資料來源：GET /api/polymarket/profiles/{wallet}/history
 *   每個 entry 帶 scanned_at + 各 feature 的摘要值；按時間倒序回傳 (新→舊)，
 *   本元件會 reverse 為 (舊→新) 畫。
 *
 * 不使用 lightweight-charts — 太重；純 SVG 即可。
 */

export interface HistoryEntry {
  scanned_at: string;
  cumulative_pnl: number | null;
  smoothness_score: number | null;
  market_edge: number | null;
  win_rate: number | null;
}

interface MetricSparklineGridProps {
  history: HistoryEntry[];
}

export function MetricSparklineGrid({ history }: MetricSparklineGridProps) {
  if (history.length < 2) {
    return (
      <Card>
        <CardHeader
          eyebrow="指標演進"
          title="Metric Trend"
          subtitle="歷史掃描 < 2 筆，無趨勢可畫"
          divider
        />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: 13 }}>
            scanner 累積多次掃描後，此區會顯示關鍵指標隨時間的變化。
          </div>
        </CardBody>
      </Card>
    );
  }

  // API 回傳是新→舊；反轉為舊→新
  const ordered = [...history].reverse();

  const series: Array<{
    key: keyof HistoryEntry;
    label: string;
    formatter: (v: number) => string;
    positiveIsGood: boolean;
  }> = [
    {
      key: 'cumulative_pnl',
      label: '累積 PnL',
      formatter: (v) => `${v >= 0 ? '+' : '-'}$${Math.abs(Math.round(v)).toLocaleString()}`,
      positiveIsGood: true,
    },
    {
      key: 'smoothness_score',
      label: '平滑度',
      formatter: (v) => v.toFixed(2),
      positiveIsGood: true,
    },
    {
      key: 'market_edge',
      label: 'Market edge',
      formatter: (v) => `${(v * 100).toFixed(1)}%`,
      positiveIsGood: true,
    },
    {
      key: 'win_rate',
      label: '勝率',
      formatter: (v) => `${(v * 100).toFixed(1)}%`,
      positiveIsGood: true,
    },
  ];

  return (
    <Card>
      <CardHeader
        eyebrow="指標演進"
        title="Metric Trend"
        subtitle={`${ordered.length} 次掃描紀錄`}
        divider
      />
      <CardBody>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 12,
          }}
        >
          {series.map((s) => (
            <SparklineTile
              key={s.key}
              label={s.label}
              values={ordered.map((e) => e[s.key] as number | null)}
              formatter={s.formatter}
              positiveIsGood={s.positiveIsGood}
            />
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function SparklineTile({
  label,
  values,
  formatter,
  positiveIsGood,
}: {
  label: string;
  values: (number | null)[];
  formatter: (v: number) => string;
  positiveIsGood: boolean;
}) {
  const clean = values
    .map((v, i) => (v == null ? null : { v, i }))
    .filter((x): x is { v: number; i: number } => x !== null);

  if (clean.length < 2) {
    return (
      <div
        style={{
          backgroundColor: layer['02'],
          border: `1px solid ${borderColor.hair}`,
          borderRadius: 4,
          padding: 10,
        }}
      >
        <div style={{ fontSize: 10, color: fg.tertiary, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          {label}
        </div>
        <div style={{ fontSize: 16, color: fg.tertiary, marginTop: 4 }}>—</div>
        <div style={{ fontSize: 10, color: fg.tertiary, marginTop: 4 }}>資料不足</div>
      </div>
    );
  }

  const first = clean[0].v;
  const last = clean[clean.length - 1].v;
  const delta = last - first;
  const positive = delta > 0;
  const trendColor = positive === positiveIsGood ? semantic.live : semantic.warn;

  return (
    <div
      style={{
        backgroundColor: layer['02'],
        border: `1px solid ${borderColor.hair}`,
        borderRadius: 4,
        padding: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
        }}
      >
        <span
          style={{
            fontSize: 10,
            color: fg.tertiary,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontSize: 10,
            color: trendColor,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {positive ? '▲' : '▼'} {Math.abs(delta).toFixed(2)}
        </span>
      </div>
      <div
        style={{
          fontSize: 16,
          color: fg.primary,
          marginTop: 2,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {formatter(last)}
      </div>
      <Sparkline values={clean.map((x) => x.v)} color={trendColor} />
    </div>
  );
}

function Sparkline({ values, color }: { values: number[]; color: string }) {
  const w = 180;
  const h = 32;
  const pad = 2;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const pts = values
    .map((v, i) => {
      const x = pad + (i / (values.length - 1)) * (w - 2 * pad);
      const y = h - pad - ((v - min) / range) * (h - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{ width: '100%', height: h, marginTop: 6, display: 'block' }}
      aria-hidden
    >
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* 最後一點的 marker */}
      <circle
        cx={pad + (w - 2 * pad)}
        cy={h - pad - ((values[values.length - 1] - min) / range) * (h - 2 * pad)}
        r="2"
        fill={color}
      />
    </svg>
  );
}
