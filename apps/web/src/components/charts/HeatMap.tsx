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

const INFO_TEXT = '相關性衡量兩個資產的價格聯動程度。+1=完全正相關（同漲同跌），-1=完全負相關（一漲一跌），0=無關。';

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
    <div>
      <div className="grid grid-cols-2 gap-2">
        {data.map((item) => {
          const interpretation = item.value > 0.5 ? 'BTC 跟隨此資產' :
            item.value < -0.5 ? 'BTC 與此資產反向' :
            Math.abs(item.value) < 0.15 ? 'BTC 獨立於此資產' : '';
          return (
            <div
              key={item.label}
              className={`${getColor(item.value)} rounded-lg p-2.5 text-center transition-all hover:scale-105`}
            >
              <div className="text-white text-xs font-medium">{item.label}</div>
              <div className="text-white text-lg font-bold font-mono">
                {item.value > 0 ? '+' : ''}{item.value.toFixed(2)}
              </div>
              {item.detail && <div className="text-white/60 text-[10px]">{item.detail}</div>}
              {interpretation && <div className="text-white/40 text-[9px] mt-0.5">{interpretation}</div>}
            </div>
          );
        })}
      </div>
      <div className="text-gray-600 text-[9px] mt-2 leading-tight">{INFO_TEXT}</div>
    </div>
  );
}
