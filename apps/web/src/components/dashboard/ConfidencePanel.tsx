'use client';

import { RadarChart } from '@/components/charts/RadarChart';

interface ConfidenceData {
  score: number;
  regime: string;
  event_multiplier: number;
  sandboxes: Record<string, number>;
  guidance: { position_pct: number; leverage: number };
}

const REGIME_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  AGGRESSIVE: { label: '積極', color: 'text-green-400', bg: 'bg-green-500/10 border-green-500/30' },
  NORMAL:     { label: '正常', color: 'text-blue-400',  bg: 'bg-blue-500/10 border-blue-500/30' },
  CAUTIOUS:   { label: '謹慎', color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30' },
  DEFENSIVE:  { label: '防禦', color: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30' },
  HIBERNATE:  { label: '休眠', color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30' },
};

const SANDBOX_ZH: Record<string, string> = {
  macro: '宏觀', sentiment: '情緒', capital: '資金', haven: '避險',
};

export function ConfidencePanel({ data }: { data: ConfidenceData }) {
  const r = REGIME_STYLE[data.regime] || REGIME_STYLE.CAUTIOUS;
  const g = data.guidance || { position_pct: 0, leverage: 0 };

  const radarData = Object.entries(data.sandboxes || {}).map(([k, v]) => ({
    label: SANDBOX_ZH[k] || k,
    value: v,
  }));

  return (
    <div className={`rounded-lg border p-4 ${r.bg}`}>
      <div className="flex justify-between items-center mb-2">
        <h3 className="text-sm font-medium text-gray-400">信心引擎</h3>
        <div className="flex items-center gap-2">
          <span className={`text-2xl font-bold ${r.color}`}>{(data.score * 100).toFixed(0)}</span>
          <span className={`text-xs px-2 py-0.5 rounded ${r.color} bg-white/5`}>{r.label}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-700/50 rounded-full h-1.5 mb-3">
        <div
          className={`h-1.5 rounded-full transition-all ${
            data.score > 0.6 ? 'bg-green-500' : data.score > 0.4 ? 'bg-yellow-500' : 'bg-red-500'
          }`}
          style={{ width: `${data.score * 100}%` }}
        />
      </div>

      <div className="flex items-start gap-3">
        {radarData.length >= 3 && (
          <div className="flex-shrink-0">
            <RadarChart data={radarData} size={130} />
          </div>
        )}
        <div className="flex-1 space-y-1.5 text-xs">
          {Object.entries(data.sandboxes || {}).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span className="text-gray-500">{SANDBOX_ZH[k] || k}</span>
              <span className={v > 0.55 ? 'text-green-400' : v < 0.45 ? 'text-red-400' : 'text-gray-400'}>
                {(v * 100).toFixed(0)}
              </span>
            </div>
          ))}
          <div className="pt-1.5 border-t border-gray-700/50 space-y-1">
            <div className="flex justify-between">
              <span className="text-gray-500">建議倉位</span>
              <span className="text-white font-medium">{g.position_pct}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">建議槓桿</span>
              <span className="text-white font-medium">{g.leverage}x</span>
            </div>
          </div>
          {data.event_multiplier < 1 && (
            <div className="text-yellow-400 text-[10px]">⚠️ 事件覆蓋 ×{data.event_multiplier}</div>
          )}
        </div>
      </div>
    </div>
  );
}
