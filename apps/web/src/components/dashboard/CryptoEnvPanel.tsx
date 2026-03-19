'use client';

interface CryptoEnvData {
  [symbol: string]: {
    score: number;
    regime: string;
    sandboxes: { derivatives: number; onchain: number; sentiment: number };
    factors: Record<string, { score: number; signal: string }>;
  };
}

const REGIME_COLOR: Record<string, string> = {
  FAVORABLE: 'text-green-400',
  NEUTRAL: 'text-blue-400',
  CAUTIOUS: 'text-yellow-400',
  HOSTILE: 'text-red-400',
};

const REGIME_ZH: Record<string, string> = {
  FAVORABLE: '有利',
  NEUTRAL: '中性',
  CAUTIOUS: '謹慎',
  HOSTILE: '不利',
};

function MiniBar({ value, color }: { value: number; color?: string }) {
  const c = color || (value > 0.6 ? 'bg-green-500' : value > 0.4 ? 'bg-yellow-500' : 'bg-red-500');
  return (
    <div className="w-12 bg-gray-700/50 rounded-full h-1.5 inline-block ml-1">
      <div className={`h-1.5 rounded-full ${c}`} style={{ width: `${value * 100}%` }} />
    </div>
  );
}

export function CryptoEnvPanel({ data }: { data: CryptoEnvData }) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
        <h3 className="text-sm font-medium text-gray-400 mb-2">🔗 加密環境引擎</h3>
        <div className="text-gray-600 text-xs">載入中...</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3">
      <h3 className="text-sm font-medium text-gray-400 mb-2">🔗 加密環境引擎</h3>

      <div className="space-y-2.5">
        {Object.entries(data).map(([sym, env]) => {
          const rc = REGIME_COLOR[env.regime] || 'text-gray-400';
          const rz = REGIME_ZH[env.regime] || env.regime;
          return (
            <div key={sym} className="border-b border-gray-800/30 pb-2 last:border-0 last:pb-0">
              {/* Header */}
              <div className="flex justify-between items-center mb-1">
                <span className="text-white text-sm font-medium">{sym}</span>
                <div className="flex items-center gap-1.5">
                  <span className={`text-lg font-bold ${rc}`}>{(env.score * 100).toFixed(0)}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${rc} bg-white/5`}>{rz}</span>
                </div>
              </div>

              {/* Sandboxes */}
              <div className="flex gap-3 text-[10px]">
                <span className="text-gray-500">衍生品 <MiniBar value={env.sandboxes.derivatives} /></span>
                <span className="text-gray-500">鏈上 <MiniBar value={env.sandboxes.onchain} /></span>
                <span className="text-gray-500">情緒 <MiniBar value={env.sandboxes.sentiment} /></span>
              </div>

              {/* Key signals */}
              <div className="mt-1 space-y-0.5">
                {Object.entries(env.factors || {}).map(([name, f]) => {
                  if (!f.signal || f.signal === 'neutral' || f.signal === 'stable' || f.signal === 'no data') return null;
                  const isWarning = f.signal.includes('⚠️') || f.signal.includes('heavily');
                  return (
                    <div key={name} className={`text-[10px] ${isWarning ? 'text-yellow-400' : 'text-gray-500'}`}>
                      {f.signal}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div className="text-gray-600 text-[9px] mt-2 leading-tight">
        衍生品（Funding Rate、多空比、OI）+ 鏈上（Mempool、DeFi TVL）+ 情緒（Fear&Greed）三層評估加密市場內部狀態。
      </div>
    </div>
  );
}
