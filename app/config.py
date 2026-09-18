"""Application configuration via environment variables."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM Configuration
    gemini_api_key: str = ""
    llm_model: str = "gemini-2.0-flash"

    # Server Configuration
    port: int = 8000
    log_level: str = "info"

    # Optimization
    solver_timeout_seconds: float = 25.0
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
