"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://driftwatch:change-this-local-password@localhost:5432/driftwatch"
    log_level: str = "INFO"
    source_root: Path = Field(
        default=Path(__file__).resolve().parents[3] / "demo",
        validation_alias="DRIFTWATCH_SOURCE_ROOT",
    )
    tofu_path: str = Field(default="tofu", validation_alias="DRIFTWATCH_TOFU_PATH")
    scan_timeout_seconds: int = Field(
        default=300, validation_alias="DRIFTWATCH_SCAN_TIMEOUT_SECONDS"
    )
    worker_poll_seconds: int = Field(default=3, validation_alias="DRIFTWATCH_WORKER_POLL_SECONDS")
    scheduler_poll_seconds: int = Field(
        default=30, validation_alias="DRIFTWATCH_SCHEDULER_POLL_SECONDS"
    )
    schedule_interval_seconds: int = Field(
        default=3600, validation_alias="DRIFTWATCH_SCHEDULE_INTERVAL_SECONDS"
    )
    github_token: SecretStr | None = Field(default=None, validation_alias="DRIFTWATCH_GITHUB_TOKEN")
    github_repository: str | None = Field(
        default=None, validation_alias="DRIFTWATCH_GITHUB_REPOSITORY"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
