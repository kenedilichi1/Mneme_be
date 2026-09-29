import pytest
from pydantic import ValidationError

from app.core.config import Settings

SAFE_PRODUCTION = dict(
    ENVIRONMENT="production",
    DEBUG=False,
    SQL_ECHO=False,
    CORS_ORIGINS=["https://app.mneme.example"],
    jwt_secret="x" * 32,
    otp_hash_key="o" * 32,
    RESEND_API_KEY="re_test",
    EMBEDDING_MODEL_REVISION="e9b6763023c676ca8431644204f50c2b100d9aab",
    POSTGRES_USER="u",
    POSTGRES_PASSWORD="p",
    B2_KEY_ID="k",
    B2_APPLICATION_KEY="k",
    B2_BUCKET_NAME="b",
    B2_S3_ENDPOINT="https://s3.us-west-004.backblazeb2.com",
    RABBITMQ_URL="amqp://localhost/",
)


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PRODUCTION, **overrides})


def test_debug_defaults_to_false():
    fields = {k: v for k, v in SAFE_PRODUCTION.items() if k != "DEBUG"}
    assert Settings(_env_file=None, **fields).DEBUG is False


def test_safe_production_config_loads():
    assert make().is_production


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"DEBUG": True}, "DEBUG"),
        ({"SQL_ECHO": True}, "SQL_ECHO"),
        ({"CORS_ORIGINS": ["*"]}, "CORS_ORIGINS"),
        ({"jwt_secret": "short"}, "jwt_secret"),
        ({"jwt_secret": "change-me"}, "jwt_secret"),
        ({"otp_hash_key": "short"}, "otp_hash_key"),
        ({"RESEND_API_KEY": ""}, "RESEND_API_KEY"),
        ({"EMBEDDING_MODEL_REVISION": "main"}, "EMBEDDING_MODEL_REVISION"),
        ({"EMBEDDING_MODEL_REVISION": ""}, "EMBEDDING_MODEL_REVISION"),
    ],
)
def test_unsafe_production_config_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        make(**overrides)


def test_all_problems_reported_together():
    with pytest.raises(ValidationError) as exc:
        make(DEBUG=True, jwt_secret="change-me")
    assert "DEBUG" in str(exc.value) and "jwt_secret" in str(exc.value)


def test_development_allows_dev_conveniences():
    settings = make(ENVIRONMENT="development", DEBUG=True, CORS_ORIGINS=["*"], jwt_secret="change-me")
    assert settings.DEBUG is True


def test_native_only_production_can_have_no_cors_origins():
    assert make(CORS_ORIGINS=[]).CORS_ORIGINS == []


def test_embedding_revision_default_is_pinned_commit():
    import re

    default = Settings.model_fields["EMBEDDING_MODEL_REVISION"].default
    assert re.fullmatch(r"[0-9a-f]{40}", default)
