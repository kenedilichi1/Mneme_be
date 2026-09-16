from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Mneme API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    otp_expire_minutes: int = 10
    jwt_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    RESEND_API_KEY: str | None = None
    EMAIL_FROM: str = "Mneme <onboarding@resend.dev>"

    CORS_ORIGINS: list[str] = ["*"]

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    ENVIRONMENT: Literal["development", "testing", "production"] = "development"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "mneme"

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    B2_KEY_ID: str
    B2_APPLICATION_KEY: str
    B2_BUCKET_NAME: str

    @property
    def get_db_name(self) -> str:
        return f"{self.POSTGRES_DB}_{self.ENVIRONMENT}"

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy connection URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.get_db_name}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync connection URL used by Alembic migrations (psycopg2 driver)."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.get_db_name}"
        )


settings = Settings()
