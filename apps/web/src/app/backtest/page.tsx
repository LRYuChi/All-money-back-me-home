'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '@/lib/api-client';

interface BacktestRun {
  id: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  initial_capital: number;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_return: number;
  calmar_ratio: number;
  avg_r_multiple: number;
  avg_trade_duration_bars: number;
  is_walk_forward: boolean;
  created_at: string;
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

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(async () => {
    try {
      const data = await apiClient.get<BacktestRun[]>('/api/strategy/backtest/runs');
      setRuns(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法載入回測結果');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white">回測結果</h1>
        <p className="text-gray-400 mt-1">策略回測歷史紀錄</p>
      </div>

      {loading && (
        <div className="text-center py-16 text-gray-400 animate-pulse">載入中...</div>
      )}

      {error && (
        <div className="rounded-xl bg-red-900/20 border border-red-800 p-4 text-red-400">
          {error}
        </div>
      )}

      {!loading && runs.length === 0 && !error && (
        <div className="rounded-xl bg-gray-800/40 border border-gray-700 p-12 text-center">
          <p className="text-gray-500 text-lg mb-2">尚無回測紀錄</p>
          <p className="text-gray-600 text-sm">
            執行回測後結果會顯示在這裡：
          </p>
          <code className="text-gray-400 text-xs mt-2 block">
            cd apps/api && python -m src.jobs.run_backtest --save
          </code>
        </div>
      )}

      {runs.length > 0 && (
        <div className="space-y-4">
          {runs.map((run) => (
            <div
              key={run.id}
              className="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-gray-700 transition-colors"
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center space-x-3">
                  <span className="text-white font-bold text-lg">{run.symbol}</span>
                  <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                    {run.timeframe}
                  </span>
                  <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                    {run.strategy}
                  </span>
                  {run.is_walk_forward && (
                    <span className="text-xs bg-blue-900/50 text-blue-400 px-2 py-0.5 rounded">
                      Walk-Forward
                    </span>
                  )}
                </div>
                <span className="text-xs text-gray-500">
                  {new Date(run.created_at).toLocaleString('zh-TW')}
                </span>
              </div>

              {/* Metrics Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
                <MetricCell label="總交易" value={`${run.total_trades}`} />
                <MetricCell
                  label="勝率"
                  value={`${(run.win_rate * 100).toFixed(1)}%`}
                  valueClass={run.win_rate >= 0.5 ? 'text-green-400' : 'text-red-400'}
                />
                <MetricCell
                  label="盈虧比"
                  value={fmt(run.profit_factor)}
                  valueClass={run.profit_factor >= 1.5 ? 'text-green-400' : run.profit_factor >= 1 ? 'text-yellow-400' : 'text-red-400'}
                />
                <MetricCell
                  label="報酬"
                  value={`${sign(run.total_return)}${(run.total_return * 100).toFixed(2)}%`}
                  valueClass={pnlClass(run.total_return)}
                />
                <MetricCell
                  label="最大回撤"
                  value={`${(run.max_drawdown * 100).toFixed(2)}%`}
                  valueClass={run.max_drawdown > 0.1 ? 'text-red-400' : 'text-yellow-400'}
                />
                <MetricCell label="Sharpe" value={fmt(run.sharpe_ratio)} />
                <MetricCell label="Calmar" value={fmt(run.calmar_ratio)} />
                <MetricCell
                  label="Avg R"
                  value={`${sign(run.avg_r_multiple)}${fmt(run.avg_r_multiple, 1)}R`}
                  valueClass={pnlClass(run.avg_r_multiple)}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricCell({
  label,
  value,
  valueClass = 'text-white',
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div>
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-bold ${valueClass}`}>{value}</div>
    </div>
  );
}
