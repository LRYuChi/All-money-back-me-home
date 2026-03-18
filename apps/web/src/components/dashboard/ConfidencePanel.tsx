'use client';

import { RadarChart } from '@/components/charts/RadarChart';

interface ConfidenceData {
  score: number;
  regime: string;
  event_multiplier: number;
  sandboxes: Record<string, number>;
  guidance: { position_pct?: number; leverage?: number };
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
  const g = data.guidance || {};
  const positionPct = g.position_pct ?? Math.round(data.score * 100);
  const leverage = g.leverage ?? Math.round((1 + 2 * data.score ** 2) * 10) / 10;

  const radarData = Object.entries(data.sandboxes || {}).map(([k, v]) => ({
    label: SANDBOX_ZH[k] || k,
    value: v,
  }));

  const hasRadar = radarData.length >= 3;

  return (
    <div className={`rounded-lg border p-4 ${r.bg}`}>
      {/* Header */}
      <div className="flex justify-between items-center mb-3">
        <h3 className="text-base font-semibold text-gray-300">信心引擎</h3>
        <div className="flex items-center gap-3">
          <span className={`text-3xl font-bold ${r.color}`}>{(data.score * 100).toFixed(0)}</span>
          <span className={`text-sm px-2.5 py-1 rounded font-medium ${r.color} bg-white/5`}>{r.label}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-700/50 rounded-full h-2 mb-4">
        <div
          className={`h-2 rounded-full transition-all ${
            data.score > 0.6 ? 'bg-green-500' : data.score > 0.4 ? 'bg-yellow-500' : data.score > 0.2 ? 'bg-orange-500' : 'bg-red-500'
          }`}
          style={{ width: `${data.score * 100}%` }}
        />
      </div>

      {/* Content */}
      <div className="flex items-start gap-4">
        {hasRadar && (
          <div className="flex-shrink-0">
            <RadarChart data={radarData} size={140} />
          </div>
        )}
        <div className="flex-1 space-y-2">
          {/* Sandbox scores */}
          {Object.entries(data.sandboxes || {}).map(([k, v]) => (
            <div key={k} className="flex justify-between items-center text-sm">
              <span className="text-gray-400">{SANDBOX_ZH[k] || k}</span>
              <div className="flex items-center gap-2">
                <div className="w-16 bg-gray-700/50 rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full ${v > 0.55 ? 'bg-green-500' : v < 0.45 ? 'bg-red-500' : 'bg-yellow-500'}`}
                    style={{ width: `${v * 100}%` }}
                  />
                </div>
                <span className={`font-mono w-8 text-right ${v > 0.55 ? 'text-green-400' : v < 0.45 ? 'text-red-400' : 'text-gray-400'}`}>
                  {(v * 100).toFixed(0)}
                </span>
              </div>
            </div>
          ))}

          {/* Guidance */}
          <div className="pt-2 border-t border-gray-700/50 grid grid-cols-2 gap-3">
            <div>
              <div className="text-gray-500 text-xs">建議倉位</div>
              <div className="text-white text-xl font-bold">{positionPct}%</div>
            </div>
            <div>
              <div className="text-gray-500 text-xs">建議槓桿</div>
              <div className="text-white text-xl font-bold">{leverage}x</div>
            </div>
          </div>

          {data.event_multiplier < 1 && (
            <div className="text-yellow-400 text-xs">⚠️ 事件覆蓋 ×{data.event_multiplier}（FOMC/CPI）</div>
          )}
        </div>
      </div>

      <div className="text-gray-600 text-[10px] mt-3 leading-relaxed">
        信心引擎綜合動量、趨勢、量能、波動品質、市場健康、活動時段六大因子，評估當前環境是否適合交易。分數越高代表多因子匯合程度越強，系統自動調整倉位與槓桿。
      </div>
    </div>
  );
}
