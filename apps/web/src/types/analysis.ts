export interface IndicatorData {
  name: string;
  values: { ts: string; value: number }[];
}

export interface MACDData {
  macd: number[];
  signal: number[];
  histogram: number[];
}

export interface BollingerBandsData {
  upper: number[];
  middle: number[];
  lower: number[];
}

export interface PatternDetection {
  name: string;
  nameZh: string;
  date: string;
  direction: 'bullish' | 'bearish' | 'neutral';
}

export interface Signal {
  type: 'buy' | 'sell' | 'hold';
  strength: number; // 0-1
  reason: string;
  indicators: string[];
}

export interface AnalysisResult {
  symbol: string;
  market: string;
  nameZh: string;
  interval: string;
  ohlcv: import('./market').OHLCV[];
  indicators: Record<string, IndicatorData>;
  patterns: PatternDetection[];
  signals: Signal[];
  summaryZh: string;
}
