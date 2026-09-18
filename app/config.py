"""Application configuration via environment variables and .env file."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Path to .env file in project root
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# Load .env file into os.environ if present (native standard library parsing)
if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    # LLM Configuration
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "open-mistral-nemo"
    # Second model tried if the primary one keeps failing (quota, outage, bad name)
    llm_fallback_model: str = "mistral-small-latest"

    # Server Configuration
    port: int = 8000
    log_level: str = "info"

    # Optimization
    solver_timeout_seconds: float = 25.0
    # Hard limit for ONE Mistral call
    llm_timeout_seconds: float = 10.0
    # Attempts per model (attempt 2+ carries feedback about what was wrong)
    llm_max_retries: int = 2
    # Total time the whole interpretation step may use (judge limit is 30 s per request)
    llm_total_budget_seconds: float = 22.0
    # In-memory cache of interpretations (same notes + battery capacity)
    llm_cache_size: int = 512
    # Minimum gap between Mistral calls (free tier is rate limited to about 1 request/second). 0 = off.
    mistral_min_interval_seconds: float = 1.1

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
