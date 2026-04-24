'use client';

import { ReactNode } from 'react';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * DataTable — institutional trading table primitive.
 *
 * 特點：
 *   - sticky thead
 *   - compact row padding (6px vertical)
 *   - zebra stripes (subtle)
 *   - hover row highlight
 *   - right-align for numeric columns
 *   - tabular-nums + JetBrains Mono for numbers
 *   - 預設無外框（DataPanel 提供）
 */

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: 'left' | 'right' | 'center';
  /** 是否用 monospace + tabular-nums（數字欄位） */
  mono?: boolean;
  /** 固定寬度（px or %） */
  width?: string;
  /** 取 cell 內容的 renderer */
  render: (row: T, idx: number) => ReactNode;
}

interface DataTableProps<T> {
  rows: T[];
  columns: Column<T>[];
  /** 當 rows 空時顯示 */
  emptyMessage?: ReactNode;
  /** 點擊行的 callback */
  onRowClick?: (row: T) => void;
  /** 每行唯一 key */
  rowKey: (row: T, idx: number) => string;
  /** 壓縮模式（減少 padding） */
  compact?: boolean;
  /** 頂部 sticky thead 的背景色 */
  stickyHeader?: boolean;
}

export function DataTable<T>({
  rows,
  columns,
  emptyMessage = '無資料',
  onRowClick,
  rowKey,
  compact = false,
  stickyHeader = true,
}: DataTableProps<T>) {
  const cellPad = compact ? '4px 10px' : '6px 12px';

  if (rows.length === 0) {
    return (
      <div
        style={{
          padding: '32px 16px',
          textAlign: 'center',
          color: fg.tertiary,
          fontSize: 12,
        }}
      >
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={{ overflow: 'auto' }}>
      <table
        style={{
          width: '100%',
          borderCollapse: 'separate',
          borderSpacing: 0,
          fontSize: 12,
          color: fg.primary,
        }}
      >
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={{
                  padding: cellPad,
                  textAlign: c.align ?? 'left',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: 0.5,
                  textTransform: 'uppercase',
                  color: fg.tertiary,
                  backgroundColor: layer['01'],
                  borderBottom: `1px solid ${borderColor.base}`,
                  position: stickyHeader ? 'sticky' : 'static',
                  top: 0,
                  zIndex: 1,
                  whiteSpace: 'nowrap',
                  width: c.width,
                }}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const isOdd = idx % 2 === 1;
            return (
              <tr
                key={rowKey(row, idx)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className="ambmh-row"
                style={{
                  backgroundColor: isOdd ? layer['01'] : layer['00'],
                  cursor: onRowClick ? 'pointer' : 'default',
                  transition: 'background-color 100ms',
                }}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    style={{
                      padding: cellPad,
                      textAlign: c.align ?? 'left',
                      fontFamily: c.mono
                        ? 'ui-monospace, SFMono-Regular, Menlo, monospace'
                        : 'inherit',
                      fontVariantNumeric: c.mono ? 'tabular-nums' : undefined,
                      borderBottom: `1px solid ${borderColor.hair}`,
                      whiteSpace: 'nowrap',
                      verticalAlign: 'top',
                    }}
                  >
                    {c.render(row, idx)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Shared cell render helpers — 讓所有 table 一致
// ─────────────────────────────────────────────────────────────────────

export function Numeric({
  value,
  precision = 2,
  prefix = '',
  suffix = '',
  tone,
}: {
  value: number | null | undefined;
  precision?: number;
  prefix?: string;
  suffix?: string;
  tone?: 'up' | 'down' | 'neutral';
}) {
  if (value == null) {
    return <span style={{ color: fg.tertiary }}>—</span>;
  }
  const color =
    tone === 'up'
      ? semantic.live
      : tone === 'down'
        ? semantic.error
        : fg.primary;
  return (
    <span style={{ color }}>
      {prefix}
      {value.toLocaleString(undefined, {
        minimumFractionDigits: precision,
        maximumFractionDigits: precision,
      })}
      {suffix}
    </span>
  );
}

export function PnlCell({
  value,
  precision = 2,
  showSign = true,
}: {
  value: number | null | undefined;
  precision?: number;
  showSign?: boolean;
}) {
  if (value == null) return <span style={{ color: fg.tertiary }}>—</span>;
  const sign = showSign ? (value >= 0 ? '+' : '-') : '';
  const abs = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  return (
    <span style={{ color: value > 0 ? semantic.no : value < 0 ? semantic.yes : fg.secondary }}>
      {sign}${abs}
    </span>
  );
}

export function PctCell({
  value,
  precision = 2,
  showSign = true,
  isRatio = true,
}: {
  value: number | null | undefined;
  precision?: number;
  showSign?: boolean;
  /** true = value 是 0.xx 格式, false = value 是 xx.x 格式 */
  isRatio?: boolean;
}) {
  if (value == null) return <span style={{ color: fg.tertiary }}>—</span>;
  const scaled = isRatio ? value * 100 : value;
  const sign = showSign ? (scaled >= 0 ? '+' : '') : '';
  return (
    <span
      style={{
        color: scaled > 0 ? semantic.no : scaled < 0 ? semantic.yes : fg.secondary,
      }}
    >
      {sign}
      {scaled.toFixed(precision)}%
    </span>
  );
}

export function Address({ addr, short = true }: { addr: string; short?: boolean }) {
  const display = short && addr.length > 12 ? `${addr.substring(0, 6)}…${addr.substring(addr.length - 4)}` : addr;
  return (
    <span
      style={{
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 11,
        color: fg.primary,
      }}
      title={addr}
    >
      {display}
    </span>
  );
}

export function TimeCell({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span style={{ color: fg.tertiary }}>—</span>;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return <span>{iso}</span>;
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const day = `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
    return (
      <span>
        <span>{day}</span>
        <span style={{ color: fg.tertiary, marginLeft: 4 }}>
          {hh}:{mm}
        </span>
      </span>
    );
  } catch {
    return <span>{iso}</span>;
  }
}
