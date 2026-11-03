"""Alembic environment.

Two things make this different from the scaffolded default:

  - The URL comes from the app's own settings, so there is exactly one place a
    database is configured and `alembic.ini` holds no credentials.
  - PostGIS types are handled through geoalchemy2's alembic helpers. Without
    them, autogenerate emits a second CREATE INDEX for the spatial index that
    PostGIS already made for the Geography column, and the migration fails on a
    fresh database.
"""
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401  (imported for its side effect: maps the tables)

config = context.config

# Fall back to the app's settings, but do not override a URL that was set
# explicitly -- by `alembic -x`, by a caller building a Config in code, or by
# the tests, which migrate a throwaway database that is deliberately not the
# one STARNET_DATABASE_URL points at.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.database_url)

# Logging is only configured when Alembic is driven from the command line.
# When the API applies migrations in process at startup, it passes
# configure_logger=False, because alembic.ini's [logger_root] sets WARNING --
# which would override the app's INFO level and leave it serving traffic in
# near silence: no request logs, no background-job logs. (The Alembic cookbook
# recommends this attribute for exactly this situation.) disable_existing_loggers
# is off as well, so a CLI run cannot switch off loggers it did not create.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _common(**extra):
    return dict(
        target_metadata=target_metadata,
        # Catch column type changes, not just added and dropped columns --
        # otherwise a Numeric(18,4) quietly becoming Numeric(10,2) would not
        # show up, and this ledger cannot afford that.
        compare_type=True,
        compare_server_default=True,
        include_object=alembic_helpers.include_object,
        render_item=alembic_helpers.render_item,
        process_revision_directives=alembic_helpers.writer,
        **extra,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_common())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
