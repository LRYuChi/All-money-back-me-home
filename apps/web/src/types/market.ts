export type Market = 'TW' | 'US' | 'CRYPTO';

export interface Instrument {
  id: string;
  market: Market;
  symbol: string;
  nameZh: string;
  nameEn?: string;
  exchange?: string;
  assetType: 'stock' | 'etf' | 'crypto';
  isActive: boolean;
}

export interface OHLCV {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Quote {
  symbol: string;
  market: Market;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  updatedAt: string;
}
