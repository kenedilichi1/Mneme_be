from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_EXAMPLE_JWT_SECRETS = {"change-me", "changeme", "secret"}
_MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Validation errors would otherwise echo setting values (secrets) into logs
        hide_input_in_errors=True,
    )

    APP_NAME: str = "Mneme API"
    APP_VERSION: str = "1.0.0"
    # Dev conveniences: logs OTP codes and skips sending email. Forbidden in production.
    DEBUG: bool = False
    SQL_ECHO: bool = False

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    # Key for the OTP HMAC (see secrets.hash_otp_code). Required in production.
    # Rotating it invalidates outstanding OTPs only (they live 10 minutes).
    otp_hash_key: str = ""
    otp_expire_minutes: int = 10
    jwt_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    RESEND_API_KEY: str
    EMAIL_FROM: str = "Mneme <onboarding@resend.dev>"

    EMBEDDING_MODEL: str = "nomic-ai/nomic-embed-text-v1.5"
    # Pin the model to an exact HuggingFace commit so embeddings are
    # reproducible across deploys. Never float on a branch: bump this only
    # deliberately, together with re-embedding existing chunks (MNE-18).
    EMBEDDING_MODEL_REVISION: str = "e9b6763023c676ca8431644204f50c2b100d9aab"
    EMBEDDING_DIMENSION: int = 768
    # Token budget per chunk, counted with the Nomic tokenizer (MNE-18)
    MAX_CHUNK_TOKENS: int = 512


    # Only browsers enforce CORS; native mobile clients don't need an entry
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
    # Bucket's S3 endpoint, e.g. https://s3.us-west-004.backblazeb2.com (B2 → Buckets → Endpoint)
    B2_S3_ENDPOINT: str

    MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024
    UPLOAD_URL_EXPIRE_SECONDS: int = 900

    # --- OTP lockout (per email, across all OTPs) ---
    OTP_MAX_FAILED_ATTEMPTS: int = 5
    OTP_LOCKOUT_MINUTES: int = 15

    # --- Rate limits (shared across processes, stored in Postgres) ---
    GLOBAL_RATE_LIMIT_PER_MINUTE: int = 100
    OTP_REQUEST_LIMIT_PER_MINUTE: int = 5
    OTP_VERIFY_LIMIT_PER_MINUTE: int = 10
    # Enable only when running behind a trusted reverse proxy that sets
    # X-Forwarded-For; otherwise clients can spoof the header and rotate IPs.
    TRUST_PROXY_HEADERS: bool = False

    RABBITMQ_URL: str
    # Jobs a worker holds at once; keep at 1 so queued jobs don't hit the broker's ack timeout
    WORKER_PREFETCH_COUNT: int = 1

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _check_production_safety(self) -> "Settings":
        if not self.is_production:
            return self

        problems = []
        if self.DEBUG:
            problems.append("DEBUG must be false (it logs OTP codes and skips sending email)")
        if self.SQL_ECHO:
            problems.append("SQL_ECHO must be false (it logs every query)")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must list explicit origins, not '*'")
        if (
            len(self.jwt_secret) < _MIN_JWT_SECRET_LENGTH
            or self.jwt_secret.lower() in _EXAMPLE_JWT_SECRETS
        ):
            problems.append(
                f"jwt_secret must be at least {_MIN_JWT_SECRET_LENGTH} random characters"
            )
        if len(self.otp_hash_key) < _MIN_JWT_SECRET_LENGTH:
            problems.append(
                f"otp_hash_key must be at least {_MIN_JWT_SECRET_LENGTH} random characters"
            )
        revision = self.EMBEDDING_MODEL_REVISION
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision.lower()):
            problems.append(
                "EMBEDDING_MODEL_REVISION must be a pinned 40-character commit hash"
            )
        if not self.RESEND_API_KEY:
            problems.append("RESEND_API_KEY must be set, or OTP emails are never sent")

        if problems:
            raise ValueError(
                "Unsafe production configuration:\n- " + "\n- ".join(problems)
            )
        return self

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
