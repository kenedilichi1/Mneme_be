from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

# ---------------------------------------------------------------------------
# Pull database URL and metadata from the application
# ---------------------------------------------------------------------------

from app.core.config import settings  
from app.core.db.base import Base 
import app.core.db.models

# Override the URL in alembic.ini with the value from settings.
# Tests inject their own URL through `config.attributes` to run migrations
# against a throwaway container.
_override_url = alembic_config.attributes.get("sqlalchemy_url_override")
if _override_url:
    # ConfigParser interpolation: literal % must be doubled
    alembic_config.set_main_option("sqlalchemy.url", _override_url.replace("%", "%%"))
else:
    alembic_config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# Required for autogenerate to detect model changes
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection needed)."""
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Emit CREATE EXTENSION for pgvector on first run
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
