"""Funding Rate as alpha source — R51.

OKX perpetuals charge funding every 8h. The rate is set by basis (perp
vs spot). Pre-R51, FR was used only as a filter (extreme positive →
don't long; extreme negative → don't short). R51 promotes FR to a
first-class signal:

  fr_signal_strength(fr) → float in [-1, 1]
    > 0  = bias to LONG (extreme negative FR — shorts crowded, squeeze risk)
    < 0  = bias to SHORT (extreme positive FR — longs crowded, top risk)
    ~ 0  = neutral

  fr_independent_entry(fr) → ("long" | "short" | None)
    Triggers in extreme regions when other 3-TF signals are neutral.
    Used as a small mean-reversion play.

Thresholds based on OKX historical FR distribution:
  |FR| < 0.0002 (0.02%/8h)  = neutral
  |FR| 0.0002 — 0.0005       = mild bias
  |FR| > 0.0005 (0.05%/8h)   = extreme — strong contra-signal
  |FR| > 0.001  (0.1%/8h)    = blowoff — high conviction contra

The signal is contrarian: extreme positive FR (everyone long, paying)
suggests short setup. Extreme negative FR (everyone short, paid)
suggests long setup or imminent squeeze.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# Thresholds (per 8h funding period)
FR_NEUTRAL = 0.0002          # < 0.02%/8h = noise
FR_MILD = 0.0005             # 0.02-0.05% = mild bias
FR_EXTREME = 0.001           # > 0.1% = blowoff territory


@dataclass(slots=True, frozen=True)
class FundingSignal:
    """One FR observation translated into a directional signal."""
    fr: float                    # raw funding rate (e.g. 0.0008 = 0.08%/8h)
    strength: float              # in [-1, 1] — sign + magnitude of contra-signal
    bias: str                    # "long" | "short" | "neutral"
    confidence: str              # "neutral" | "mild" | "extreme" | "blowoff"
    independent_entry: str | None  # "long" | "short" | None


def fr_signal_strength(fr: float) -> float:
    """Map raw FR → contra-signal strength in [-1, 1].

    Sign is inverted (positive FR → negative signal, suggesting short).
    Magnitude grows non-linearly so blowoffs (FR > 0.1%) saturate near 1.

    Examples:
      fr=0       → 0
      fr=0.0003 → -0.3 (mild short bias)
      fr=0.0008 → -0.7 (strong short bias)
      fr=0.002  → -1.0 (saturated; blowoff)
    """
    if abs(fr) < FR_NEUTRAL:
        return 0.0

    # Smooth response — tanh saturates at ±1, knee around FR_EXTREME
    # tanh(fr / FR_EXTREME) gives ±0.76 at FR_EXTREME
    raw = math.tanh(fr / FR_EXTREME)
    # Invert sign — positive FR (longs paying) signals short bias
    return -float(raw)


def fr_confidence_label(fr: float) -> str:
    """Describe the regime in words for journals/Telegram."""
    af = abs(fr)
    if af < FR_NEUTRAL:
        return "neutral"
    if af < FR_MILD:
        return "mild"
    if af < FR_EXTREME:
        return "extreme"
    return "blowoff"


def fr_bias_label(fr: float) -> str:
    """Direction of FR bias (CONTRARIAN — opposite of FR sign).

    Returns 'long' / 'short' / 'neutral'.
    """
    if abs(fr) < FR_NEUTRAL:
        return "neutral"
    return "short" if fr > 0 else "long"


def fr_independent_entry(fr: float, *,
                         all_tf_neutral: bool = False) -> str | None:
    """Independent entry trigger for extreme FR + no other signal.

    Caller passes `all_tf_neutral=True` only when the multi-timeframe
    indicators are all neutral (no clear direction from price action).
    Then extreme FR alone can fire a small mean-reversion entry.

    Returns 'long' / 'short' / None.
    """
    if not all_tf_neutral:
        return None
    if abs(fr) < FR_EXTREME:
        return None
    # Only trigger when FR is in extreme zone
    return "long" if fr < 0 else "short"


def build_funding_signal(fr: float, *,
                         all_tf_neutral: bool = False) -> FundingSignal:
    """Single-call helper: returns full FundingSignal struct."""
    return FundingSignal(
        fr=fr,
        strength=fr_signal_strength(fr),
        bias=fr_bias_label(fr),
        confidence=fr_confidence_label(fr),
        independent_entry=fr_independent_entry(fr, all_tf_neutral=all_tf_neutral),
    )


__all__ = [
    "FundingSignal",
    "fr_signal_strength",
    "fr_confidence_label",
    "fr_bias_label",
    "fr_independent_entry",
    "build_funding_signal",
    "FR_NEUTRAL",
    "FR_MILD",
    "FR_EXTREME",
]
