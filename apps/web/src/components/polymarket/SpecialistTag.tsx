import { fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * 領域專精標籤——對齊 Telegram 推播的 Politics專家 ✓ 慣例.
 *
 * 三種狀態：
 *   - specialist + matched (此筆交易在專長內)：紫框 + ✓
 *   - specialist + not matched：紫框 + 灰色說明
 *   - 無 specialist：不渲染
 */

interface SpecialistTagProps {
  specialists: string[];
  matched?: boolean | null; // 此筆交易是否在專長類別 (alert feed 用)
  size?: 'xs' | 'sm';
}

export function SpecialistTag({ specialists, matched, size = 'sm' }: SpecialistTagProps) {
  if (!specialists || specialists.length === 0) return null;
  const label = specialists[0]; // 主要 specialist
  const more = specialists.length > 1 ? `+${specialists.length - 1}` : '';
  const fontSize = size === 'xs' ? '10px' : '11px';
  const padding = size === 'xs' ? '1px 6px' : '2px 8px';

  return (
    <span
      className="inline-flex items-center gap-[3px] rounded border"
      style={{
        padding,
        fontSize,
        color: semantic.whale,
        backgroundColor: layer['02'],
        borderColor: 'color-mix(in oklab, ' + semantic.whale + ' 35%, transparent)',
        whiteSpace: 'nowrap',
      }}
    >
      <span>{label}專家{more}</span>
      {matched === true && <span style={{ color: semantic.live, fontWeight: 600 }}>✓</span>}
      {matched === false && (
        <span style={{ color: fg.tertiary, fontSize: '9px' }}>非此</span>
      )}
    </span>
  );
}

interface ConsistencyTagProps {
  isConsistent: boolean | null;
  size?: 'xs' | 'sm';
}

export function ConsistencyTag({ isConsistent, size = 'sm' }: ConsistencyTagProps) {
  if (isConsistent === null || isConsistent === undefined) {
    return (
      <span
        style={{
          fontSize: size === 'xs' ? '10px' : '11px',
          color: fg.tertiary,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        }}
      >
        —
      </span>
    );
  }

  const color = isConsistent ? semantic.live : semantic.warn;
  const text = isConsistent ? '穩定 ✓' : '波動';
  const fontSize = size === 'xs' ? '10px' : '11px';
  const padding = size === 'xs' ? '1px 6px' : '2px 8px';

  return (
    <span
      className="inline-flex items-center rounded border"
      style={{
        padding,
        fontSize,
        color,
        backgroundColor: layer['02'],
        borderColor: 'color-mix(in oklab, ' + color + ' 35%, transparent)',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </span>
  );
}
