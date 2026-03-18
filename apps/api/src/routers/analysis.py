from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from src.models.schemas import AnalysisResult
from src.services.technical_analysis import TechnicalAnalysisService

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

Market = Literal["us", "tw", "crypto"]

_ta_service = TechnicalAnalysisService()


@router.get("/{market}/{symbol}", response_model=AnalysisResult)
async def get_analysis(
    market: Market,
    symbol: str,
    interval: str = Query(default="1d", description="Data interval (e.g. 1d, 1h, 5m)"),
    period: str = Query(default="6mo", description="Lookback period (e.g. 6mo, 1y, 3mo)"),
    indicators: str = Query(
        default="sma,rsi,macd,bbands",
        description="Comma-separated list of indicators to compute",
    ),
) -> AnalysisResult:
    """Compute technical analysis for a given market and symbol."""
    indicator_list = [i.strip().lower() for i in indicators.split(",") if i.strip()]

    try:
        result = _ta_service.analyze(
            symbol=symbol,
            market=market,
            interval=interval,
            period=period,
            indicators=indicator_list,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {exc}",
        ) from exc

    return result
