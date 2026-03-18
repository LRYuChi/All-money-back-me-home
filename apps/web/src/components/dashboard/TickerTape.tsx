'use client';

import { useEffect, useRef } from 'react';

interface TickerItem {
  name: string;
  price: number;
  change_pct: number;
}

export function TickerTape({ items }: { items: TickerItem[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let pos = 0;
    const speed = 0.5;
    const animate = () => {
      pos -= speed;
      if (pos <= -el.scrollWidth / 2) pos = 0;
      el.style.transform = `translateX(${pos}px)`;
      requestAnimationFrame(animate);
    };
    const id = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(id);
  }, []);

  const renderItem = (item: TickerItem, key: string) => (
    <span key={key} className="inline-flex items-center gap-2 px-4 whitespace-nowrap">
      <span className="text-gray-400 font-medium text-xs">{item.name}</span>
      <span className="text-white text-xs">${item.price.toLocaleString('en-US', { maximumFractionDigits: 2 })}</span>
      <span className={`text-xs font-medium ${item.change_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
        {item.change_pct >= 0 ? '▲' : '▼'}{Math.abs(item.change_pct).toFixed(2)}%
      </span>
    </span>
  );

  return (
    <div className="overflow-hidden bg-gray-900/80 border-b border-gray-800 py-1.5">
      <div ref={scrollRef} className="inline-flex" style={{ willChange: 'transform' }}>
        {items.map((item, i) => renderItem(item, `a-${i}`))}
        {items.map((item, i) => renderItem(item, `b-${i}`))}
      </div>
    </div>
  );
}
