import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    supabase_url: str = ""
    supabase_key: str = ""
    fugle_api_key: str = ""
    alpha_vantage_api_key: str = ""
    cors_origins: str = "http://localhost:3000"
    initial_capital: float = 300.0
    log_level: str = "INFO"

    def validate_startup(self) -> None:
        """Log warnings for missing optional config at startup."""
        if not self.supabase_url or not self.supabase_key:
            logger.warning("SUPABASE_URL/SUPABASE_KEY not set — using JSON fallback for trade storage")
        if not self.fugle_api_key:
            logger.info("FUGLE_API_KEY not set — Taiwan stock data unavailable")
        if not self.alpha_vantage_api_key:
            logger.info("ALPHA_VANTAGE_API_KEY not set — US stock data limited")


settings = Settings()
settings.validate_startup()
