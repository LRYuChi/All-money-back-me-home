'use client';

/**
 * GaugeChart — 半圓儀表盤 (純 SVG)
 * 用於 VIX / Fear & Greed 指數
 */
export function GaugeChart({
  value,
  min = 0,
  max = 100,
  label,
  size = 120,
  thresholds,
}: {
  value: number;
  min?: number;
  max?: number;
  label: string;
  size?: number;
  thresholds?: { low: number; high: number };
}) {
  const cx = size / 2;
  const cy = size * 0.65;
  const r = size / 2 - 10;
  const startAngle = Math.PI;
  const endAngle = 0;

  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const angle = startAngle - normalized * Math.PI;

  // Determine color
  const t = thresholds || { low: 30, high: 70 };
  let color = '#eab308'; // yellow
  if (value <= t.low) color = '#ef4444'; // red
  else if (value >= t.high) color = '#22c55e'; // green

  // Arc path helper
  const arcPath = (startA: number, endA: number, radius: number) => {
    const x1 = cx + Math.cos(startA) * radius;
    const y1 = cy - Math.sin(startA) * radius;
    const x2 = cx + Math.cos(endA) * radius;
    const y2 = cy - Math.sin(endA) * radius;
    const largeArc = endA - startA > Math.PI ? 1 : 0;
    return `M ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 0 ${x2} ${y2}`;
  };

  // Needle endpoint
  const needleX = cx + Math.cos(angle) * (r - 5);
  const needleY = cy - Math.sin(angle) * (r - 5);

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size * 0.7} viewBox={`0 0 ${size} ${size * 0.7}`}>
        {/* Background arc */}
        <path d={arcPath(startAngle, endAngle, r)} fill="none" stroke="#374151" strokeWidth="8" strokeLinecap="round" />

        {/* Value arc */}
        <path
          d={arcPath(startAngle, angle, r)}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
        />

        {/* Needle */}
        <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke="white" strokeWidth="2" />
        <circle cx={cx} cy={cy} r="4" fill="white" />

        {/* Value text */}
        <text x={cx} y={cy - 15} textAnchor="middle" fill="white" fontSize="18" fontWeight="bold">
          {Math.round(value)}
        </text>
      </svg>
      <div className="text-gray-400 text-xs mt-1">{label}</div>
    </div>
  );
}
