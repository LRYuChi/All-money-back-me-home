'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '@/lib/api-client';

interface PaperTradeData {
  capital: number;
  initial_capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  open_positions: OpenPosition[];
  closed_trades: ClosedTrade[];
  win_rate: number;
  total_trades: number;
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

type Tab = 'overview' | 'positions' | 'history' | 'journal' | 'performance';

export default function TradesPage() {
  const [data, setData] = useState<PaperTradeData | null>(null);
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [journal, setJournal] = useState<JournalData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');

  const fetchAll = useCallback(async () => {
    try {
      const [tradeData, perfData, eqData, journalData] = await Promise.all([
        // Prefer Freqtrade live data, fallback to scanner paper trades
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
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法載入交易資料');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 60_000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="text-gray-400 text-lg animate-pulse">載入交易資料中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="text-red-400 text-lg">{error}</div>
      </div>
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
    <div className="space-y-6">
      {/* Header */}
      <section className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">模擬交易</h1>
          <p className="text-gray-500 text-sm mt-1">
            {data.last_updated
              ? `最後更新：${new Date(data.last_updated).toLocaleString('zh-TW')}`
              : 'Paper Trading'}
          </p>
        </div>
        <div className="text-right">
          <div className="text-sm text-gray-400">淨值</div>
          <div className="text-2xl font-bold text-white">${fmt(data.capital)}</div>
          <div className={`text-sm ${pnlClass(data.total_pnl)}`}>
            {sign(data.total_pnl)}${fmt(data.total_pnl)} ({sign(data.total_pnl_pct)}{fmt(data.total_pnl_pct)}%)
          </div>
        </div>
      </section>

      {/* Summary Cards */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="資金" value={`$${fmt(data.capital)}`} sub={`初始 $${fmt(data.initial_capital)}`} />
        <StatCard
          label="總損益"
          value={`${sign(data.total_pnl)}$${fmt(data.total_pnl)}`}
          valueClass={pnlClass(data.total_pnl)}
          sub={`${sign(data.total_pnl_pct)}${fmt(data.total_pnl_pct)}%`}
          subClass={pnlClass(data.total_pnl_pct)}
        />
        <StatCard label="勝率" value={`${fmt(data.win_rate, 1)}%`} />
        <StatCard label="總交易數" value={`${data.total_trades}`} />
      </section>

      {/* Tabs */}
      <div className="border-b border-gray-800 flex space-x-1">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
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
  );
}

// ------------------------------------------------------------------
// Components
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
    <div className="rounded-xl p-4 bg-gray-800/60 border border-gray-700">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className={`text-xl font-bold ${valueClass}`}>{value}</div>
      {sub && <div className={`text-xs mt-1 ${subClass}`}>{sub}</div>}
    </div>
  );
}

function OverviewTab({ data, equity }: { data: PaperTradeData; equity: EquityPoint[] }) {
  return (
    <div className="space-y-6">
      {/* Equity Curve */}
      {equity.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">權益曲線</h3>
          <div className="h-48 flex items-end space-x-px">
            {(() => {
              const values = equity.map((e) => e.equity);
              const min = Math.min(...values);
              const max = Math.max(...values);
              const range = max - min || 1;
              return equity.map((e, i) => (
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
              ));
            })()}
          </div>
        </div>
      )}

      {/* Recent Trades */}
      <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
        <h3 className="text-lg font-semibold text-white mb-4">近期交易</h3>
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
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                    t.direction === 'long' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                  }`}>
                    {t.direction === 'long' ? 'LONG' : 'SHORT'}
                  </span>
                  <span className="text-white font-medium">{t.symbol}</span>
                  <span className="text-gray-500 text-xs">{t.exit_reason}</span>
                </div>
                <div className="text-right">
                  <div className={`font-medium ${pnlClass(t.pnl_usd)}`}>
                    {sign(t.pnl_usd)}${fmt(t.pnl_usd)}
                  </div>
                  <div className={`text-xs ${pnlClass(t.pnl_pct)}`}>
                    {sign(t.pnl_pct)}{fmt(t.pnl_pct)}%
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Open Positions Summary */}
      {data.open_positions.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">
            持倉中 ({data.open_positions.length})
          </h3>
          <div className="space-y-2">
            {data.open_positions.map((pos, i) => (
              <div key={i} className="flex items-center justify-between py-2">
                <div className="flex items-center space-x-3">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                    pos.direction === 'long' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                  }`}>
                    {pos.direction === 'long' ? 'LONG' : 'SHORT'}
                  </span>
                  <span className="text-white font-medium">{pos.symbol}</span>
                </div>
                <div className="text-right text-sm">
                  <div className="text-gray-300">入場 ${fmt(pos.entry_price)}</div>
                  <div className="text-gray-500">SL ${fmt(pos.stop_loss)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

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
            <th className="text-left px-4 py-3 text-gray-400 font-medium">幣種</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">方向</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">進場價格</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">停損</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">現價</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">倉位</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">損益</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">原因</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">時間</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/50">
          {positions.map((pos, idx) => (
            <tr key={idx} className="hover:bg-gray-800/40 transition-colors">
              <td className="px-4 py-3 text-white font-medium">{pos.symbol}</td>
              <td className="px-4 py-3">
                <span className={pos.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                  {pos.direction === 'long' ? '做多' : '做空'}
                </span>
              </td>
              <td className="px-4 py-3 text-right text-gray-300">${fmt(pos.entry_price)}</td>
              <td className="px-4 py-3 text-right text-red-400/70">
                {pos.stop_loss ? `$${fmt(pos.stop_loss)}` : '—'}
              </td>
              <td className="px-4 py-3 text-right text-green-400/70">
                {pos.current_rate ? `$${fmt(pos.current_rate)}` : pos.take_profit_levels?.length ? `$${fmt(pos.take_profit_levels[0])}` : '—'}
              </td>
              <td className="px-4 py-3 text-right text-gray-300">
                {pos.position_size_usd ? `$${fmt(pos.position_size_usd)}${pos.leverage ? ` ${pos.leverage.toFixed(1)}x` : ''}` : '—'}
              </td>
              <td className={`px-4 py-3 text-right font-medium ${pnlClass(pos.profit_pct ?? 0)}`}>
                {pos.profit_pct != null ? `${sign(pos.profit_pct)}${fmt(pos.profit_pct)}%` : pos.confidence ? `${(pos.confidence * 100).toFixed(0)}%` : '—'}
              </td>
              <td className="px-4 py-3 text-gray-400 text-xs max-w-[200px] truncate">
                {pos.reason || '—'}
              </td>
              <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                {pos.entry_time ? new Date(pos.entry_time).toLocaleString('zh-TW') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTab({ trades }: { trades: ClosedTrade[] }) {
  if (trades.length === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        目前沒有已平倉交易
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-700">
      <table className="w-full text-sm">
        <thead className="bg-gray-800/80">
          <tr>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">幣種</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">方向</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">進場</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">出場</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">損益 %</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">損益 USD</th>
            <th className="text-right px-4 py-3 text-gray-400 font-medium">R</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">原因</th>
            <th className="text-left px-4 py-3 text-gray-400 font-medium">出場時間</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/50">
          {trades.map((t, idx) => (
            <tr key={idx} className="hover:bg-gray-800/40 transition-colors">
              <td className="px-4 py-3 text-white font-medium">{t.symbol}</td>
              <td className="px-4 py-3">
                <span className={t.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                  {t.direction === 'long' ? 'LONG' : 'SHORT'}
                </span>
              </td>
              <td className="px-4 py-3 text-right text-gray-300">${fmt(t.entry_price)}</td>
              <td className="px-4 py-3 text-right text-gray-300">${fmt(t.exit_price)}</td>
              <td className={`px-4 py-3 text-right font-medium ${pnlClass(t.pnl_pct)}`}>
                {sign(t.pnl_pct)}{fmt(t.pnl_pct)}%
              </td>
              <td className={`px-4 py-3 text-right font-medium ${pnlClass(t.pnl_usd)}`}>
                {sign(t.pnl_usd)}${fmt(t.pnl_usd)}
              </td>
              <td className={`px-4 py-3 text-right ${pnlClass(t.r_multiple ?? 0)}`}>
                {t.r_multiple != null ? `${sign(t.r_multiple)}${fmt(t.r_multiple, 1)}R` : '—'}
              </td>
              <td className="px-4 py-3 text-gray-400 text-xs">{t.exit_reason}</td>
              <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                {t.exit_time ? new Date(t.exit_time).toLocaleString('zh-TW') : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JournalTab({ journal }: { journal: JournalData | null }) {
  if (!journal || journal.trades.length === 0) {
    return (
      <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center text-gray-500">
        交易日誌尚無記錄（交易完成後自動寫入）
      </div>
    );
  }

  const gradeColor: Record<string, string> = {
    'A': 'bg-purple-600/80 text-purple-100',
    'B+': 'bg-blue-600/80 text-blue-100',
    'B': 'bg-gray-600/80 text-gray-100',
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
      {/* Grade Stats */}
      {Object.keys(journal.grade_stats).length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(journal.grade_stats).map(([grade, s]) => (
            <div key={grade} className="rounded-xl p-4 bg-gray-800/60 border border-gray-700">
              <div className="flex items-center space-x-2 mb-2">
                <span className={`text-xs font-bold px-2 py-0.5 rounded ${gradeColor[grade] || 'bg-gray-600 text-gray-200'}`}>
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

      {/* Journal Entries */}
      <div className="space-y-3">
        {journal.trades.map((t, i) => (
          <div key={i} className="rounded-xl bg-gray-900 border border-gray-800 p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center space-x-2">
                <span className={t.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
                  {t.pnl_usd >= 0 ? '🟢' : '🔴'}
                </span>
                <span className="text-white font-medium">{t.pair.replace('/USDT:USDT', '')}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${t.side === 'long' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'}`}>
                  {t.side === 'long' ? 'LONG' : 'SHORT'} {t.leverage.toFixed(1)}x
                </span>
                <span className={`text-xs font-bold px-2 py-0.5 rounded ${gradeColor[t.grade] || 'bg-gray-600 text-gray-200'}`}>
                  {t.grade}
                </span>
              </div>
              <div className="text-right">
                <div className={`font-bold ${pnlClass(t.pnl_usd)}`}>
                  {sign(t.pnl_usd)}${fmt(t.pnl_usd)} ({sign(t.pnl_pct)}{fmt(t.pnl_pct)}%)
                </div>
                <div className={`text-xs ${pnlClass(t.r_multiple)}`}>
                  {sign(t.r_multiple)}{fmt(t.r_multiple, 1)}R
                </div>
              </div>
            </div>

            {/* Entry conditions */}
            <div className="flex flex-wrap gap-1 mb-2">
              {Object.entries(t.conditions).map(([key, val]) => {
                if (!val || val === 0) return null;
                const label = conditionLabels[key] || key;
                return (
                  <span key={key} className="text-xs px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-300 border border-blue-800/50">
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

            {/* Details */}
            <div className="flex items-center space-x-4 text-xs text-gray-500">
              <span>進 ${fmt(t.entry_price)} → 出 ${fmt(t.exit_price)}</span>
              <span>信心 {(t.confidence_entry * 100).toFixed(0)}% → {(t.confidence_exit * 100).toFixed(0)}%</span>
              <span>{t.duration_min >= 60 ? `${Math.floor(t.duration_min / 60)}h${Math.round(t.duration_min % 60)}m` : `${Math.round(t.duration_min)}m`}</span>
              <span>{t.exit_reason}</span>
              {t.slippage_pct > 0.1 && <span className="text-yellow-400">滑點 {t.slippage_pct.toFixed(2)}%</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PerformanceTab({
  perf,
  data,
}: {
  perf: PerformanceData | null;
  data: PaperTradeData;
}) {
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
          valueClass={perf.profit_factor >= 1.5 ? 'text-green-400' : perf.profit_factor >= 1 ? 'text-yellow-400' : 'text-red-400'}
        />
        <StatCard
          label="總損益"
          value={`${sign(perf.total_pnl_usd)}$${fmt(perf.total_pnl_usd)}`}
          valueClass={pnlClass(perf.total_pnl_usd)}
        />
        <StatCard label="總交易數" value={`${perf.total_trades}`} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="平均獲利"
          value={`$${fmt(perf.avg_win)}`}
          valueClass="text-green-400"
        />
        <StatCard
          label="平均虧損"
          value={`$${fmt(perf.avg_loss)}`}
          valueClass="text-red-400"
        />
        <StatCard
          label="最佳交易"
          value={`$${fmt(perf.best_trade)}`}
          valueClass="text-green-400"
        />
        <StatCard
          label="最差交易"
          value={`$${fmt(perf.worst_trade)}`}
          valueClass="text-red-400"
        />
      </div>

      {/* Win/Loss distribution */}
      {data.closed_trades.length > 0 && (
        <div className="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <h3 className="text-lg font-semibold text-white mb-4">損益分佈</h3>
          <div className="space-y-1">
            {data.closed_trades.map((t, i) => (
              <div key={i} className="flex items-center space-x-2">
                <span className="text-xs text-gray-500 w-16 shrink-0">#{i + 1}</span>
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
                  {sign(t.pnl_pct)}{fmt(t.pnl_pct)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
