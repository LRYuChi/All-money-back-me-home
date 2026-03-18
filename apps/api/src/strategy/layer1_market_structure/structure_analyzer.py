from __future__ import annotations

from ..enums import MarketState, SignalDirection
from ..models import MarketStructureResult, SwingPoint


def _is_ascending(points: list[SwingPoint]) -> bool:
    """Return True if the prices of consecutive points are strictly ascending."""
    if len(points) < 2:
        return False
    return all(points[i].price < points[i + 1].price for i in range(len(points) - 1))


def _is_descending(points: list[SwingPoint]) -> bool:
    """Return True if the prices of consecutive points are strictly descending."""
    if len(points) < 2:
        return False
    return all(points[i].price > points[i + 1].price for i in range(len(points) - 1))


def _compute_confidence(
    highs: list[SwingPoint],
    lows: list[SwingPoint],
    state: MarketState,
) -> float:
    """Compute a 0-1 confidence score for the classified market state.

    The score reflects how consistently the swing points agree with the
    detected pattern.  For trending states every consecutive pair that
    moves in the expected direction adds to the score; for ranging the
    confidence is based on how *mixed* the movements are.
    """
    if state == MarketState.RANGING:
        # In a ranging market, confidence is higher when movements are mixed.
        # We return a moderate baseline.
        return 0.5

    def _direction_agreement(points: list[SwingPoint], ascending: bool) -> float:
        if len(points) < 2:
            return 0.0
        pairs = len(points) - 1
        matching = sum(
            1
            for i in range(pairs)
            if (points[i].price < points[i + 1].price) == ascending
        )
        return matching / pairs

    if state == MarketState.TRENDING_UP:
        high_score = _direction_agreement(highs, ascending=True)
        low_score = _direction_agreement(lows, ascending=True)
    else:  # TRENDING_DOWN
        high_score = _direction_agreement(highs, ascending=False)
        low_score = _direction_agreement(lows, ascending=False)

    return round((high_score + low_score) / 2, 4)


def classify_market_state(
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    min_points: int = 3,
) -> MarketStructureResult:
    """Classify the current market structure based on swing points.

    Uses the most recent *min_points* swing highs and swing lows to
    determine whether the market is trending up, trending down, or
    ranging.

    Parameters
    ----------
    swing_highs:
        Detected swing high points, ordered chronologically.
    swing_lows:
        Detected swing low points, ordered chronologically.
    min_points:
        Minimum number of recent points to evaluate on each side.

    Returns
    -------
    MarketStructureResult
        Contains the detected state, swing points used, and confidence.
    """
    recent_highs = swing_highs[-min_points:] if len(swing_highs) >= min_points else swing_highs
    recent_lows = swing_lows[-min_points:] if len(swing_lows) >= min_points else swing_lows

    highs_up = _is_ascending(recent_highs)
    lows_up = _is_ascending(recent_lows)
    highs_down = _is_descending(recent_highs)
    lows_down = _is_descending(recent_lows)

    if highs_up and lows_up:
        state = MarketState.TRENDING_UP
    elif highs_down and lows_down:
        state = MarketState.TRENDING_DOWN
    else:
        state = MarketState.RANGING

    choch_detected, choch_direction = detect_choch(swing_highs, swing_lows, state)
    confidence = _compute_confidence(recent_highs, recent_lows, state)

    return MarketStructureResult(
        state=state,
        swing_highs=recent_highs,
        swing_lows=recent_lows,
        choch_detected=choch_detected,
        choch_direction=choch_direction,
        confidence=confidence,
    )


def detect_choch(
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    state: MarketState,
) -> tuple[bool, SignalDirection | None]:
    """Detect a Change of Character (CHoCH) in the market structure.

    A CHoCH signals a potential trend reversal:

    * **TRENDING_UP**: the latest swing low breaks below the previous swing
      low, suggesting a shift to bearish → ``(True, SHORT)``.
    * **TRENDING_DOWN**: the latest swing high breaks above the previous
      swing high, suggesting a shift to bullish → ``(True, LONG)``.
    * **RANGING**: no CHoCH detection is performed.

    Parameters
    ----------
    swing_highs:
        Chronologically ordered swing highs.
    swing_lows:
        Chronologically ordered swing lows.
    state:
        The current classified market state.

    Returns
    -------
    tuple[bool, SignalDirection | None]
        Whether a CHoCH was detected and, if so, its implied direction.
    """
    if state == MarketState.RANGING:
        return False, None

    if state == MarketState.TRENDING_UP and len(swing_lows) >= 2:
        latest_low = swing_lows[-1]
        previous_low = swing_lows[-2]
        if latest_low.price < previous_low.price:
            return True, SignalDirection.SHORT

    if state == MarketState.TRENDING_DOWN and len(swing_highs) >= 2:
        latest_high = swing_highs[-1]
        previous_high = swing_highs[-2]
        if latest_high.price > previous_high.price:
            return True, SignalDirection.LONG

    return False, None
