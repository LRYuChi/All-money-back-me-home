from enum import Enum


class MarketState(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"


class Timeframe(str, Enum):
    MONTHLY = "1mo"
    WEEKLY = "1wk"
    DAILY = "1d"
    H4 = "4h"
    H1 = "1h"


class StrategyName(str, Enum):
    TREND_FOLLOWING = "A_TREND"
    BB_SQUEEZE = "B_SQUEEZE"
    RSI_DIVERGENCE = "C_DIVERGENCE"
    SR_ZONES = "D_SR_ZONES"


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
