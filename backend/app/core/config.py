import os
import json
from typing import List, Optional, Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Smart Market Watchlist API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = int(os.getenv("PORT", "8080"))
    DEBUG: bool = True
    ENVIRONMENT: str = "development"
    
    # CORS
    ALLOWED_ORIGINS: List[str] = ["*"]
    
    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            v = v.strip()
            if v == "*":
                return ["*"]
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v
    
    # Redis configuration
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_IN_MEMORY_REDIS_FALLBACK: bool = True
    
    # BigQuery configuration
    BIGQUERY_PROJECT_ID: Optional[str] = None
    BIGQUERY_DATASET: str = "smart_watchlist"
    BIGQUERY_TABLE: str = "ticks"
    USE_DUCKDB_FALLBACK: bool = True
    DUCKDB_PATH: str = ":memory:"  # Or file path for persistence
    
    # Firebase configuration
    FIREBASE_PROJECT_ID: Optional[str] = None
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None
    USE_DEV_AUTH_FALLBACK: bool = True
    
    # Auth JWT settings
    SECRET_KEY: str = "smart-market-watchlist-secret-key-2026-production-ready"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    
    # WebSocket & Engine settings
    TICK_INTERVAL_SECONDS: float = 1.0
    PING_INTERVAL_SECONDS: float = 15.0
    PONG_TIMEOUT_SECONDS: float = 10.0
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
