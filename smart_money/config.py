"""Smart Money 設定中心.

所有 env var 與 runtime thresholds 集中在此,避免散落在各 module.
Phase 0 提供 skeleton + 預設值;各 Phase 實作時直接 import.

使用:
    from smart_money.config import settings
    settings.hl_api_url            # 讀值
    settings.ranking.top_n         # 巢狀 group

Env var 以 `SM_` 為 prefix,pydantic-settings 自動讀取.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ------------------------------------------------------------------ #
# 排名演算法 thresholds (Phase 2/3 會實際讀取)
# ------------------------------------------------------------------ #
class RankingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SM_RANKING_", extra="ignore")

    # 硬門檻 (filters.py)
    min_sample_size: int = 50           # 最少已平倉交易筆數
    min_active_days: int = 30
    max_symbol_concentration: float = 0.80   # 單幣種佔比上限
    min_avg_holding_seconds: int = 300       # 過濾 HFT/bot

    # 權重 (scorer.py) — 總和不強制為 1,scorer 內部 normalize
    w_sortino: float = 0.25
    w_profit_factor: float = 0.20
    w_dd_recovery: float = 0.15
    w_holding_cv: float = 0.10
    w_regime_stability: float = 0.15
    w_martingale_penalty: float = 0.20       # 扣分權重

    # 排名輸出
    top_n: int = 50
    whitelist_size: int = 10


# ------------------------------------------------------------------ #
# 執行層設定 (Phase 5)
# ------------------------------------------------------------------ #
class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SM_EXEC_", extra="ignore")

    # 跟單規模控管
    total_capital_usdt: float = 1000.0       # 總資金
    max_exposure_per_wallet: float = 0.20    # 單錢包最大曝險
    max_concurrent_correlated: int = 3       # 同方向高相關資產上限
    signal_latency_budget_sec: int = 15      # 訊號延遲 > 此值拒單

    # Kill switch
    daily_loss_circuit_breaker: float = 0.05  # 日虧 5% 暫停
    consecutive_loss_days_to_shadow: int = 3  # 連虧 3 日進 shadow

    # 加碼路徑 (Phase 5 §3)
    capital_ramp_steps_usdt: tuple[float, ...] = (100.0, 300.0, 600.0, 1000.0)


# ------------------------------------------------------------------ #
# 主設定
# ------------------------------------------------------------------ #
class Settings(BaseSettings):
    """整個 smart_money 系統的設定根節點."""

    model_config = SettingsConfigDict(
        env_prefix="SM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 執行模式 — shadow 為預設,避免誤觸實盤
    mode: Literal["shadow", "live"] = "shadow"

    # Hyperliquid
    hl_api_url: str = "https://api.hyperliquid.xyz"
    hl_ws_url: str = "wss://api.hyperliquid.xyz/ws"

    # OKX (執行場館,沿用現有 .env 中的 keys)
    okx_api_key: str = Field(default="", alias="OKX_API_KEY")
    okx_api_secret: str = Field(default="", alias="OKX_API_SECRET")
    okx_api_passphrase: str = Field(default="", alias="OKX_API_PASSPHRASE")

    # Supabase (沿用現有)
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_key: str = Field(default="", alias="SUPABASE_SERVICE_KEY")

    # AI layer (Phase 6,預設 disabled)
    ai_layer_enabled: bool = False
    ai_endpoint: str = "https://api.acetoken.ai/v1/messages"
    ai_api_key: str = Field(default="", alias="ACETOKEN_API_KEY")
    ai_model: str = "claude-opus-4-6"
    ai_cache_ttl_hours: int = 72
    ai_daily_budget_usd: float = 2.0

    # 子設定
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)


settings = Settings()


__all__ = ["Settings", "RankingSettings", "ExecutionSettings", "settings"]
