'use client';

/**
 * RadarChart — 六邊形雷達圖 (純 SVG，零依賴)
 * 用於信心引擎 6 因子視覺化
 */
interface RadarData {
  label: string;
  value: number; // 0-1
}

export function RadarChart({
  data,
  size = 200,
}: {
  data: RadarData[];
  size?: number;
}) {
  if (!data || data.length < 3) return null;

  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 30;
  const n = data.length;
  const angleStep = (Math.PI * 2) / n;

  // Grid circles
  const gridLevels = [0.25, 0.5, 0.75, 1.0];

  // Calculate points
  const getPoint = (index: number, value: number) => {
    const angle = index * angleStep - Math.PI / 2;
    return {
      x: cx + Math.cos(angle) * r * value,
      y: cy + Math.sin(angle) * r * value,
    };
  };

  // Data polygon
  const dataPoints = data.map((d, i) => getPoint(i, d.value));
  const dataPath = dataPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${p.y}`).join(' ') + ' Z';

  // Color based on average
  const avg = data.reduce((s, d) => s + d.value, 0) / n;
  const fillColor = avg > 0.6 ? '#22c55e' : avg > 0.4 ? '#eab308' : '#ef4444';

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {/* Grid */}
      {gridLevels.map((level) => {
        const points = Array.from({ length: n }, (_, i) => getPoint(i, level));
        const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${p.y}`).join(' ') + ' Z';
        return <path key={level} d={path} fill="none" stroke="#374151" strokeWidth="0.5" />;
      })}

      {/* Axis lines */}
      {data.map((_, i) => {
        const p = getPoint(i, 1);
        return <line key={`axis-${i}`} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#374151" strokeWidth="0.5" />;
      })}

      {/* Data area */}
      <path d={dataPath} fill={fillColor} fillOpacity="0.2" stroke={fillColor} strokeWidth="2" />

      {/* Data points */}
      {dataPoints.map((p, i) => (
        <circle key={`dot-${i}`} cx={p.x} cy={p.y} r="3" fill={fillColor} />
      ))}

      {/* Labels */}
      {data.map((d, i) => {
        const p = getPoint(i, 1.2);
        return (
          <text
            key={`label-${i}`}
            x={p.x}
            y={p.y}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="#9ca3af"
            fontSize="11"
          >
            {d.label}
          </text>
        );
      })}

      {/* Center score */}
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fill="white" fontSize="16" fontWeight="bold">
        {(avg * 100).toFixed(0)}
      </text>
    </svg>
  );
}
