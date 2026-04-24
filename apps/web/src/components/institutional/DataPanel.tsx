'use client';

import { ReactNode } from 'react';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * DataPanel — 交易軟體風格的資訊面板。
 *
 * 取代 Card 在密集資料場景下的使用。設計差異：
 *   - 最小圓角 (2px)，而非 Card 的 lg (6px)
 *   - Header bar 是矮的、單行（非多層堆疊）
 *   - 沒有 left-accent 條（以 title 顏色或 StatusDot 代替）
 *   - 可選 density="compact" 減少 padding
 */

interface DataPanelProps {
  children: ReactNode;
  title?: string;
  subtitle?: string;
  /** 右上角動作區（按鈕 / 標籤） */
  actions?: ReactNode;
  /** 左側狀態點（green/yellow/red/gray/none） */
  statusDot?: 'green' | 'yellow' | 'red' | 'gray';
  /** 面板內容 padding 層級 */
  density?: 'compact' | 'comfortable' | 'none';
  className?: string;
  /** 移除下邊線（用於與下方元件緊貼） */
  seamlessBottom?: boolean;
}

export function DataPanel({
  children,
  title,
  subtitle,
  actions,
  statusDot,
  density = 'comfortable',
  className,
  seamlessBottom = false,
}: DataPanelProps) {
  const pad =
    density === 'compact'
      ? '8px 12px'
      : density === 'comfortable'
        ? '12px 16px'
        : '0';

  return (
    <section
      className={className}
      style={{
        backgroundColor: layer['01'],
        border: `1px solid ${borderColor.hair}`,
        borderBottom: seamlessBottom ? 'none' : `1px solid ${borderColor.hair}`,
        borderRadius: 2,
      }}
    >
      {(title || actions) && (
        <header
          style={{
            height: 36,
            borderBottom: `1px solid ${borderColor.hair}`,
            padding: '0 12px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            backgroundColor: layer['01'],
          }}
        >
          {statusDot && <StatusDot color={statusDot} />}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flex: 1, minWidth: 0 }}>
            <span
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: fg.primary,
                letterSpacing: 0.2,
              }}
            >
              {title}
            </span>
            {subtitle && (
              <span
                style={{
                  fontSize: 11,
                  color: fg.tertiary,
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                }}
              >
                {subtitle}
              </span>
            )}
          </div>
          {actions}
        </header>
      )}
      <div style={{ padding: pad }}>{children}</div>
    </section>
  );
}

function StatusDot({ color }: { color: 'green' | 'yellow' | 'red' | 'gray' }) {
  const fill =
    color === 'green'
      ? semantic.live
      : color === 'yellow'
        ? semantic.warn
        : color === 'red'
          ? semantic.error
          : fg.tertiary;
  return (
    <span
      style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        backgroundColor: fill,
        boxShadow: `0 0 6px ${fill}`,
        flexShrink: 0,
      }}
    />
  );
}
