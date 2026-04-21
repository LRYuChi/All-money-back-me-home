import { borderColor, fg, layer } from '@/lib/polymarket/tokens';
import { ReactNode } from 'react';

/**
 * 共用卡片外殼 — 統一圓角、邊框、背景、padding、分隔線。
 * 所有 polymarket dashboard 區塊都用這個容器，避免每個元件各寫一次 card style。
 */

interface CardProps {
  children: ReactNode;
  accentColor?: string; // 左側 2px accent 條
  className?: string;
}

export function Card({ children, accentColor, className }: CardProps) {
  return (
    <section
      className={`rounded-lg overflow-hidden ${className ?? ''}`}
      style={{
        backgroundColor: layer['01'],
        border: `1px solid ${borderColor.hair}`,
        borderLeft: accentColor ? `2px solid ${accentColor}` : `1px solid ${borderColor.hair}`,
        color: fg.primary,
      }}
    >
      {children}
    </section>
  );
}

interface CardHeaderProps {
  eyebrow?: string;
  title?: string;
  subtitle?: string;
  trailing?: ReactNode;
  divider?: boolean;
}

export function CardHeader({ eyebrow, title, subtitle, trailing, divider }: CardHeaderProps) {
  return (
    <header
      className="flex items-start justify-between gap-4"
      style={{
        padding: '16px 20px',
        borderBottom: divider ? `1px solid ${borderColor.hair}` : undefined,
      }}
    >
      <div className="min-w-0">
        {eyebrow && (
          <div
            style={{
              color: fg.tertiary,
              fontSize: '10px',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              fontWeight: 500,
            }}
          >
            {eyebrow}
          </div>
        )}
        {title && (
          <h2
            style={{
              color: fg.primary,
              fontSize: '15px',
              fontWeight: 600,
              marginTop: eyebrow ? '2px' : 0,
            }}
          >
            {title}
          </h2>
        )}
        {subtitle && (
          <div style={{ color: fg.tertiary, fontSize: '12px', marginTop: '2px' }}>
            {subtitle}
          </div>
        )}
      </div>
      {trailing}
    </header>
  );
}

export function CardBody({ children, pad = true }: { children: ReactNode; pad?: boolean }) {
  return <div style={{ padding: pad ? '16px 20px 20px' : 0 }}>{children}</div>;
}
