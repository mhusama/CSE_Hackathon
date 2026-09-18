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
    gemini_api_key: str = ""
    llm_model: str = "gemini-3.6-flash"

    # Server Configuration
    port: int = 8000
    log_level: str = "info"

    # Optimization
    solver_timeout_seconds: float = 25.0
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
