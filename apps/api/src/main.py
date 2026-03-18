import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.routers import analysis, market_data
from src.routers.strategy import router as strategy_router

app = FastAPI(
    title="AMBMH API - 交易策略輔助顧問系統",
    version="0.1.0",
    description="Multi-market (TW stocks, US stocks, crypto) trading strategy advisory system.",
)

# CORS: configurable via CORS_ORIGINS env var (comma-separated)
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router)
app.include_router(market_data.router)
app.include_router(strategy_router)


@app.get("/health")
async def health_check() -> dict:
    """Health check with dependency status."""
    checks: dict = {"api": "ok"}

    # Check Supabase connectivity
    try:
        from src.services.supabase_client import get_supabase
        sb = get_supabase()
        if sb:
            sb.table("instruments").select("id").limit(1).execute()
            checks["supabase"] = "ok"
        else:
            checks["supabase"] = "not_configured"
    except Exception as e:
        checks["supabase"] = f"error: {e}"

    overall = "ok" if all(
        v in ("ok", "not_configured") for v in checks.values()
    ) else "degraded"

    return {"status": overall, "checks": checks}
