'use client';

/**
 * HeatMap — 相關性色塊熱力圖
 * 紅色 = 負相關, 綠色 = 正相關, 灰色 = 無相關
 */
interface HeatMapItem {
  label: string;
  value: number; // -1 to 1
  detail?: string;
}

export function HeatMap({ data }: { data: HeatMapItem[] }) {
  if (!data || data.length === 0) return <div className="text-gray-500 text-sm">載入中...</div>;

  const getColor = (v: number): string => {
    if (v > 0.6) return 'bg-green-600';
    if (v > 0.3) return 'bg-green-800';
    if (v > 0.1) return 'bg-green-900/50';
    if (v > -0.1) return 'bg-gray-700';
    if (v > -0.3) return 'bg-red-900/50';
    if (v > -0.6) return 'bg-red-800';
    return 'bg-red-600';
  };

  return (
    <div className="grid grid-cols-2 gap-2">
      {data.map((item) => (
        <div
          key={item.label}
          className={`${getColor(item.value)} rounded-lg p-3 text-center transition-all hover:scale-105`}
        >
          <div className="text-white text-sm font-medium">{item.label}</div>
          <div className="text-white text-lg font-bold">
            {item.value > 0 ? '+' : ''}{item.value.toFixed(2)}
          </div>
          {item.detail && <div className="text-white/60 text-xs">{item.detail}</div>}
        </div>
      ))}
    </div>
  );
}
