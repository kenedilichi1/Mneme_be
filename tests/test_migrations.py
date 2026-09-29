"""MNE-26: migrations must reproduce exactly what the models describe.

The rest of the suite builds its schema with `Base.metadata.create_all`, so a
drift between the Alembic history and the SQLAlchemy models (e.g. the historic
1536-vs-768 embedding dimension mismatch) is invisible there.  These tests run
the real migration chain against a fresh container instead.
"""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

from app.core.db.base import Base
import app.core.db.models  # noqa: F401 – registers all models with Base.metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
PGVECTOR_IMAGE = "pgvector/pgvector:pg18"

APP_TABLES = {"users", "otp_codes", "oauth_accounts", "documents", "document_chunks"}


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """A fresh database with `alembic upgrade head` already applied."""
    with PostgresContainer(PGVECTOR_IMAGE) as pg:
        url = pg.get_connection_url()
        command.upgrade(_alembic_config(url), "head")
        yield url


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["sqlalchemy_url_override"] = url
    return cfg


def test_migrated_schema_matches_models(migrated_db: str):
    engine = create_engine(migrated_db)
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "include_schemas": True},
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()

    assert diff == [], f"Migrations and models disagree:\n{diff}"


def test_downgrade_to_base_succeeds(migrated_db: str):
    command.downgrade(_alembic_config(migrated_db), "base")

    engine = create_engine(migrated_db)
    with engine.connect() as connection:
        remaining = set(inspect(connection).get_table_names())
        leftover = remaining & APP_TABLES
    engine.dispose()

    assert not leftover, f"Tables survived downgrade to base: {leftover}"


def test_upgrade_head_is_idempotent(migrated_db: str):
    # Re-running after test_downgrade_to_base_succeeds brings the database back
    # to head; running it again on an already-migrated database must be a no-op.
    command.upgrade(_alembic_config(migrated_db), "head")
    command.upgrade(_alembic_config(migrated_db), "head")

    engine = create_engine(migrated_db)
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()

    assert version is not None


# ---------------------------------------------------------------------------
# MNE-13 — email normalization migration
# ---------------------------------------------------------------------------


def _insert_user(connection, user_id: uuid.UUID, email: str, created_at: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO users (id, email, first_name, last_name, created_at, updated_at)
            VALUES (:id, :email, NULL, NULL, :created_at, :created_at)
            """
        ),
        {"id": str(user_id), "email": email, "created_at": created_at},
    )


def test_email_normalization_migration_merges_case_duplicates(migrated_db: str):
    """Legacy mixed-case rows fold into one canonical account, children kept."""
    # Start from the schema as it was before this migration, where the two
    # case-variants could legally coexist.
    command.downgrade(_alembic_config(migrated_db), "b1c07d2e4a56")

    older, loser, solo = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    otp_id = uuid.uuid4()
    engine = create_engine(migrated_db)
    with engine.begin() as connection:
        _insert_user(connection, older, "Dup@Example.com", "2026-01-01 00:00:00+00")
        _insert_user(connection, loser, "dup@example.com", "2026-06-01 00:00:00+00")
        _insert_user(connection, solo, "solo@example.com", "2026-03-01 00:00:00+00")
        # Child row belonging to the newer duplicate: must survive, re-pointed
        # at the older account, not be cascaded away with it.
        connection.execute(
            text(
                """
                INSERT INTO otp_codes (id, user_id, code_hash, expires_at, created_at)
                VALUES (:id, :user_id, 'hash', now(), now())
                """
            ),
            {"id": str(otp_id), "user_id": str(loser)},
        )

    command.upgrade(_alembic_config(migrated_db), "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, email FROM users "
                "WHERE lower(email) = 'dup@example.com'"
            )
        ).fetchall()
        assert len(rows) == 1, f"case-duplicates survived: {rows}"
        assert rows[0].id == older
        assert rows[0].email == "dup@example.com"

        otp_user = connection.execute(
            text("SELECT user_id FROM otp_codes WHERE id = :id"),
            {"id": str(otp_id)},
        ).scalar_one()
        assert otp_user == older

        solo_email = connection.execute(
            text("SELECT email FROM users WHERE id = :id"), {"id": str(solo)}
        ).scalar_one()
        assert solo_email == "solo@example.com"

        # The database itself now refuses a case-variant duplicate
        with pytest.raises(IntegrityError):
            _insert_user(connection, uuid.uuid4(), "DUP@example.com", "2026-07-01 00:00:00+00")
    engine.dispose()
