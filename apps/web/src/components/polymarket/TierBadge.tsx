import { tier as tierTokens } from '@/lib/polymarket/tokens';

type TierKey = 'A' | 'B' | 'C' | 'volatile' | 'excluded';

interface TierBadgeProps {
  tier: string;
  size?: 'sm' | 'md';
}

export function TierBadge({ tier, size = 'sm' }: TierBadgeProps) {
  const known = (tier in tierTokens ? (tier as TierKey) : null) as TierKey | null;
  const colors = known ? (tierTokens as Record<TierKey, { fg: string; bg: string; border: string; label: string }>)[known] : null;

  const px = size === 'sm' ? '6px' : '10px';
  const py = size === 'sm' ? '2px' : '4px';
  const fontSize = size === 'sm' ? '11px' : '13px';

  const style = colors
    ? {
        color: colors.fg,
        backgroundColor: colors.bg,
        borderColor: colors.border,
      }
    : {
        color: 'oklch(55% 0.008 240)',
        backgroundColor: 'oklch(20% 0.008 240)',
        borderColor: 'oklch(30% 0.008 240)',
      };

  return (
    <span
      className="inline-flex items-center border rounded font-semibold uppercase tracking-wide"
      style={{
        ...style,
        padding: `${py} ${px}`,
        fontSize,
        letterSpacing: '0.05em',
      }}
    >
      {known && known !== 'volatile' ? `Tier ${tier.toUpperCase()}` : tier}
    </span>
  );
}
