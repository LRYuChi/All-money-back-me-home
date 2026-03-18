'use client';

interface FreqtradeData {
  state: string;
  strategy: string;
  dry_run?: boolean;
  trade_count?: number;
  profit?: number;
}

interface KillzoneData {
  name: string;
  active?: boolean;
  starts_in_hours?: number;
  utc_start: string;
}

interface TradingData {
  capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  open_positions: number;
  total_trades: number;
  win_rate: number;
}

export function BotStatus({
  bot, killzone, trading,
}: {
  bot: FreqtradeData;
  killzone: KillzoneData;
  trading: TradingData;
}) {
  const isRunning = bot.state === 'running';

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3 space-y-3">
      {/* Bot status */}
      <div>
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-sm font-medium text-gray-400">交易系統</h3>
          <div className={`flex items-center gap-1.5 text-xs ${isRunning ? 'text-green-400' : 'text-red-400'}`}>
            <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`} />
            {isRunning ? '運行中' : '已停止'}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5 text-xs">
          <div className="flex justify-between">
            <span className="text-gray-500">策略</span>
            <span className="text-white font-mono">{bot.strategy}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">模式</span>
            <span className={bot.dry_run ? 'text-yellow-400' : 'text-green-400'}>
              {bot.dry_run ? '模擬' : '實盤'}
            </span>
          </div>
        </div>
      </div>

      {/* Trading stats */}
      <div className="border-t border-gray-800/50 pt-2">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-[10px] text-gray-500">資金</div>
            <div className="text-sm text-white font-mono">${trading.capital.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-[10px] text-gray-500">損益</div>
            <div className={`text-sm font-mono ${trading.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {trading.total_pnl >= 0 ? '+' : ''}{trading.total_pnl_pct.toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-500">交易</div>
            <div className="text-sm text-white font-mono">{trading.total_trades}</div>
          </div>
        </div>
      </div>

      {/* Killzone */}
      <div className="border-t border-gray-800/50 pt-2 text-xs">
        <div className="text-gray-500 mb-0.5">Killzone</div>
        {killzone.active ? (
          <span className="text-green-400">🟢 {killzone.name} (進行中)</span>
        ) : (
          <span className="text-gray-300">{killzone.name} · {killzone.starts_in_hours}h 後</span>
        )}
      </div>
    </div>
  );
}
