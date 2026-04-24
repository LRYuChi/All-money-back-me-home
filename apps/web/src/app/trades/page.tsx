'use client';

import { useEffect, useMemo, useState, useCallback } from 'react';
import { apiClient } from '@/lib/api-client';
import { AppShell } from '@/components/layout/AppShell';

interface BotMeta {
  state?: string | null;
  dry_run?: boolean | null;
  strategy?: string | null;
  timeframe?: string | null;
  exchange?: string | null;
  trading_mode?: string | null;
  max_open_trades?: number | null;
  stake_amount?: string | number | null;
  stake_currency?: string | null;
  pairs?: string[];
  pairs_count?: number;
  bot_start_timestamp?: number | null;
  bot_start_date?: string | null;
}

interface PaperTradeData {
  capital: number;
  initial_capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  open_positions: OpenPosition[];
  closed_trades: ClosedTrade[];
  win_rate: number;
  total_trades: number;
  winning_trades?: number;
  losing_trades?: number;
  best_pair?: string;
  best_pair_pnl?: number;
  max_drawdown?: number;
  max_drawdown_abs?: number;
  profit_factor?: number | null;
  sharpe?: number | null;
  bot?: BotMeta;
  last_updated: string | null;
}

interface OpenPosition {
  symbol: string;
  direction: string;
  entry_price: number;
  stop_loss: number;
  take_profit_levels?: number[];
  position_size_usd?: number;
  leverage?: number;
  confidence?: number;
  reason?: string;
  entry_time?: string;
  current_rate?: number;
  profit_pct?: number;
  profit_abs?: number;
}

interface ClosedTrade {
  symbol: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  pnl_usd: number;
  exit_reason: string;
  r_multiple?: number;
  duration_bars?: number;
  commission_paid?: number;
  entry_time?: string;
  exit_time?: string;
}

interface PerformanceData {
  total_trades: number;
  win_rate: number;
  total_pnl_usd: number;
  total_pnl_pct: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  profit_factor: number;
}

interface JournalTrade {
  pair: string;
  side: string;
  grade: string;
  entry_price: number;
  exit_price: number;
  confidence_entry: number;
  confidence_exit: number;
  conditions: Record<string, boolean | number>;
  r_multiple: number;
  pnl_pct: number;
  pnl_usd: number;
  duration_min: number;
  exit_reason: string;
  slippage_pct: number;
  entry_ts: string;
  exit_ts: string;
  leverage: number;
  atr_pct: number;
  macro_regime: string;
}

interface JournalData {
  trades: JournalTrade[];
  grade_stats: Record<string, { wins: number; losses: number; total: number; win_rate: number; pnl: number }>;
}

interface EquityPoint {
  ts: string;
  capital: number;
  equity: number;
  unrealized_pnl: number;
}

function fmt(n: number, d = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function pnlClass(v: number): string {
  if (v > 0) return 'text-green-400';
  if (v < 0) return 'text-red-400';
  return 'text-gray-400';
}

function sign(v: number): string {
  return v > 0 ? '+' : '';
}

function shortPair(p: string): string {
  return p.replace(/[:/]USDT.*/, '').replace('/', '');
}

function fmtDuration(min: number): string {
  if (min < 60) return `${Math.round(min)}m`;
  if (min < 1440) return `${Math.floor(min / 60)}h${Math.round(min % 60)}m`;
  return `${Math.floor(min / 1440)}d${Math.floor((min % 1440) / 60)}h`;
}

function fmtRelative(tsMs: number | null | undefined): string {
  if (!tsMs) return '—';
  const diff = (Date.now() - tsMs) / 1000;
  if (diff < 60) return `${Math.round(diff)} 秒前`;
  if (diff < 3600) return `${Math.round(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小時前`;
  return `${Math.round(diff / 86400)} 天前`;
}

function distancePct(current: number, target: number): number {
  if (!current || !target) return 0;
  return ((target - current) / current) * 100;
}

type Tab = 'overview' | 'positions' | 'history' | 'journal' | 'performance';
type SortKey = 'time' | 'pnl' | 'pnl_pct' | 'symbol';

const REFRESH_MS = 60_000;

export default function TradesPage() {
  const [data, setData] = useState<PaperTradeData | null>(null);
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [journal, setJournal] = useState<JournalData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [lastUpdateDate, setLastUpdateDate] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [tradeData, perfData, eqData, journalData] = await Promise.all([
        apiClient.get<PaperTradeData>('/api/dashboard/ft-trades').catch(() =>
          apiClient.get<PaperTradeData>('/api/strategy/trades/paper')
        ),
        apiClient.get<PerformanceData>('/api/strategy/trades/paper/performance').catch(() => null),
        apiClient.get<EquityPoint[]>('/api/strategy/trades/paper/equity-curve').catch(() => []),
        apiClient.get<JournalData>('/api/dashboard/journal').catch(() => ({ trades: [], grade_stats: {} })),
      ]);
      setData(tradeData);
      setPerf(perfData);
      setEquity(eqData);
      setJournal(journalData);
      setLastUpdateDate(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法載入交易資料');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchAll]);

  if (loading) {
    return (
      <AppShell pageTitle="Supertrend · Paper Trading">
        <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>載入交易資料中...</div>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell pageTitle="Supertrend · Paper Trading">
        <div style={{ padding: 40, textAlign: 'center', color: '#f87171' }}>{error}</div>
      </AppShell>
    );
  }

  if (!data) return null;

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: '總覽' },
    { key: 'positions', label: `持倉 (${data.open_positions.length})` },
    { key: 'history', label: `歷史 (${data.closed_trades.length})` },
    { key: 'journal', label: `日誌 (${journal?.trades.length ?? 0})` },
    { key: 'performance', label: '績效分析' },
  ];

  return (
    <AppShell
      pageTitle="Supertrend · Paper Trading"
      dataFreshness={{ lastUpdate: lastUpdateDate, refreshMs: REFRESH_MS, onRefresh: fetchAll }}
    >
      <div style={{ padding: 16 }} className="space-y-4">
        {/* Bot Status Ribbon */}
        {data.bot && <BotStatusRibbon bot={data.bot} />}

        {/* Summary Cards */}
        <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatCard
            label="資金"
            value={`$${fmt(data.capital)}`}
            sub={`初始 $${fmt(data.initial_capital)}`}
          />
          <StatCard
            label="總損益"
            value={`${sign(data.total_pnl)}$${fmt(data.total_pnl)}`}
            valueClass={pnlClass(data.total_pnl)}
            sub={`${sign(data.total_pnl_pct)}${fmt(data.total_pnl_pct)}%`}
            subClass={pnlClass(data.total_pnl_pct)}
          />
          <StatCard
            label="已實現"
            value={`${sign(data.realized_pnl ?? 0)}$${fmt(data.realized_pnl ?? 0)}`}
            valueClass={pnlClass(data.realized_pnl ?? 0)}
            sub={`已平 ${data.closed_trades.length}`}
          />
          <StatCard
            label="未實現"
            value={`${sign(data.unrealized_pnl ?? 0)}$${fmt(data.unrealized_pnl ?? 0)}`}
            valueClass={pnlClass(data.unrealized_pnl ?? 0)}
            sub={`持倉 ${data.open_positions.length}/${data.bot?.max_open_trades ?? '?'}`}
          />
          <StatCard
            label="勝率"
            value={data.total_trades > 0 ? `${fmt(data.win_rate, 1)}%` : '—'}
            sub={
              data.total_trades > 0
                ? `${data.winning_trades ?? 0}W / ${data.losing_trades ?? 0}L`
                : '尚無資料'
            }
          />
          <StatCard
            label="最大回撤"
            value={
              data.max_drawdown && data.max_drawdown > 0
                ? `-${fmt(data.max_drawdown * 100, 1)}%`
                : '—'
            }
            valueClass={data.max_drawdown && data.max_drawdown > 0 ? 'text-red-400' : 'text-white'}
            sub={
              data.max_drawdown_abs && data.max_drawdown_abs > 0
                ? `-$${fmt(data.max_drawdown_abs)}`
                : ''
            }
          />
        </section>

        {/* Tabs */}
        <div className="border-b border-gray-800 flex space-x-1 overflow-x-auto">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors whitespace-nowrap ${
                tab === t.key
                  ? 'bg-gray-800 text-white border-b-2 border-blue-500'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {tab === 'overview' && <OverviewTab data={data} equity={equity} />}
        {tab === 'positions' && <PositionsTab positions={data.open_positions} />}
        {tab === 'history' && <HistoryTab trades={data.closed_trades} />}
        {tab === 'journal' && <JournalTab journal={journal} />}
        {tab === 'performance' && <PerformanceTab perf={perf} data={data} />}
      </div>
    </AppShell>
  );
}

// ------------------------------------------------------------------
// Bot Status Ribbon
// ------------------------------------------------------------------

function BotStatusRibbon({ bot }: { bot: BotMeta }) {
  const running = bot.state === 'running';
  const dryRun = bot.dry_run !== false;

  return (
    <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
      <div className="flex items-center gap-2">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            running ? 'bg-green-400 animate-pulse' : 'bg-red-400'
          }`}
          title={bot.state ?? 'unknown'}
        />
        <span className={running ? 'text-green-300 font-semibold' : 'text-red-300 font-semibold'}>
          {bot.state?.toUpperCase() ?? 'UNKNOWN'}
        </span>
      </div>

      <Chip
        label={dryRun ? 'DRY-RUN' : 'LIVE'}
        tone={dryRun ? 'yellow' : 'red'}
      />

      {bot.strategy && <Meta k="策略" v={bot.strategy} />}
      {bot.exchange && (
        <Meta
          k="交易所"
          v={`${bot.exchange.toUpperCase()}${bot.trading_mode ? ` · ${bot.trading_mode}` : ''}`}
        />
      )}
      {bot.timeframe && <Meta k="週期" v={bot.timeframe} />}
      {bot.pairs_count !== undefined && (
        <Meta
          k="監控"
          v={`${bot.pairs_count} 對`}
          title={bot.pairs?.join(', ')}
        />
      )}
      {bot.max_open_trades !== null && bot.max_open_trades !== undefined && (
        <Meta k="上限" v={`${bot.max_open_trades} 倉`} />
      )}
      {bot.bot_start_timestamp ? (
        <Meta
          k="啟動"
          v={fmtRelative(bot.bot_start_timestamp)}
          title={bot.bot_start_date ?? undefined}
        />
      ) : null}
    </div>
  );
}

function Meta({ k, v, title }: { k: string; v: string; title?: string }) {
  return (
    <span className="flex items-center gap-1.5" title={title}>
      <span className="text-gray-500">{k}</span>
      <span className="text-gray-200 font-medium">{v}</span>
    </span>
  );
}

function Chip({ label, tone }: { label: string; tone: 'yellow' | 'red' | 'green' }) {
  const styles: Record<string, string> = {
    yellow: 'bg-yellow-900/50 text-yellow-300 border-yellow-700/60',
    red: 'bg-red-900/50 text-red-300 border-red-700/60',
    green: 'bg-green-900/50 text-green-300 border-green-700/60',
  };
  return (
    <span className={`px-2 py-0.5 rounded border text-[11px] font-bold ${styles[tone]}`}>
      {label}
    </span>
  );
}

// ------------------------------------------------------------------
// StatCard
// ------------------------------------------------------------------

function StatCard({
  label,
  value,
  sub,
  valueClass = 'text-white',
  subClass = 'text-gray-500',
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  subClass?: string;
}) {
  return (
    <div className="rounded-xl p-3 bg-gray-800/60 border border-gray-700">
      <div className="text-[11px] text-gray-400 mb-1">{label}</div>
      <div className={`text-lg font-bold ${valueClass}`}>{value}</div>
      {sub && <div className={`text-[11px] mt-0.5 ${subClass}`}>{sub}</div>}
    </div>
  );
}

// ------------------------------------------------------------------
// Overview
// ------------------------------------------------------------------

function OverviewTab({ data, equity }: { data: PaperTradeData; equity: EquityPoint[] }) {
  const isEmpty = data.total_trades === 0 && data.open_positions.length === 0;

  if (isEmpty) {
    return <EmptyOverview bot={data.bot} />;
  }

  return (
    <div className="space-y-6">
      {/* Equity Curve */}
      {equity.length > 0 && <EquityCurve equity={equity} />}

      {/* Open positions summary */}
      {data.open_positions.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">
            持倉中 ({data.open_positions.length})
          </h3>
          <div className="space-y-2">
            {data.open_positions.map((pos, i) => (
              <OverviewPositionRow key={i} pos={pos} />
            ))}
          </div>
        </div>
      )}

      {/* Recent closed */}
      <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
        <h3 className="text-lg font-semibold text-white mb-4">近期平倉</h3>
        {data.closed_trades.length === 0 ? (
          <p className="text-gray-500 text-center py-8">尚無已平倉交易</p>
        ) : (
          <div className="space-y-2">
            {data.closed_trades.slice(0, 5).map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between py-2 border-b border-gray-800 last:border-0"
              >
                <div className="flex items-center space-x-3">
                  <span
                    className={`text-xs font-medium px-2 py-0.5 rounded ${
                      t.direction === 'long'
                        ? 'bg-green-900/50 text-green-400'
                        : 'bg-red-900/50 text-red-400'
                    }`}
                  >
                    {t.direction === 'long' ? 'LONG' : 'SHORT'}
                  </span>
                  <span className="text-white font-medium">{shortPair(t.symbol)}</span>
                  <span className="text-gray-500 text-xs">{t.exit_reason}</span>
                </div>
                <div className="text-right">
                  <div className={`font-medium ${pnlClass(t.pnl_usd)}`}>
                    {sign(t.pnl_usd)}${fmt(t.pnl_usd)}
                  </div>
                  <div className={`text-xs ${pnlClass(t.pnl_pct)}`}>
                    {sign(t.pnl_pct)}
                    {fmt(t.pnl_pct)}%
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyOverview({ bot }: { bot?: BotMeta }) {
  const dryRun = bot?.dry_run !== false;
  const hours = bot?.bot_start_timestamp
    ? Math.max(1, Math.round((Date.now() - bot.bot_start_timestamp) / 3600_000))
    : null;

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-8 space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-white mb-1">尚無交易紀錄</h3>
        <p className="text-sm text-gray-400">
          Bot 正在監控訊號；觸發進場條件後交易會自動出現在此。
        </p>
      </div>

      <ul className="text-xs text-gray-400 space-y-1.5 pl-4 list-disc">
        <li>
          目前為 <span className={dryRun ? 'text-yellow-300' : 'text-red-300'}>
            {dryRun ? '紙上模擬盤（dry-run）' : '真實下單（LIVE）'}
          </span>
          {dryRun && '，訊號觸發後會虛擬下單、不動用真實資金'}
        </li>
        {hours !== null && (
          <li>
            Bot 已運行 <span className="text-gray-200 font-medium">{hours} 小時</span>
            {bot?.bot_start_date && `（啟動於 ${bot.bot_start_date}）`}
          </li>
        )}
        {bot?.timeframe && bot?.pairs_count !== undefined && (
          <li>
            掃描 <span className="text-gray-200 font-medium">{bot.pairs_count}</span> 對{' '}
            <span className="text-gray-200 font-medium">{bot.timeframe}</span> K 線
          </li>
        )}
        <li>
          <span className="text-gray-300">SupertrendStrategy 屬於 signal-rare 策略</span>；
          歷史上平均每週 1–3 次訊號，剛啟動後數小時無訊號屬於正常
        </li>
      </ul>

      {bot?.pairs && bot.pairs.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2">監控清單</div>
          <div className="flex flex-wrap gap-1.5">
            {bot.pairs.map((p) => (
              <span
                key={p}
                className="text-[11px] px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700"
              >
                {shortPair(p)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EquityCurve({ equity }: { equity: EquityPoint[] }) {
  const values = equity.map((e) => e.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const first = values[0];
  const last = values[values.length - 1];
  const delta = last - first;
  const deltaPct = first ? (delta / first) * 100 : 0;

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg font-semibold text-white">權益曲線</h3>
        <div className="text-right">
          <div className={`text-sm font-bold ${pnlClass(delta)}`}>
            {sign(delta)}${fmt(delta)} ({sign(deltaPct)}
            {fmt(deltaPct)}%)
          </div>
          <div className="text-xs text-gray-500">{equity.length} 個資料點</div>
        </div>
      </div>
      <div className="h-48 flex items-end space-x-px">
        {equity.map((e, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t ${
              e.equity >= (equity[i - 1]?.equity ?? e.equity)
                ? 'bg-green-500/60'
                : 'bg-red-500/60'
            }`}
            style={{ height: `${((e.equity - min) / range) * 100}%`, minHeight: '2px' }}
            title={`${new Date(e.ts).toLocaleDateString('zh-TW')} — $${fmt(e.equity)}`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-gray-500 mt-2">
        <span>最低 ${fmt(min)}</span>
        <span>最高 ${fmt(max)}</span>
      </div>
    </div>
  );
}

function OverviewPositionRow({ pos }: { pos: OpenPosition }) {
  const profit = pos.profit_pct ?? 0;
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-800 last:border-0">
      <div className="flex items-center space-x-3">
        <span
          className={`text-xs font-medium px-2 py-0.5 rounded ${
            pos.direction === 'long' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
          }`}
        >
          {pos.direction === 'long' ? 'LONG' : 'SHORT'}
        </span>
        <span className="text-white font-medium">{shortPair(pos.symbol)}</span>
        {pos.leverage && pos.leverage > 1 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
            {pos.leverage.toFixed(1)}x
          </span>
        )}
      </div>
      <div className="text-right text-sm">
        <div className={`font-medium ${pnlClass(profit)}`}>
          {sign(profit)}
          {fmt(profit)}%
        </div>
        <div className="text-gray-500 text-xs">
          {pos.current_rate ? `$${fmt(pos.current_rate)}` : `$${fmt(pos.entry_price)}`}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Positions
// ------------------------------------------------------------------

function PositionsTab({ positions }: { positions: OpenPosition[] }) {
  if (positions.length === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        目前沒有未平倉部位
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-700">
      <table className="w-full text-sm">
        <thead className="bg-gray-800/80">
          <tr>
            <th className="text-left px-3 py-3 text-gray-400 font-medium">幣種</th>
            <th className="text-left px-3 py-3 text-gray-400 font-medium">方向</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">進場</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">現價</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">SL (距離)</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">倉位</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">損益</th>
            <th className="text-right px-3 py-3 text-gray-400 font-medium">持有時間</th>
            <th className="text-left px-3 py-3 text-gray-400 font-medium">Tag</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/50">
          {positions.map((pos, idx) => {
            const slDist = pos.current_rate && pos.stop_loss
              ? distancePct(pos.current_rate, pos.stop_loss)
              : null;
            const entryMs = pos.entry_time ? new Date(pos.entry_time).getTime() : null;
            const durationMin = entryMs ? (Date.now() - entryMs) / 60_000 : null;

            return (
              <tr key={idx} className="hover:bg-gray-800/40 transition-colors">
                <td className="px-3 py-3 text-white font-medium">{shortPair(pos.symbol)}</td>
                <td className="px-3 py-3">
                  <span className={pos.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                    {pos.direction === 'long' ? '做多' : '做空'}
                  </span>
                  {pos.leverage && pos.leverage > 1 && (
                    <span className="ml-1 text-[10px] text-gray-400">{pos.leverage.toFixed(1)}x</span>
                  )}
                </td>
                <td className="px-3 py-3 text-right text-gray-300">${fmt(pos.entry_price)}</td>
                <td className="px-3 py-3 text-right text-gray-200">
                  {pos.current_rate ? `$${fmt(pos.current_rate)}` : '—'}
                </td>
                <td className="px-3 py-3 text-right text-red-400/80 whitespace-nowrap">
                  {pos.stop_loss ? `$${fmt(pos.stop_loss)}` : '—'}
                  {slDist !== null && (
                    <span className="text-[10px] text-gray-500 ml-1">
                      ({sign(slDist)}
                      {fmt(slDist, 1)}%)
                    </span>
                  )}
                </td>
                <td className="px-3 py-3 text-right text-gray-300">
                  {pos.position_size_usd ? `$${fmt(pos.position_size_usd)}` : '—'}
                </td>
                <td className={`px-3 py-3 text-right font-medium ${pnlClass(pos.profit_pct ?? 0)}`}>
                  {pos.profit_pct != null ? (
                    <>
                      <div>
                        {sign(pos.profit_pct)}
                        {fmt(pos.profit_pct)}%
                      </div>
                      {pos.profit_abs !== undefined && (
                        <div className="text-[10px] text-gray-500">
                          {sign(pos.profit_abs)}${fmt(pos.profit_abs)}
                        </div>
                      )}
                    </>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="px-3 py-3 text-right text-gray-400 text-xs whitespace-nowrap">
                  {durationMin !== null ? fmtDuration(durationMin) : '—'}
                </td>
                <td className="px-3 py-3 text-gray-400 text-xs max-w-[160px] truncate">
                  {pos.reason || '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------
// History
// ------------------------------------------------------------------

function HistoryTab({ trades }: { trades: ClosedTrade[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('time');
  const [sortDesc, setSortDesc] = useState(true);
  const [filterDir, setFilterDir] = useState<'all' | 'long' | 'short'>('all');
  const [filterResult, setFilterResult] = useState<'all' | 'win' | 'loss'>('all');

  const filtered = useMemo(() => {
    let out = trades;
    if (filterDir !== 'all') out = out.filter((t) => t.direction === filterDir);
    if (filterResult === 'win') out = out.filter((t) => t.pnl_usd > 0);
    if (filterResult === 'loss') out = out.filter((t) => t.pnl_usd <= 0);

    const sorted = [...out].sort((a, b) => {
      let av: number, bv: number;
      switch (sortKey) {
        case 'pnl':
          av = a.pnl_usd;
          bv = b.pnl_usd;
          break;
        case 'pnl_pct':
          av = a.pnl_pct;
          bv = b.pnl_pct;
          break;
        case 'symbol':
          return sortDesc
            ? b.symbol.localeCompare(a.symbol)
            : a.symbol.localeCompare(b.symbol);
        case 'time':
        default:
          av = a.exit_time ? new Date(a.exit_time).getTime() : 0;
          bv = b.exit_time ? new Date(b.exit_time).getTime() : 0;
      }
      return sortDesc ? bv - av : av - bv;
    });
    return sorted;
  }, [trades, sortKey, sortDesc, filterDir, filterResult]);

  const totals = useMemo(() => {
    const wins = filtered.filter((t) => t.pnl_usd > 0);
    const losses = filtered.filter((t) => t.pnl_usd <= 0);
    const totalPnl = filtered.reduce((s, t) => s + t.pnl_usd, 0);
    return {
      count: filtered.length,
      wins: wins.length,
      losses: losses.length,
      totalPnl,
      winRate: filtered.length > 0 ? (wins.length / filtered.length) * 100 : 0,
    };
  }, [filtered]);

  if (trades.length === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        目前沒有已平倉交易
      </div>
    );
  }

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDesc(!sortDesc);
    else {
      setSortKey(k);
      setSortDesc(true);
    }
  }

  function SortHeader({ k, label, align = 'left' }: { k: SortKey; label: string; align?: 'left' | 'right' }) {
    const active = sortKey === k;
    return (
      <th
        className={`px-3 py-3 font-medium cursor-pointer select-none text-${align} ${
          active ? 'text-white' : 'text-gray-400 hover:text-gray-200'
        }`}
        onClick={() => toggleSort(k)}
      >
        {label}
        {active && <span className="ml-1 text-[10px]">{sortDesc ? '↓' : '↑'}</span>}
      </th>
    );
  }

  return (
    <div className="space-y-3">
      {/* Filters & totals */}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <FilterPills
          label="方向"
          value={filterDir}
          options={[
            { k: 'all', v: '全部' },
            { k: 'long', v: '做多' },
            { k: 'short', v: '做空' },
          ]}
          onChange={(v) => setFilterDir(v as typeof filterDir)}
        />
        <FilterPills
          label="結果"
          value={filterResult}
          options={[
            { k: 'all', v: '全部' },
            { k: 'win', v: '獲利' },
            { k: 'loss', v: '虧損' },
          ]}
          onChange={(v) => setFilterResult(v as typeof filterResult)}
        />
        <div className="ml-auto flex items-center gap-4 text-[11px] text-gray-400">
          <span>
            篩選 <span className="text-white font-medium">{totals.count}</span> 筆
          </span>
          <span>
            勝率 <span className="text-white font-medium">{fmt(totals.winRate, 1)}%</span>
          </span>
          <span>
            總 PnL{' '}
            <span className={`font-medium ${pnlClass(totals.totalPnl)}`}>
              {sign(totals.totalPnl)}${fmt(totals.totalPnl)}
            </span>
          </span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-700">
        <table className="w-full text-sm">
          <thead className="bg-gray-800/80">
            <tr>
              <SortHeader k="symbol" label="幣種" />
              <th className="text-left px-3 py-3 text-gray-400 font-medium">方向</th>
              <th className="text-right px-3 py-3 text-gray-400 font-medium">進場</th>
              <th className="text-right px-3 py-3 text-gray-400 font-medium">出場</th>
              <SortHeader k="pnl_pct" label="損益 %" align="right" />
              <SortHeader k="pnl" label="損益 USD" align="right" />
              <th className="text-right px-3 py-3 text-gray-400 font-medium">R</th>
              <th className="text-right px-3 py-3 text-gray-400 font-medium">持有</th>
              <th className="text-left px-3 py-3 text-gray-400 font-medium">原因</th>
              <SortHeader k="time" label="出場時間" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700/50">
            {filtered.map((t, idx) => {
              const durationMin = t.duration_bars ? t.duration_bars : null;
              return (
                <tr key={idx} className="hover:bg-gray-800/40 transition-colors">
                  <td className="px-3 py-3 text-white font-medium">{shortPair(t.symbol)}</td>
                  <td className="px-3 py-3">
                    <span className={t.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                      {t.direction === 'long' ? 'LONG' : 'SHORT'}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-right text-gray-300">${fmt(t.entry_price)}</td>
                  <td className="px-3 py-3 text-right text-gray-300">${fmt(t.exit_price)}</td>
                  <td className={`px-3 py-3 text-right font-medium ${pnlClass(t.pnl_pct)}`}>
                    {sign(t.pnl_pct)}
                    {fmt(t.pnl_pct)}%
                  </td>
                  <td className={`px-3 py-3 text-right font-medium ${pnlClass(t.pnl_usd)}`}>
                    {sign(t.pnl_usd)}${fmt(t.pnl_usd)}
                  </td>
                  <td className={`px-3 py-3 text-right ${pnlClass(t.r_multiple ?? 0)}`}>
                    {t.r_multiple != null ? `${sign(t.r_multiple)}${fmt(t.r_multiple, 1)}R` : '—'}
                  </td>
                  <td className="px-3 py-3 text-right text-gray-400 text-xs whitespace-nowrap">
                    {durationMin ? fmtDuration(durationMin) : '—'}
                  </td>
                  <td className="px-3 py-3 text-gray-400 text-xs">{t.exit_reason}</td>
                  <td className="px-3 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {t.exit_time ? new Date(t.exit_time).toLocaleString('zh-TW') : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterPills<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { k: T; v: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-gray-500">{label}</span>
      {options.map((o) => (
        <button
          key={o.k}
          onClick={() => onChange(o.k)}
          className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
            value === o.k
              ? 'bg-blue-600/70 text-white'
              : 'bg-gray-800 text-gray-400 hover:text-gray-200'
          }`}
        >
          {o.v}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------
// Journal (unchanged)
// ------------------------------------------------------------------

function JournalTab({ journal }: { journal: JournalData | null }) {
  if (!journal || journal.trades.length === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        交易日誌尚無記錄（交易完成後自動寫入）
      </div>
    );
  }

  const gradeColor: Record<string, string> = {
    A: 'bg-purple-600/80 text-purple-100',
    'B+': 'bg-blue-600/80 text-blue-100',
    B: 'bg-gray-600/80 text-gray-100',
  };

  const conditionLabels: Record<string, string> = {
    htf_trend: 'HTF',
    in_ob: 'OB',
    in_fvg: 'FVG',
    confluence: 'Confluence',
    in_ote: 'OTE',
    vwap: 'VWAP',
    strong_structure: 'Strong',
  };

  return (
    <div className="space-y-6">
      {Object.keys(journal.grade_stats).length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(journal.grade_stats).map(([grade, s]) => (
            <div key={grade} className="rounded-xl p-4 bg-gray-800/60 border border-gray-700">
              <div className="flex items-center space-x-2 mb-2">
                <span
                  className={`text-xs font-bold px-2 py-0.5 rounded ${
                    gradeColor[grade] || 'bg-gray-600 text-gray-200'
                  }`}
                >
                  Grade {grade}
                </span>
                <span className="text-gray-400 text-xs">{s.total} 筆</span>
              </div>
              <div className="text-lg font-bold text-white">{s.win_rate}% W</div>
              <div className={`text-sm ${s.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-3">
        {journal.trades.map((t, i) => (
          <div key={i} className="rounded-xl bg-gray-900 border border-gray-800 p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center space-x-2">
                <span className={t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
                  {t.pnl_usd >= 0 ? '🟢' : '🔴'}
                </span>
                <span className="text-white font-medium">{shortPair(t.pair)}</span>
                <span
                  className={`text-xs px-1.5 py-0.5 rounded ${
                    t.side === 'long' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                  }`}
                >
                  {t.side === 'long' ? 'LONG' : 'SHORT'} {t.leverage.toFixed(1)}x
                </span>
                <span
                  className={`text-xs font-bold px-2 py-0.5 rounded ${
                    gradeColor[t.grade] || 'bg-gray-600 text-gray-200'
                  }`}
                >
                  {t.grade}
                </span>
              </div>
              <div className="text-right">
                <div className={`font-bold ${pnlClass(t.pnl_usd)}`}>
                  {sign(t.pnl_usd)}${fmt(t.pnl_usd)} ({sign(t.pnl_pct)}
                  {fmt(t.pnl_pct)}%)
                </div>
                <div className={`text-xs ${pnlClass(t.r_multiple)}`}>
                  {sign(t.r_multiple)}
                  {fmt(t.r_multiple, 1)}R
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-1 mb-2">
              {Object.entries(t.conditions).map(([key, val]) => {
                if (!val || val === 0) return null;
                const label = conditionLabels[key] || key;
                return (
                  <span
                    key={key}
                    className="text-xs px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-300 border border-blue-800/50"
                  >
                    {typeof val === 'number' ? `${label}=${val > 0 ? '+' : ''}${val}` : label}
                  </span>
                );
              })}
              {t.macro_regime && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-300 border border-yellow-800/50">
                  {t.macro_regime}
                </span>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              <span>
                進 ${fmt(t.entry_price)} → 出 ${fmt(t.exit_price)}
              </span>
              <span>
                信心 {(t.confidence_entry * 100).toFixed(0)}% →{' '}
                {(t.confidence_exit * 100).toFixed(0)}%
              </span>
              <span>{fmtDuration(t.duration_min)}</span>
              <span>{t.exit_reason}</span>
              {t.slippage_pct > 0.1 && (
                <span className="text-yellow-400">滑點 {t.slippage_pct.toFixed(2)}%</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Performance
// ------------------------------------------------------------------

function PerformanceTab({
  perf,
  data,
}: {
  perf: PerformanceData | null;
  data: PaperTradeData;
}) {
  const { streaks, byPair } = useMemo(() => computePerfExtras(data.closed_trades), [data.closed_trades]);

  if (!perf || perf.total_trades === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        需要至少一筆已平倉交易才能顯示績效分析
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="勝率" value={`${fmt(perf.win_rate, 1)}%`} />
        <StatCard
          label="盈虧比"
          value={`${fmt(perf.profit_factor)}`}
          valueClass={
            perf.profit_factor >= 1.5
              ? 'text-green-400'
              : perf.profit_factor >= 1
              ? 'text-yellow-400'
              : 'text-red-400'
          }
        />
        <StatCard
          label="總損益"
          value={`${sign(perf.total_pnl_usd)}$${fmt(perf.total_pnl_usd)}`}
          valueClass={pnlClass(perf.total_pnl_usd)}
        />
        <StatCard label="總交易數" value={`${perf.total_trades}`} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="平均獲利" value={`$${fmt(perf.avg_win)}`} valueClass="text-green-400" />
        <StatCard label="平均虧損" value={`$${fmt(perf.avg_loss)}`} valueClass="text-red-400" />
        <StatCard label="最佳交易" value={`$${fmt(perf.best_trade)}`} valueClass="text-green-400" />
        <StatCard label="最差交易" value={`$${fmt(perf.worst_trade)}`} valueClass="text-red-400" />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="連勝 (最長)"
          value={`${streaks.longestWin}`}
          valueClass="text-green-400"
          sub={streaks.current > 0 ? `目前連勝 ${streaks.current}` : ''}
        />
        <StatCard
          label="連敗 (最長)"
          value={`${streaks.longestLoss}`}
          valueClass="text-red-400"
          sub={streaks.current < 0 ? `目前連敗 ${Math.abs(streaks.current)}` : ''}
        />
        <StatCard
          label="最大回撤"
          value={
            data.max_drawdown && data.max_drawdown > 0
              ? `-${fmt(data.max_drawdown * 100, 1)}%`
              : '—'
          }
          valueClass="text-red-400"
          sub={
            data.max_drawdown_abs && data.max_drawdown_abs > 0
              ? `-$${fmt(data.max_drawdown_abs)}`
              : ''
          }
        />
        <StatCard
          label="最佳幣對"
          value={data.best_pair ? shortPair(data.best_pair) : '—'}
          valueClass="text-green-400"
          sub={
            data.best_pair_pnl && data.best_pair_pnl !== 0
              ? `${sign(data.best_pair_pnl)}$${fmt(data.best_pair_pnl)}`
              : ''
          }
        />
      </div>

      {/* 依幣對統計 */}
      {byPair.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">各幣對績效</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-400 border-b border-gray-800">
                  <th className="text-left pb-2">幣種</th>
                  <th className="text-right pb-2">交易數</th>
                  <th className="text-right pb-2">勝率</th>
                  <th className="text-right pb-2">損益</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {byPair.map((p) => (
                  <tr key={p.symbol} className="text-gray-300">
                    <td className="py-2 text-white">{shortPair(p.symbol)}</td>
                    <td className="text-right">{p.total}</td>
                    <td className="text-right">{fmt(p.winRate, 1)}%</td>
                    <td className={`text-right font-medium ${pnlClass(p.pnl)}`}>
                      {sign(p.pnl)}${fmt(p.pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 損益分佈 */}
      {data.closed_trades.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">損益分佈</h3>
          <div className="space-y-1">
            {data.closed_trades.map((t, i) => (
              <div key={i} className="flex items-center space-x-2">
                <span className="text-xs text-gray-500 w-16 shrink-0">
                  {shortPair(t.symbol)}
                </span>
                <div className="flex-1 flex items-center">
                  <div
                    className={`h-4 rounded ${t.pnl_usd >= 0 ? 'bg-green-500/60' : 'bg-red-500/60'}`}
                    style={{
                      width: `${Math.min(Math.abs(t.pnl_pct) * 5, 100)}%`,
                      minWidth: '4px',
                    }}
                  />
                </div>
                <span className={`text-xs w-20 text-right ${pnlClass(t.pnl_pct)}`}>
                  {sign(t.pnl_pct)}
                  {fmt(t.pnl_pct)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function computePerfExtras(trades: ClosedTrade[]) {
  // Streaks (chronological)
  const sorted = [...trades].sort((a, b) => {
    const ta = a.exit_time ? new Date(a.exit_time).getTime() : 0;
    const tb = b.exit_time ? new Date(b.exit_time).getTime() : 0;
    return ta - tb;
  });
  let longestWin = 0;
  let longestLoss = 0;
  let current = 0; // positive = consecutive wins, negative = consecutive losses
  let curWinRun = 0;
  let curLossRun = 0;
  for (const t of sorted) {
    if (t.pnl_usd > 0) {
      curWinRun += 1;
      curLossRun = 0;
      longestWin = Math.max(longestWin, curWinRun);
      current = curWinRun;
    } else if (t.pnl_usd < 0) {
      curLossRun += 1;
      curWinRun = 0;
      longestLoss = Math.max(longestLoss, curLossRun);
      current = -curLossRun;
    }
  }

  // Per-pair
  const map = new Map<string, { total: number; wins: number; pnl: number }>();
  for (const t of trades) {
    const m = map.get(t.symbol) ?? { total: 0, wins: 0, pnl: 0 };
    m.total += 1;
    if (t.pnl_usd > 0) m.wins += 1;
    m.pnl += t.pnl_usd;
    map.set(t.symbol, m);
  }
  const byPair = Array.from(map.entries())
    .map(([symbol, m]) => ({
      symbol,
      total: m.total,
      wins: m.wins,
      pnl: m.pnl,
      winRate: (m.wins / m.total) * 100,
    }))
    .sort((a, b) => b.pnl - a.pnl);

  return { streaks: { longestWin, longestLoss, current }, byPair };
}
