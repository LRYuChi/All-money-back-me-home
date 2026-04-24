"""Load + validate the regime × source weight matrix from YAML."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fusion.regime import Regime
from shared.signals.types import SignalSource

logger = logging.getLogger(__name__)


DEFAULT_WEIGHTS_PATH = Path("config/fusion/weights.yaml")


class WeightsError(ValueError):
    """Weight matrix is malformed or missing required regime/source."""


_VALID_SOURCES: set[str] = {s.value for s in SignalSource}
_VALID_REGIMES: set[str] = {r.value for r in Regime}


def load_weights(path: Path | str | None = None) -> dict[str, dict[str, float]]:
    """Load and validate the weight matrix.

    Returns nested dict: {regime_name: {source_name: weight}}

    Validation:
      - Every Regime member must have an entry (UNKNOWN inclusive)
      - Each regime's sources are checked against SignalSource enum
      - Weights must be non-negative floats
      - Sum-to-1 not enforced (Fuser normalises); but warn if very off

    Raises WeightsError on invalid structure.
    """
    p = Path(path) if path else DEFAULT_WEIGHTS_PATH
    if not p.exists():
        raise WeightsError(f"weights yaml not found: {p}")

    try:
        import yaml
    except ImportError as e:
        raise WeightsError(f"pyyaml not installed: {e}") from e

    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise WeightsError(f"YAML parse error in {p}: {e}") from e

    if not isinstance(raw, dict):
        raise WeightsError(f"top-level must be a dict, got {type(raw).__name__}")

    parsed: dict[str, dict[str, float]] = {}
    for regime_name, sources in raw.items():
        if regime_name not in _VALID_REGIMES:
            raise WeightsError(
                f"unknown regime {regime_name!r}; valid={sorted(_VALID_REGIMES)}",
            )
        if not isinstance(sources, dict):
            raise WeightsError(f"{regime_name}: must be a dict of source→weight")

        normalized: dict[str, float] = {}
        for source_name, weight in sources.items():
            if source_name not in _VALID_SOURCES:
                raise WeightsError(
                    f"{regime_name}.{source_name}: unknown source; "
                    f"valid={sorted(_VALID_SOURCES)}",
                )
            try:
                w = float(weight)
            except (TypeError, ValueError) as e:
                raise WeightsError(f"{regime_name}.{source_name}: {weight!r} not numeric") from e
            if w < 0:
                raise WeightsError(f"{regime_name}.{source_name}: weight {w} < 0")
            normalized[source_name] = w

        total = sum(normalized.values())
        if total <= 0:
            raise WeightsError(f"{regime_name}: weight sum is zero — at least one source must have weight")
        if abs(total - 1.0) > 0.05:
            logger.info(
                "weights[%s]: sum=%.3f differs from 1.0 — fuser will normalise",
                regime_name, total,
            )
        parsed[regime_name] = normalized

    # Every regime must be present (UNKNOWN serves as fallback when detector
    # can't classify; missing it would crash fuser later)
    missing = _VALID_REGIMES - parsed.keys()
    if missing:
        raise WeightsError(f"weights yaml missing regimes: {sorted(missing)}")

    return parsed


def get_weights_for(
    regime: Regime, weights: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Look up the weight row for a regime; falls back to UNKNOWN if missing."""
    return weights.get(regime.value) or weights.get("UNKNOWN") or {}


__all__ = [
    "DEFAULT_WEIGHTS_PATH",
    "WeightsError",
    "load_weights",
    "get_weights_for",
]
