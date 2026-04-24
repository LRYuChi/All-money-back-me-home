'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import { AppShell } from '@/components/layout/AppShell';
import { TickerTape } from '@/components/dashboard/TickerTape';
import { ConfidencePanel } from '@/components/dashboard/ConfidencePanel';
import { MarketTable } from '@/components/dashboard/MarketTable';
import { MacroPanel } from '@/components/dashboard/MacroPanel';
import { BotStatus } from '@/components/dashboard/BotStatus';
import { HeatMap } from '@/components/charts/HeatMap';
import { CryptoEnvPanel } from '@/components/dashboard/CryptoEnvPanel';
import EquityCurve from '@/components/dashboard/EquityCurve';
import SupertrendSignals from '@/components/dashboard/SupertrendSignals';
import { SystemHealthBar } from '@/components/dashboard/SystemHealthBar';

interface DashboardData {
  timestamp: string;
  confidence: {
    score: number;
    regime: string;
    event_multiplier: number;
    sandboxes: Record<string, number>;
    guidance: { position_pct: number; leverage: number; threshold_mult: number };
  };
  crypto: { name: string; price?: number; change_pct?: number; rsi?: number; sparkline?: number[]; error?: string }[];
  trading: {
    capital: number; initial_capital: number; total_pnl: number; total_pnl_pct: number;
    open_positions: number; total_trades: number; win_rate: number;
  };
  macro: {
    vix?: { name: string; price: number; change_pct: number };
    yield_10y?: { name: string; price: number; change_pct: number };
    gold?: { name: string; price: number; change_pct: number };
    oil?: { name: string; price: number; change_pct: number };
    fear_greed?: { value: number; classification: string };
    btc_dominance?: number;
  };
  correlations: Record<string, { value: number; label: string }>;
  freqtrade: { state: string; strategy: string; dry_run?: boolean; trade_count?: number; profit?: number };
  next_killzone: { name: string; active?: boolean; starts_in_hours?: number; utc_start: string };
  crypto_env?: Record<string, {
    score: number;
    regime: string;
    sandboxes: { derivatives: number; onchain: number; sentiment: number };
    factors: Record<string, { score: number; signal: string }>;
  }>;
}

const REFRESH_MS = 5 * 60 * 1000;

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdateDate, setLastUpdateDate] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const d = await apiClient.get<DashboardData>('/api/dashboard');
      setData(d);
      setLastUpdateDate(new Date());
    } catch { /* keep stale */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchData]);

  if (loading && !data) {
    return (
      <AppShell pageTitle="Overview · Trading Dashboard">
        <div style={{ padding: 16 }}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <SkeletonCard />
            <SkeletonCard />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
            <div className="md:col-span-2"><SkeletonCard tall /></div>
            <SkeletonCard tall />
          </div>
          <div className="text-center text-xs text-gray-600 pt-4 animate-pulse">載入儀表板…</div>
        </div>
      </AppShell>
    );
  }
  if (!data) return null;

  // Generate market alerts
  const alerts: { level: 'danger' | 'warning' | 'info'; msg: string }[] = [];
  // VIX spike
  if (data.macro.vix && data.macro.vix.price > 30)
    alerts.push({ level: 'danger', msg: `VIX ${data.macro.vix.price.toFixed(1)} — 市場恐慌，波動極高` });
  else if (data.macro.vix && data.macro.vix.price > 25)
    alerts.push({ level: 'warning', msg: `VIX ${data.macro.vix.price.toFixed(1)} — 波動升高，謹慎交易` });
  // Fear & Greed extreme
  if (data.macro.fear_greed && data.macro.fear_greed.value <= 20)
    alerts.push({ level: 'warning', msg: `極度恐懼 (${data.macro.fear_greed.value}) — 潛在反彈機會` });
  if (data.macro.fear_greed && data.macro.fear_greed.value >= 80)
    alerts.push({ level: 'warning', msg: `極度貪婪 (${data.macro.fear_greed.value}) — 注意回調風險` });
  // BTC big move
  const btcData = data.crypto.find(c => c.name === 'BTC');
  if (btcData && btcData.change_pct && Math.abs(btcData.change_pct) > 5)
    alerts.push({ level: 'danger', msg: `BTC ${btcData.change_pct > 0 ? '暴漲' : '暴跌'} ${btcData.change_pct.toFixed(1)}%` });
  // Confidence regime
  if (data.confidence.regime === 'HIBERNATE')
    alerts.push({ level: 'danger', msg: '信心引擎: 休眠模式 — 所有交易已暫停' });
  else if (data.confidence.regime === 'DEFENSIVE')
    alerts.push({ level: 'warning', msg: '信心引擎: 防禦模式 — 僅允許高品質訊號' });
  // Event overlay
  if (data.confidence.event_multiplier < 1)
    alerts.push({ level: 'info', msg: `事件覆蓋 ×${data.confidence.event_multiplier} — FOMC/CPI 降低風險暴露` });

  // Prepare ticker tape items
  const tickerItems = [
    ...data.crypto.filter(c => c.price).map(c => ({ name: c.name, price: c.price!, change_pct: c.change_pct || 0 })),
    ...(data.macro.vix ? [{ name: 'VIX', price: data.macro.vix.price, change_pct: data.macro.vix.change_pct }] : []),
    ...(data.macro.gold ? [{ name: 'Gold', price: data.macro.gold.price, change_pct: data.macro.gold.change_pct }] : []),
    ...(data.macro.oil ? [{ name: 'Oil', price: data.macro.oil.price, change_pct: data.macro.oil.change_pct }] : []),
  ];

  // Prepare heatmap
  const heatmapData = Object.entries(data.correlations || {}).map(([asset, info]) => ({
    label: `BTC-${asset}`,
    value: info.value,
    detail: info.label,
  }));

  return (
    <AppShell
      pageTitle="Overview · Trading Dashboard"
      dataFreshness={{ lastUpdate: lastUpdateDate, refreshMs: REFRESH_MS, onRefresh: fetchData }}
    >
      {/* Ticker tape sits flush below TopBar, edge-to-edge */}
      <TickerTape items={tickerItems} />

      <div style={{ padding: 16 }} className="space-y-3">
        {/* System Health Bar (crypto + polymarket 雙系統並列) */}
        <SystemHealthBar
          crypto={{
            botState: data.freqtrade.state,
            openPositions: data.trading.open_positions,
            totalPnlPct: data.trading.total_pnl_pct,
            totalTrades: data.trading.total_trades,
          }}
        />

        {/* Alert Banners */}
        {alerts.length > 0 && (
          <div className="space-y-2">
            {alerts.map((a, i) => (
              <AlertBanner key={i} level={a.level} msg={a.msg} />
            ))}
          </div>
        )}

        {/* Row 1: Confidence + Bot Status */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <ConfidencePanel data={data.confidence} />
          </div>
          <BotStatus bot={data.freqtrade} killzone={data.next_killzone} trading={data.trading} />
        </div>

        {/* Row 2: Market Table + Macro */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <MarketTable crypto={data.crypto} macro={data.macro} />
          </div>
          <MacroPanel data={data.macro} />
        </div>

        {/* Row 3: Supertrend Signals + Equity Curve */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <SupertrendSignals />
          <EquityCurve />
        </div>

        {/* Row 4: Crypto Environment + Correlations + Quick Links */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <CryptoEnvPanel data={data.crypto_env || {}} />
          <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
            <h3 className="text-xs font-medium text-gray-400 mb-2 uppercase tracking-wider">跨市場相關性 (30日)</h3>
            <HeatMap data={heatmapData} />
          </div>
          <QuickLinks />
        </div>
      </div>
    </AppShell>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function AlertBanner({ level, msg }: { level: 'danger' | 'warning' | 'info'; msg: string }) {
  const styles = {
    danger: { bg: 'rgba(220, 38, 38, 0.10)', border: 'rgba(220, 38, 38, 0.35)', text: 'rgb(248, 113, 113)' },
    warning: { bg: 'rgba(217, 119, 6, 0.10)', border: 'rgba(217, 119, 6, 0.35)', text: 'rgb(251, 191, 36)' },
    info: { bg: 'rgba(37, 99, 235, 0.10)', border: 'rgba(37, 99, 235, 0.35)', text: 'rgb(96, 165, 250)' },
  }[level];
  return (
    <div
      className="rounded-lg px-4 py-2.5 text-sm flex items-center gap-2.5"
      style={{ backgroundColor: styles.bg, border: `1px solid ${styles.border}`, color: styles.text }}
    >
      <span
        className="inline-block rounded-full"
        style={{ width: '8px', height: '8px', backgroundColor: styles.text, flexShrink: 0 }}
      />
      <span>{msg}</span>
    </div>
  );
}

function QuickLinks() {
  const links = [
    { href: '/polymarket', label: 'Polymarket 情報', sub: 'Phase 1.5c · 鯨魚追蹤', accent: 'oklch(70% 0.18 290)' },
    { href: '/smart-money', label: 'Smart Money (HL)', sub: '鯨魚排行榜 · Hyperliquid', accent: 'oklch(65% 0.20 200)' },
    { href: '/market/crypto', label: '加密貨幣', sub: 'BTC / ETH / SOL', accent: 'oklch(70% 0.16 50)' },
    { href: '/market/us', label: '美股', sub: 'S&P / Nasdaq', accent: 'oklch(65% 0.18 155)' },
    { href: '/market/tw', label: '台股', sub: '加權指數 / 半導體', accent: 'oklch(65% 0.16 250)' },
    { href: '/trades', label: '交易紀錄', sub: '已平倉 / 未平倉', accent: 'oklch(70% 0.10 320)' },
    { href: '/backtest', label: '回測', sub: 'WFO · 多策略', accent: 'oklch(70% 0.13 350)' },
  ];
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
      <h3 className="text-xs font-medium text-gray-400 mb-2 uppercase tracking-wider">快速連結</h3>
      <div className="grid grid-cols-2 gap-1.5">
        {links.map((link) => (
          <Link key={link.href} href={link.href}>
            <div
              className="rounded-md px-2.5 py-2 transition-all hover:bg-gray-800/50 group"
              style={{
                backgroundColor: 'rgba(255,255,255,0.02)',
                border: '1px solid rgba(255,255,255,0.05)',
                borderLeft: `2px solid ${link.accent}`,
              }}
            >
              <div className="text-xs font-medium text-gray-200 group-hover:text-white transition-colors">
                {link.label}
              </div>
              <div className="text-[10px] text-gray-500 mt-0.5 font-mono">
                {link.sub}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function SkeletonCard({ tall }: { tall?: boolean }) {
  return (
    <div
      className="rounded-lg border border-gray-800 bg-gray-900/40 animate-pulse"
      style={{ height: tall ? '180px' : '108px' }}
    />
  );
}
