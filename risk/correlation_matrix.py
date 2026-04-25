"""CorrelationMatrix — pairwise symbol correlations for G7 CorrelationCapGuard.

The matrix stores ρ(symbol_a, symbol_b) ∈ [-1, 1]. It's intentionally
symmetric: lookups try both orderings before falling back to the default.
Self-correlation defaults to 1.0 but can be overridden per-symbol.

Backends:
  - NoOpCorrelationMatrix      — always returns 0 (G7 fail-opens)
  - InMemoryCorrelationMatrix  — caller seeds (a, b) → ρ pairs (tests + smoke)
  - YamlCorrelationMatrix      — loads from a YAML file with shape:
        defaults:
          self: 1.0
          missing: 0.0
        pairs:
          - [crypto:OKX:BTC/USDT:USDT, crypto:OKX:ETH/USDT:USDT, 0.85]
          - [crypto:OKX:BTC/USDT:USDT, crypto:OKX:SOL/USDT:USDT, 0.78]
          ...

Phase G v2 will add a rolling-window backend that recomputes from
ohlcv data nightly. Until then the YAML loader is the prod path —
risk team curates the matrix manually so we don't trade on
inadvertently-stale stats.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)


class CorrelationMatrix(Protocol):
    def get(self, symbol_a: str, symbol_b: str) -> float: ...
    def known_pairs(self) -> int: ...


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpCorrelationMatrix:
    """Always returns 0. G7 will treat every position as uncorrelated and
    fail-open. Use when no matrix is configured."""

    def get(self, symbol_a: str, symbol_b: str) -> float:
        return 0.0

    def known_pairs(self) -> int:
        return 0


# ================================================================== #
# InMemory
# ================================================================== #
class InMemoryCorrelationMatrix:
    """Caller pre-seeds pairs. Symmetric: get(a,b) == get(b,a).

    `default_self` (default 1.0) used when symbol_a == symbol_b and the
    pair isn't explicitly seeded. `default_missing` (default 0.0) used
    when neither (a,b) nor (b,a) is seeded.
    """

    def __init__(
        self,
        pairs: Iterable[tuple[str, str, float]] | None = None,
        *,
        default_self: float = 1.0,
        default_missing: float = 0.0,
    ):
        self._by_pair: dict[tuple[str, str], float] = {}
        self._default_self = default_self
        self._default_missing = default_missing
        for a, b, rho in (pairs or []):
            self.add(a, b, rho)

    def add(self, symbol_a: str, symbol_b: str, rho: float) -> None:
        rho_clamped = max(-1.0, min(1.0, float(rho)))
        # Store canonical ordering (alphabetical) — symmetric lookup uses
        # the same canonical key.
        key = self._canonical(symbol_a, symbol_b)
        self._by_pair[key] = rho_clamped

    def get(self, symbol_a: str, symbol_b: str) -> float:
        if symbol_a == symbol_b:
            key = (symbol_a, symbol_b)
            return self._by_pair.get(key, self._default_self)
        key = self._canonical(symbol_a, symbol_b)
        return self._by_pair.get(key, self._default_missing)

    def known_pairs(self) -> int:
        return len(self._by_pair)

    @staticmethod
    def _canonical(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)


# ================================================================== #
# YAML loader
# ================================================================== #
class YamlCorrelationMatrix(InMemoryCorrelationMatrix):
    """Reads a YAML file and pre-seeds an InMemory matrix."""

    @classmethod
    def from_path(cls, path: Path | str) -> "YamlCorrelationMatrix":
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError(
                "YamlCorrelationMatrix requires PyYAML — pip install pyyaml"
            ) from e

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"correlation matrix not found: {p}")

        data = yaml.safe_load(p.read_text())
        if not isinstance(data, dict):
            raise ValueError(
                f"correlation matrix YAML must be a mapping; got {type(data).__name__}"
            )

        defaults = data.get("defaults") or {}
        default_self = float(defaults.get("self", 1.0))
        default_missing = float(defaults.get("missing", 0.0))

        raw_pairs = data.get("pairs") or []
        pairs: list[tuple[str, str, float]] = []
        for i, row in enumerate(raw_pairs):
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise ValueError(
                    f"correlation pairs[{i}] must be [symbol_a, symbol_b, rho]; "
                    f"got {row!r}"
                )
            a, b, rho = row
            pairs.append((str(a), str(b), float(rho)))

        m = cls(
            pairs=pairs,
            default_self=default_self,
            default_missing=default_missing,
        )
        logger.info(
            "loaded correlation matrix from %s: %d pairs (default_self=%.2f, "
            "default_missing=%.2f)",
            p, m.known_pairs(), default_self, default_missing,
        )
        return m


# ================================================================== #
# Factory
# ================================================================== #
def build_correlation_matrix(settings) -> CorrelationMatrix:  # noqa: ANN001
    """YAML > NoOp. settings.correlation_matrix_path is checked first;
    if that env/setting isn't present or the file is missing, returns NoOp
    so G7 can be wired without breaking deployments without a matrix."""
    raw_path = (
        getattr(settings, "correlation_matrix_path", "")
        or ""
    ).strip()
    if not raw_path:
        logger.info(
            "correlation_matrix: NoOp (no SM_CORRELATION_MATRIX_PATH) — "
            "G7 will treat all positions as uncorrelated"
        )
        return NoOpCorrelationMatrix()

    try:
        return YamlCorrelationMatrix.from_path(raw_path)
    except FileNotFoundError as e:
        logger.warning(
            "correlation_matrix: %s; falling back to NoOp", e,
        )
        return NoOpCorrelationMatrix()
    except Exception as e:
        logger.error(
            "correlation_matrix: failed to load %s (%s); falling back to NoOp",
            raw_path, e,
        )
        return NoOpCorrelationMatrix()


__all__ = [
    "CorrelationMatrix",
    "NoOpCorrelationMatrix",
    "InMemoryCorrelationMatrix",
    "YamlCorrelationMatrix",
    "build_correlation_matrix",
]
