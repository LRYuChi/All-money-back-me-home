'use client';

/**
 * GaugeChart — 半圓儀表盤 (純 SVG，修正版)
 */
export function GaugeChart({
  value,
  min = 0,
  max = 100,
  label,
  size = 100,
  thresholds,
}: {
  value: number;
  min?: number;
  max?: number;
  label: string;
  size?: number;
  thresholds?: { low: number; high: number };
}) {
  const w = size;
  const h = size * 0.6;
  const cx = w / 2;
  const cy = h - 8;
  const r = Math.min(cx, cy) - 8;

  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));

  // Color
  const t = thresholds || { low: 30, high: 70 };
  let color = '#eab308';
  if (value <= t.low) color = '#ef4444';
  else if (value >= t.high) color = '#22c55e';

  // Arc: from 180° (left) to 0° (right), going clockwise
  const describeArc = (startPct: number, endPct: number, radius: number) => {
    const startAngle = Math.PI * (1 - startPct);
    const endAngle = Math.PI * (1 - endPct);
    const x1 = cx + Math.cos(startAngle) * radius;
    const y1 = cy - Math.sin(startAngle) * radius;
    const x2 = cx + Math.cos(endAngle) * radius;
    const y2 = cy - Math.sin(endAngle) * radius;
    const sweep = endPct > startPct ? 1 : 0;
    return `M ${x1} ${y1} A ${radius} ${radius} 0 0 ${sweep} ${x2} ${y2}`;
  };

  // Needle angle: 0% = left (180°), 100% = right (0°)
  const needleAngle = Math.PI * (1 - normalized);
  const needleLen = r - 4;
  const nx = cx + Math.cos(needleAngle) * needleLen;
  const ny = cy - Math.sin(needleAngle) * needleLen;

  return (
    <div className="flex flex-col items-center">
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        {/* Background arc (full) */}
        <path d={describeArc(0, 1, r)} fill="none" stroke="#374151" strokeWidth="6" strokeLinecap="round" />

        {/* Colored segments */}
        <path d={describeArc(0, (t.low - min) / (max - min), r)} fill="none" stroke="#ef4444" strokeWidth="6" strokeLinecap="round" opacity="0.4" />
        <path d={describeArc((t.low - min) / (max - min), (t.high - min) / (max - min), r)} fill="none" stroke="#eab308" strokeWidth="6" strokeLinecap="round" opacity="0.4" />
        <path d={describeArc((t.high - min) / (max - min), 1, r)} fill="none" stroke="#22c55e" strokeWidth="6" strokeLinecap="round" opacity="0.4" />

        {/* Active arc */}
        <path d={describeArc(0, normalized, r)} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />

        {/* Needle */}
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="white" strokeWidth="1.5" />
        <circle cx={cx} cy={cy} r="3" fill="white" />

        {/* Value */}
        <text x={cx} y={cy - 12} textAnchor="middle" fill="white" fontSize="16" fontWeight="bold">
          {Math.round(value)}
        </text>
      </svg>
      <div className="text-gray-400 text-[10px] -mt-1">{label}</div>
    </div>
  );
}
