"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://driftwatch:change-this-local-password@localhost:5432/driftwatch"
    log_level: str = "INFO"
    jwt_secret: SecretStr = Field(
        default=SecretStr("driftwatch-local-development-secret-not-for-production"),
        validation_alias="DRIFTWATCH_JWT_SECRET",
    )
    jwt_ttl_minutes: int = Field(
        default=30, ge=5, le=1440, validation_alias="DRIFTWATCH_JWT_TTL_MINUTES"
    )
    environment: str = Field(default="development", validation_alias="DRIFTWATCH_ENV")
    source_root: Path = Field(
        default=Path(__file__).resolve().parents[3] / "demo",
        validation_alias="DRIFTWATCH_SOURCE_ROOT",
    )
    tofu_path: str = Field(default="tofu", validation_alias="DRIFTWATCH_TOFU_PATH")
    kubectl_path: str = Field(default="kubectl", validation_alias="DRIFTWATCH_KUBECTL_PATH")
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

    @model_validator(mode="after")
    def validate_security_settings(self):
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < 32:
            raise ValueError("DRIFTWATCH_JWT_SECRET must contain at least 32 characters")
        if self.environment.lower() == "production" and (
            secret.startswith("driftwatch-local-development-")
            or "replace-this" in secret.lower()
            or "change-this" in secret.lower()
        ):
            raise ValueError("Set a unique DRIFTWATCH_JWT_SECRET before production deployment")
        return self


settings = Settings()
