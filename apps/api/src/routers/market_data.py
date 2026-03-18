from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from src.fetchers.crypto import CryptoFetcher
from src.fetchers.tw_stock import TWStockFetcher
from src.fetchers.us_stock import USStockFetcher
from src.models.schemas import OHLCVData

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

Market = Literal["us", "tw", "crypto"]


def _get_fetcher(market: Market):
    if market == "us":
        return USStockFetcher()
    if market == "tw":
        return TWStockFetcher()
    if market == "crypto":
        return CryptoFetcher()
    raise HTTPException(status_code=400, detail=f"Unsupported market: {market}")


@router.get("/{market}/{symbol}/ohlcv", response_model=list[OHLCVData])
async def get_ohlcv(
    market: Market,
    symbol: str,
    interval: str = Query(default="1d", description="Data interval (e.g. 1d, 1h, 5m)"),
    period: str = Query(default="6mo", description="Lookback period (e.g. 6mo, 1y, 3mo)"),
) -> list[OHLCVData]:
    """Fetch OHLCV (candlestick) data for a given market and symbol."""
    fetcher = _get_fetcher(market)
    try:
        df = fetcher.fetch_ohlcv(symbol, interval=interval, period=period)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data: {exc}") from exc

    if df.empty:
        return []

    results: list[OHLCVData] = []
    for idx, row in df.iterrows():
        ts = idx if isinstance(idx, datetime) else datetime.now(tz=timezone.utc)
        results.append(
            OHLCVData(
                ts=ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0)),
            )
        )
    return results
