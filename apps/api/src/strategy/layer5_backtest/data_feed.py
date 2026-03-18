"""Layer 5 — DataFeed: simulates candle-by-candle arrival."""

from __future__ import annotations

from typing import Iterator

import pandas as pd


class DataFeed:
    """Yields expanding windows of OHLCV data, simulating real-time bar arrival.

    After *warmup* bars, each iteration yields ``(bar_index, df_up_to_bar)``
    so that strategies only see data available at that point in time.
    """

    def __init__(self, df: pd.DataFrame, warmup: int = 200) -> None:
        self._df = df
        self._warmup = warmup

    def __len__(self) -> int:
        return max(0, len(self._df) - self._warmup)

    def __iter__(self) -> Iterator[tuple[int, pd.DataFrame]]:
        for i in range(self._warmup, len(self._df)):
            yield i, self._df.iloc[: i + 1]
