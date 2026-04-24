'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiClient } from '@/lib/api-client';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';
import { Card, CardHeader, CardBody } from '@/components/polymarket/Card';
import { AppShell } from '@/components/layout/AppShell';

interface StatusPayload {
  configured: boolean;
  reason?: string;
  latest_snapshot_date?: string | null;
  ranking_count?: number;
  wallet_count?: number;
}

interface Ranking {
  rank: number;
  score: number;
  address: string | null;
  tags: string[];
  last_active_at: string | null;
  notes: string | null;
  metrics: Record<string, unknown>;
  ai_analysis: unknown;
}

interface LeaderboardPayload {
  configured: boolean;
  reason?: string;
  snapshot_date?: string | null;
  count?: number;
  rankings?: Ranking[];
}

interface SignalHealthPayload {
  configured: boolean;
  reason?: string;
  checked_at?: string;
  health?: 'green' | 'yellow' | 'red';
  health_reason?: string | null;
  last_activity_at?: string | null;
  density?: Record<string, { paper_open: number; paper_closed: number; skipped: number }>;
  latency_24h?: { n: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null };
  skipped_by_reason_24h?: Record<string, number>;
  positions?: { long: number; short: number; flat: number; distinct_wallets: number };
}

const REFRESH_MS = 60_000;

export default function SmartMoneyPage() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardPayload | null>(null);
  const [signalHealth, setSignalHealth] = useState<SignalHealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, lb, sh] = await Promise.all([
        apiClient.get<StatusPayload>('/api/smart-money/status').catch(() => null),
        apiClient
          .get<LeaderboardPayload>('/api/smart-money/leaderboard', { params: { limit: '50' } })
          .catch(() => null),
        apiClient.get<SignalHealthPayload>('/api/smart-money/signal-health').catch(() => null),
      ]);
      setStatus(s);
      setLeaderboard(lb);
      setSignalHealth(sh);
      setLastUpdate(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  return (
    <AppShell
      pageTitle="Smart Money · Hyperliquid 鯨魚排名"
      dataFreshness={{ lastUpdate, refreshMs: REFRESH_MS, onRefresh: fetchAll }}
    >
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {loading && !status && !leaderboard && <LoadingState />}
        {error && <ErrorBanner message={error} />}

        <div className="flex flex-col" style={{ gap: 12 }}>
          <StatusCard status={status} />
          <SignalHealthCard sh={signalHealth} />
          <LeaderboardTable lb={leaderboard} />
        </div>

        <footer
          className="mt-4 pt-4"
          style={{ borderTop: `1px solid ${borderColor.hair}`, color: fg.tertiary, fontSize: 11 }}
        >
          Smart Money 情報系統 · Hyperliquid 鯨魚排名 · 每 60 秒重新整理
        </footer>
      </div>
    </AppShell>
  );
}

// ─────────────────────────────────────────────────────────────────────

function StatusCard({ status }: { status: StatusPayload | null }) {
  if (!status) {
    return (
      <Card>
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: 13 }}>載入中…</div>
        </CardBody>
      </Card>
    );
  }

  if (!status.configured) {
    return (
      <Card accentColor={semantic.warn}>
        <CardHeader
          eyebrow="系統狀態"
          title="Supabase 未配置"
          subtitle={status.reason ?? 'SUPABASE_URL / SUPABASE_KEY 未設'}
          divider
        />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: 13 }}>
            請在環境變數或 .env 設定 Supabase 連線後重啟 API service，scanner CLI 跑一次後即可在此看到排名。
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card accentColor={semantic.live}>
      <CardHeader
        eyebrow="系統狀態"
        title="Smart Money Pipeline"
        subtitle={`最新快照：${status.latest_snapshot_date ?? '(尚未產出)'}`}
        divider
      />
      <CardBody>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: 16,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <Stat label="快照日期" value={status.latest_snapshot_date ?? '—'} />
          <Stat label="排名筆數" value={(status.ranking_count ?? 0).toLocaleString()} />
          <Stat label="追蹤錢包" value={(status.wallet_count ?? 0).toLocaleString()} />
        </div>
      </CardBody>
    </Card>
  );
}

function SignalHealthCard({ sh }: { sh: SignalHealthPayload | null }) {
  if (!sh) {
    return null;
  }
  if (!sh.configured) {
    return (
      <Card accentColor={semantic.warn}>
        <CardHeader eyebrow="Shadow 訊號管線" title="未配置" divider />
        <CardBody>
          <div style={{ color: fg.tertiary, fontSize: 13 }}>
            {sh.reason ?? 'API 層看不到 Supabase — 檢查 SUPABASE_URL / SUPABASE_KEY'}
          </div>
        </CardBody>
      </Card>
    );
  }

  const healthColor = sh.health === 'green'
    ? semantic.live
    : sh.health === 'yellow'
    ? semantic.warn
    : semantic.error;
  const healthDot: Record<string, string> = {
    green: '🟢',
    yellow: '🟡',
    red: '🔴',
  };
  const healthLabel = sh.health
    ? `${healthDot[sh.health]} ${sh.health.toUpperCase()}`
    : 'UNKNOWN';

  const d1 = sh.density?.['1h'];
  const d6 = sh.density?.['6h'];
  const d24 = sh.density?.['24h'];
  const lat = sh.latency_24h;
  const pos = sh.positions;
  const reasons = sh.skipped_by_reason_24h ?? {};
  const reasonEntries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);

  const lastActivity = sh.last_activity_at ? new Date(sh.last_activity_at) : null;
  const lastActivityMin = lastActivity
    ? Math.round((Date.now() - lastActivity.getTime()) / 60_000)
    : null;

  return (
    <Card accentColor={healthColor}>
      <CardHeader
        eyebrow="Shadow 訊號管線"
        title={healthLabel}
        subtitle={
          sh.health_reason
            ? sh.health_reason
            : lastActivityMin !== null
            ? `最近活動 ${lastActivityMin} 分鐘前`
            : '尚無資料'
        }
        divider
      />
      <CardBody>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
            gap: 12,
            fontVariantNumeric: 'tabular-nums',
            marginBottom: 16,
          }}
        >
          <Stat label="1h 開倉" value={`${d1?.paper_open ?? 0}`} />
          <Stat label="1h 平倉" value={`${d1?.paper_closed ?? 0}`} />
          <Stat label="1h 跳過" value={`${d1?.skipped ?? 0}`} />
          <Stat
            label="6h 活動"
            value={`${(d6?.paper_open ?? 0) + (d6?.paper_closed ?? 0) + (d6?.skipped ?? 0)}`}
          />
          <Stat
            label="24h 活動"
            value={`${(d24?.paper_open ?? 0) + (d24?.paper_closed ?? 0) + (d24?.skipped ?? 0)}`}
          />
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 16,
          }}
        >
          {/* Latency */}
          <div>
            <div
              style={{
                fontSize: 11,
                color: fg.tertiary,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                marginBottom: 8,
              }}
            >
              24h 延遲 (n={lat?.n ?? 0})
            </div>
            {lat && lat.n > 0 ? (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3, 1fr)',
                  gap: 8,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                <LatencyStat label="p50" ms={lat.p50_ms} budget={10_000} />
                <LatencyStat label="p95" ms={lat.p95_ms} budget={10_000} />
                <LatencyStat label="p99" ms={lat.p99_ms} budget={20_000} />
              </div>
            ) : (
              <div style={{ color: fg.tertiary, fontSize: 12 }}>尚無樣本</div>
            )}
          </div>

          {/* Positions */}
          <div>
            <div
              style={{
                fontSize: 11,
                color: fg.tertiary,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                marginBottom: 8,
              }}
            >
              追蹤錢包持倉
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: 8,
                fontVariantNumeric: 'tabular-nums',
                fontSize: 12,
              }}
            >
              <div>
                <div style={{ color: fg.tertiary, fontSize: 10 }}>多</div>
                <div style={{ color: '#22c55e', fontWeight: 600 }}>{pos?.long ?? 0}</div>
              </div>
              <div>
                <div style={{ color: fg.tertiary, fontSize: 10 }}>空</div>
                <div style={{ color: '#ef4444', fontWeight: 600 }}>{pos?.short ?? 0}</div>
              </div>
              <div>
                <div style={{ color: fg.tertiary, fontSize: 10 }}>平</div>
                <div style={{ color: fg.secondary }}>{pos?.flat ?? 0}</div>
              </div>
              <div>
                <div style={{ color: fg.tertiary, fontSize: 10 }}>錢包</div>
                <div style={{ color: fg.primary, fontWeight: 600 }}>{pos?.distinct_wallets ?? 0}</div>
              </div>
            </div>
          </div>
        </div>

        {/* Skip reasons */}
        {reasonEntries.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div
              style={{
                fontSize: 11,
                color: fg.tertiary,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                marginBottom: 8,
              }}
            >
              24h 跳過原因
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 11 }}>
              {reasonEntries.map(([r, n]) => (
                <span
                  key={r}
                  style={{
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: layer['02'],
                    border: `1px solid ${borderColor.hair}`,
                    color: fg.secondary,
                  }}
                >
                  {r}: <span style={{ color: fg.primary, fontWeight: 600 }}>{n}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function LatencyStat({ label, ms, budget }: { label: string; ms: number | null; budget: number }) {
  if (ms === null || ms === undefined) {
    return (
      <div>
        <div style={{ fontSize: 10, color: fg.tertiary }}>{label}</div>
        <div style={{ color: fg.tertiary }}>—</div>
      </div>
    );
  }
  const over = ms > budget;
  return (
    <div>
      <div style={{ fontSize: 10, color: fg.tertiary }}>{label}</div>
      <div style={{ color: over ? '#ef4444' : fg.primary, fontWeight: 600, fontSize: 14 }}>
        {ms.toLocaleString()}ms
      </div>
    </div>
  );
}

function LeaderboardTable({ lb }: { lb: LeaderboardPayload | null }) {
  if (!lb || !lb.configured) {
    return null;
  }
  const rankings = lb.rankings ?? [];

  return (
    <Card>
      <CardHeader
        eyebrow="排行榜"
        title={`Top ${rankings.length}`}
        subtitle={`${lb.snapshot_date ?? '(無快照)'} · 依 score 遞減`}
        divider
      />
      <CardBody pad={false}>
        {rankings.length === 0 && (
          <div style={{ padding: '40px 20px', textAlign: 'center', color: fg.tertiary, fontSize: 13 }}>
            尚無排名資料。請先跑 <code>python -m smart_money.cli.rank --top 50</code>。
          </div>
        )}
        {rankings.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table
              style={{
                width: '100%',
                fontSize: 12,
                fontVariantNumeric: 'tabular-nums',
                borderCollapse: 'separate',
                borderSpacing: 0,
              }}
            >
              <thead>
                <tr
                  style={{
                    color: fg.tertiary,
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                  }}
                >
                  <Th>#</Th>
                  <Th>錢包</Th>
                  <Th right>Score</Th>
                  <Th right>Sortino</Th>
                  <Th right>PF</Th>
                  <Th right>MDD</Th>
                  <Th>標籤</Th>
                  <Th>最近活動</Th>
                </tr>
              </thead>
              <tbody>
                {rankings.map((r, i) => (
                  <LeaderboardRow key={`${r.rank}-${r.address}`} r={r} idx={i} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function LeaderboardRow({ r, idx }: { r: Ranking; idx: number }) {
  const addr = r.address ?? '(missing)';
  const short =
    addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
  const sortino = _metricNum(r.metrics.sortino);
  const pf = _metricNum(r.metrics.pf ?? r.metrics.profit_factor);
  const mdd = _metricNum(r.metrics.mdd ?? r.metrics.max_drawdown);

  return (
    <tr
      style={{
        backgroundColor:
          idx % 2 === 0 ? 'transparent' : 'color-mix(in oklab, white 2%, transparent)',
      }}
    >
      <Td first mono>
        {r.rank}
      </Td>
      <Td mono>
        <span title={addr} style={{ color: semantic.live }}>
          {short}
        </span>
      </Td>
      <Td right mono style={{ color: semantic.live, fontWeight: 500 }}>
        {r.score.toFixed(3)}
      </Td>
      <Td right mono>{sortino !== null ? sortino.toFixed(2) : '—'}</Td>
      <Td right mono>{pf !== null ? pf.toFixed(2) : '—'}</Td>
      <Td right mono style={{ color: mdd !== null && mdd > 0.2 ? semantic.warn : fg.primary }}>
        {mdd !== null ? `${(mdd * 100).toFixed(1)}%` : '—'}
      </Td>
      <Td>
        <TagsCell tags={r.tags} />
      </Td>
      <Td style={{ color: fg.tertiary, fontSize: 11 }}>
        {r.last_active_at ? _formatRelative(r.last_active_at) : '—'}
      </Td>
    </tr>
  );
}

function TagsCell({ tags }: { tags: string[] }) {
  if (!tags.length) return <span style={{ color: fg.tertiary }}>—</span>;
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {tags.slice(0, 3).map((t) => {
        const [color, bg, border] = _tagColors(t);
        return (
          <span
            key={t}
            style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 3,
              color,
              backgroundColor: bg,
              border: `1px solid ${border}`,
            }}
          >
            {t}
          </span>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          color: fg.tertiary,
          fontSize: 10,
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        {label}
      </div>
      <div style={{ color: fg.primary, fontSize: 22, fontWeight: 500, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      style={{
        padding: '10px 12px',
        fontWeight: 500,
        textAlign: right ? 'right' : 'left',
        borderBottom: `1px solid ${borderColor.hair}`,
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  first,
  mono,
  style,
}: {
  children: React.ReactNode;
  right?: boolean;
  first?: boolean;
  mono?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td
      style={{
        padding: '10px 12px',
        paddingLeft: first ? '20px' : '12px',
        borderBottom: `1px solid ${borderColor.hair}`,
        textAlign: right ? 'right' : 'left',
        fontFamily: mono
          ? 'ui-monospace, SFMono-Regular, Menlo, monospace'
          : undefined,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {children}
    </td>
  );
}

function LoadingState() {
  return (
    <div style={{ padding: '40px', textAlign: 'center', color: fg.tertiary, fontSize: 13 }}>
      載入中…
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: '12px 20px',
        marginBottom: 16,
        borderRadius: 4,
        color: semantic.error,
        backgroundColor: semantic.errorBg,
        border: `1px solid ${semantic.errorBorder}`,
        fontSize: 13,
      }}
    >
      錯誤：{message}
    </div>
  );
}

function _metricNum(v: unknown): number | null {
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    const n = parseFloat(v);
    return isNaN(n) ? null : n;
  }
  return null;
}

function _tagColors(tag: string): [string, string, string] {
  if (tag === 'whitelisted') return [semantic.live, semantic.liveBg, semantic.liveBorder];
  if (tag === 'banned') return [semantic.error, semantic.errorBg, semantic.errorBorder];
  if (tag === 'watchlist') return [semantic.warn, semantic.warnBg, semantic.warnBorder];
  return [fg.secondary, layer['02'], borderColor.hair];
}

function _formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 60) return `${mins} 分前`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} 小時前`;
  const days = Math.round(hours / 24);
  return `${days} 天前`;
}
