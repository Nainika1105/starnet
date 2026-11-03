"""The migrations must describe the same schema as the models.

This is the test that makes migrations trustworthy rather than ceremonial. A
model change with no accompanying revision is the normal way a migration chain
rots: everything passes locally, because the test database was built from the
models, and then the deployment runs `upgrade head` and gets a schema that is
one column short.

So this builds a database from the migrations ALONE, with no `create_all`
anywhere, and asks Alembic whether autogenerate would still find a difference.
If it would, the models moved and nobody wrote the revision.
"""
from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.config import settings
from app.db import Base

API_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _config(url: str) -> Config:
    """Mirrors how the app builds its config, including leaving logging alone."""
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def bare_database():
    """A database with nothing in it -- not even PostGIS.

    Deliberately bare: a managed Postgres hands you exactly this, so the
    migration has to be able to install the extension itself.
    """
    name = f"starnet_mig_{uuid.uuid4().hex[:8]}"
    admin_url = settings.database_url.rsplit("/", 1)[0] + "/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    url = settings.database_url.rsplit("/", 1)[0] + f"/{name}"
    try:
        yield url
    finally:
        engine = create_engine(url)
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_migrations_build_the_schema_from_nothing(bare_database):
    """Including the PostGIS extension, which the geography column needs."""
    command.upgrade(_config(bare_database), "head")

    engine = create_engine(bare_database)
    with engine.connect() as conn:
        extensions = set(
            conn.execute(text("SELECT extname FROM pg_extension")).scalars()
        )
        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars()
        )
    engine.dispose()

    assert "postgis" in extensions
    for table in (
        "users",
        "issues",
        "reports",
        "tasks",
        "contributions",
        "reward_ledger",
        "redemptions",
        "issue_events",
    ):
        assert table in tables, table


def test_the_models_have_not_drifted_from_the_migrations(bare_database):
    """The guard. If this fails, run:  alembic revision --autogenerate"""
    command.upgrade(_config(bare_database), "head")

    engine = create_engine(bare_database)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    # geoalchemy2 manages the spatial index itself, and its
                    # internal tables are not ours to diff.
                    "include_object": __import__(
                        "geoalchemy2.alembic_helpers", fromlist=["include_object"]
                    ).include_object,
                },
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], (
        "The models and the migrations disagree. Generate a revision with "
        f"`alembic revision --autogenerate`. Differences: {diff}"
    )


def test_the_ledger_amount_column_keeps_its_precision(bare_database):
    """Numeric(18,4) is not decoration.

    Points are money. If a migration ever narrowed this column the ledger would
    start rounding silently, which no amount of Decimal care in the engine
    could recover from -- so it is asserted against the real database, not
    against the model that generated it.
    """
    command.upgrade(_config(bare_database), "head")

    engine = create_engine(bare_database)
    with engine.connect() as conn:
        precision, scale = conn.execute(
            text(
                "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                "WHERE table_name = 'reward_ledger' AND column_name = 'amount'"
            )
        ).one()
    engine.dispose()

    assert (precision, scale) == (18, 4)


def test_the_idempotency_key_is_unique_in_the_database(bare_database):
    """The double-pay guard is a database constraint, not application code.

    The engine relies on an IntegrityError to tell it a transition was already
    recorded. If the unique index were missing, that check would silently pass
    and a retry would pay twice.
    """
    command.upgrade(_config(bare_database), "head")

    engine = create_engine(bare_database)
    with engine.connect() as conn:
        unique = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM pg_index i
                JOIN pg_class t ON t.oid = i.indrelid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)
                WHERE t.relname = 'reward_ledger'
                  AND a.attname = 'idempotency_key'
                  AND i.indisunique
                """
            )
        ).scalar_one()
    engine.dispose()

    assert unique >= 1


def test_applying_migrations_does_not_silence_the_application(bare_database):
    """Alembic's env.py configures logging from alembic.ini, whose root logger
    is WARNING. The API applies migrations in process at startup, so letting
    that run would reset the app's INFO level for the rest of its life -- it
    would serve traffic in near silence: no request logs, no background-job
    logs. Silent success is the hardest kind of breakage to notice.

    The level is set here rather than imported from the app because pytest owns
    the root logger during a test run, so `logging.basicConfig` is a no-op and
    the app's own INFO setting never takes effect. What matters either way is
    the same: whatever level was configured before the migration is still in
    force after it.
    """
    import logging

    root = logging.getLogger()
    app_logger = logging.getLogger("starnet")
    original = root.level
    root.setLevel(logging.INFO)
    try:
        assert app_logger.isEnabledFor(logging.INFO), "precondition"

        command.upgrade(_config(bare_database), "head")

        assert app_logger.disabled is False, (
            "migrations disabled the app's logger -- env.py needs "
            "disable_existing_loggers=False"
        )
        assert app_logger.isEnabledFor(logging.INFO), (
            "migrations reset the log level -- env.py must skip fileConfig when "
            "the caller passes configure_logger=False"
        )

        received: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                received.append(record.getMessage())

        handler = Capture()
        app_logger.addHandler(handler)
        try:
            app_logger.info("after migrations")
        finally:
            app_logger.removeHandler(handler)

        assert received == ["after migrations"]
    finally:
        root.setLevel(original)
