'use client';

import { ReactNode, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { borderColor, fg, layer, semantic } from '@/lib/polymarket/tokens';

/**
 * AppShell — 交易軟體風格應用程式外殼。
 *
 * 佈局：
 *   ┌────────────────────────────────────────────────────────┐
 *   │  TopBar  (48px)  brand · env · connection · clock      │
 *   ├────┬───────────────────────────────────────────────────┤
 *   │Side│                                                   │
 *   │Nav │              main content (scroll)                │
 *   │72px│                                                   │
 *   │    │                                                   │
 *   └────┴───────────────────────────────────────────────────┘
 *
 * 設計原則：資訊密度、最少圓角、綠(up)/紅(down)、單色強調。
 */

interface NavItem {
  href: string;
  icon: string;
  label: string;
  // optional sub-routes that also match this nav item
  matchPrefix?: string[];
}

interface NavSection {
  label: string;
  items: NavItem[];
}

const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Markets',
    items: [
      { href: '/', icon: '◎', label: 'Overview' },
      {
        href: '/market/crypto',
        icon: '◐',
        label: 'Markets',
        matchPrefix: ['/market', '/symbol'],
      },
    ],
  },
  {
    label: 'Trading',
    items: [
      {
        href: '/trades',
        icon: '≡',
        label: 'Supertrend',
      },
      {
        href: '/polymarket/paper-trades',
        icon: '▦',
        label: 'Poly Paper',
        matchPrefix: ['/polymarket/paper-trades'],
      },
    ],
  },
  {
    label: 'Research',
    items: [
      {
        href: '/polymarket',
        icon: '◈',
        label: 'Poly Whales',
        matchPrefix: ['/polymarket/wallet'],
      },
      { href: '/smart-money', icon: '⟨⟩', label: 'Smart Money' },
      { href: '/backtest', icon: '⟳', label: 'Backtest' },
    ],
  },
];

interface AppShellProps {
  children: ReactNode;
  /** 頁面標題（顯示在 TopBar 右側） */
  pageTitle?: string;
  /** Data freshness hint — 顯示在 TopBar（e.g. "updated 12s ago") */
  dataFreshness?: {
    lastUpdate: Date | null;
    refreshMs: number;
    onRefresh?: () => void;
  };
}

export function AppShell({ children, pageTitle, dataFreshness }: AppShellProps) {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: layer['00'],
        color: fg.primary,
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      }}
    >
      <TopBar pageTitle={pageTitle} dataFreshness={dataFreshness} />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <SideNav />
        <main
          style={{
            flex: 1,
            overflow: 'auto',
            backgroundColor: layer['00'],
          }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}

function TopBar({
  pageTitle,
  dataFreshness,
}: {
  pageTitle?: string;
  dataFreshness?: AppShellProps['dataFreshness'];
}) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1_000);
    return () => clearInterval(id);
  }, []);

  const age = dataFreshness?.lastUpdate
    ? Math.floor((now.getTime() - dataFreshness.lastUpdate.getTime()) / 1000)
    : null;
  const staleThreshold = dataFreshness ? (dataFreshness.refreshMs / 1000) * 2 : 60;
  const isStale = age != null && age > staleThreshold;

  return (
    <header
      style={{
        height: 48,
        borderBottom: `1px solid ${borderColor.hair}`,
        backgroundColor: layer['01'],
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        gap: 16,
        flexShrink: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: 2,
            color: fg.primary,
          }}
        >
          AMBMH
        </span>
        <span
          style={{
            fontSize: 10,
            padding: '2px 6px',
            border: `1px solid ${semantic.warnBorder}`,
            backgroundColor: semantic.warnBg,
            color: semantic.warn,
            borderRadius: 2,
            letterSpacing: 0.5,
            textTransform: 'uppercase',
          }}
        >
          Dry-run
        </span>
      </div>

      {pageTitle && (
        <div
          style={{
            fontSize: 12,
            color: fg.secondary,
            borderLeft: `1px solid ${borderColor.hair}`,
            paddingLeft: 16,
          }}
        >
          {pageTitle}
        </div>
      )}

      <div style={{ flex: 1 }} />

      {dataFreshness && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <LivePulse active={!isStale} />
          <span
            style={{
              fontSize: 11,
              color: isStale ? semantic.warn : fg.secondary,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {age != null ? `${age}s ago` : '—'}
          </span>
          {dataFreshness.onRefresh && (
            <button
              onClick={dataFreshness.onRefresh}
              title="Refresh"
              style={{
                padding: '3px 8px',
                fontSize: 10,
                fontFamily: 'ui-monospace, monospace',
                color: fg.secondary,
                backgroundColor: 'transparent',
                border: `1px solid ${borderColor.hair}`,
                borderRadius: 2,
                cursor: 'pointer',
                letterSpacing: 0.5,
              }}
            >
              ↻
            </button>
          )}
        </div>
      )}

      <div
        style={{
          fontSize: 11,
          color: fg.tertiary,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontVariantNumeric: 'tabular-nums',
          borderLeft: `1px solid ${borderColor.hair}`,
          paddingLeft: 12,
        }}
      >
        {now.toISOString().substring(11, 19)} UTC
      </div>
    </header>
  );
}

function SideNav() {
  const pathname = usePathname() ?? '';
  return (
    <nav
      style={{
        width: 80,
        borderRight: `1px solid ${borderColor.hair}`,
        backgroundColor: layer['01'],
        display: 'flex',
        flexDirection: 'column',
        padding: '4px 0',
        flexShrink: 0,
        overflowY: 'auto',
      }}
    >
      {NAV_SECTIONS.map((section, sIdx) => (
        <div key={section.label} style={{ marginTop: sIdx === 0 ? 4 : 10 }}>
          <div
            style={{
              padding: '4px 6px',
              fontSize: 9,
              color: fg.tertiary,
              letterSpacing: 1,
              textTransform: 'uppercase',
              textAlign: 'center',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}
          >
            {section.label}
          </div>
          {section.items.map((item) => {
            const active =
              pathname === item.href ||
              (item.matchPrefix?.some((p) => pathname.startsWith(p)) ?? false);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={item.label}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 3,
                  padding: '8px 4px',
                  borderLeft: `2px solid ${active ? semantic.live : 'transparent'}`,
                  color: active ? fg.primary : fg.secondary,
                  textDecoration: 'none',
                  transition: 'color 120ms cubic-bezier(0,0,0.2,1)',
                  backgroundColor: active ? layer['02'] : 'transparent',
                }}
              >
                <span
                  style={{
                    fontSize: 15,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    color: active ? semantic.live : fg.secondary,
                  }}
                >
                  {item.icon}
                </span>
                <span
                  style={{
                    fontSize: 9,
                    letterSpacing: 0.2,
                    textAlign: 'center',
                    lineHeight: 1.1,
                  }}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function LivePulse({ active }: { active: boolean }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        backgroundColor: active ? semantic.live : semantic.stale,
        boxShadow: active ? `0 0 6px ${semantic.live}` : 'none',
        animation: active ? 'ambmh-pulse 1200ms ease-in-out infinite' : 'none',
      }}
    />
  );
}
