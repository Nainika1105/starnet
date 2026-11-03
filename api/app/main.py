from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (registers the mapped classes)
from app import storage
from app.config import settings
from app.jobs import (
    expire_stale_confirmations,
    expire_stale_verifications,
    reprioritise_open_issues,
)
from app.routers import admin, auth, issues, reports, tasks, wallet

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("starnet")

scheduler = BackgroundScheduler()


def init_db() -> None:
    """Bring the schema up to date.

    Migrations, not `create_all`. create_all builds a correct schema on an
    empty database and then silently does nothing forever: it cannot add a
    column, widen a numeric, or create an enum value on a database that already
    has tables. For a ledger whose precision is load-bearing, "silently does
    nothing" is the wrong failure mode.

    A database created by the old create_all path has no alembic_version table,
    so `upgrade` would try to create tables that already exist. Stamp it once:

        alembic stamp head
    """
    if not settings.migrate_on_startup:
        log.info("Skipping migrations (STARNET_MIGRATE_ON_STARTUP=false)")
        return

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.normalised_database_url)
    # Leave this process's logging alone -- alembic.ini configures the root
    # logger at WARNING, which would silence the app's own INFO output for the
    # rest of its life. See the note in migrations/env.py.
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def check_production_config() -> None:
    """Refuse to boot an unsafe production instance.

    The failure modes this catches are not subtle -- a known JWT secret, or a
    public instance seeded with six accounts sharing a password printed in the
    README, one of them an admin. Both would be silent: the app would come up
    and serve traffic perfectly well while anyone could sign in as the city.
    """
    if not settings.is_public:
        return

    problems = settings.production_problems()
    if problems:
        listed = "\n".join(f"  {n}. {p}" for n, p in enumerate(problems, 1))
        raise RuntimeError(
            f"Refusing to start: STARNET_ENVIRONMENT={settings.mode} with "
            f"{len(problems)} unsafe setting(s).\n{listed}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_production_config()
    init_db()

    if settings.seed_on_startup:
        from app.seed import seed_if_empty

        seed_if_empty()

    # Three recurring jobs. None of them accumulates: each re-reads state and
    # computes the answer for right now, so a missed run self-corrects and a
    # doubled run changes nothing. The two expiry jobs are also idempotent in
    # the strict sense -- they find nothing to do the second time.
    scheduler.add_job(
        expire_stale_confirmations,
        "interval",
        minutes=15,
        id="expire_stale_confirmations",
        replace_existing=True,
    )
    scheduler.add_job(
        expire_stale_verifications,
        "interval",
        minutes=15,
        id="expire_stale_verifications",
        replace_existing=True,
    )
    scheduler.add_job(
        reprioritise_open_issues,
        "interval",
        hours=1,
        id="reprioritise_open_issues",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "STAR-Net API ready (env=%s, confidence=%s, g_verification=%s)",
        settings.environment,
        settings.confidence_mode,
        settings.g_verification,
    )

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(
    title="STAR-Net API",
    version="0.1.0",
    summary="Civic fault reporting where reward vests on confirmed resolution.",
    lifespan=lifespan,
)

# Auth travels as a bearer token in a header, not a cookie, so credentials are
# not needed here -- and allowing them alongside a wildcard origin is something
# browsers reject outright. The allowlist comes from config: localhost in dev,
# the dashboard's real origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

for r in (auth.router, reports.router, issues.router, tasks.router, wallet.router, admin.router):
    app.include_router(r)

# How photos get served depends on where they live. With object storage the API
# never touches the bytes -- `storage.url_for` hands out a presigned URL and the
# client fetches the store directly.
_BACKEND = settings.storage_backend.strip().lower()

if _BACKEND == "db":

    @app.get("/media/{key}", tags=["meta"])
    def media(key: str) -> Response:
        """Serve a photo out of the database.

        Cached hard and immutably: a key is a content-addressed-style random
        name that is never reused, so a client that has one copy never needs
        another.
        """
        found = storage.load(key)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such photo")
        data, content_type = found
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

elif _BACKEND != "s3":
    Path(settings.media_root).mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.media_root), name="media")


def mount_dashboard() -> None:
    """Serve the built dashboard from this app, when it is present.

    Mounted last and at "/", so every API route above still wins; what falls
    through is a dashboard path. `html=True` makes unknown paths return
    index.html, which is what a single-page app needs.

    This is what lets one URL serve the whole demo. Without it the dashboard is
    a separate origin and needs its own host and its own CORS entry.
    """
    directory = settings.dashboard_dir.strip()
    if not directory:
        return
    path = Path(directory)
    if not (path / "index.html").exists():
        log.warning("STARNET_DASHBOARD_DIR=%s has no index.html; not serving it", directory)
        return

    app.mount("/", StaticFiles(directory=str(path), html=True), name="dashboard")
    log.info("Serving the dashboard from %s", path)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/config/mechanism", tags=["meta"])
def mechanism_config() -> dict:
    """The tunable knobs, exposed so a demo can show what they are set to.

    g_verification is the anti-fraud knob: it caps what a fabricated report can
    ever collect, because a fake fault cannot be resolved or confirmed.
    """
    return {
        "r0_base_points": settings.r0_base_points,
        "role_split": {
            "discoverer": settings.f_discoverer,
            "corroborator": settings.f_corroborator,
            "verifier": settings.f_verifier,
            "confirmer": settings.f_confirmer,
        },
        "vesting_split": {
            "verification": settings.g_verification,
            "resolution": settings.g_resolution,
            "confirmation": settings.g_confirmation,
        },
        "thresholds": {"theta_lo": settings.theta_lo, "theta_hi": settings.theta_hi},
        "confidence_mode": settings.confidence_mode,
        "clustering_enabled": settings.clustering_enabled,
        "weights": {
            "w0": settings.w0,
            "s_img": settings.w_img,
            "s_geo": settings.w_geo,
            "s_cor": settings.w_cor,
            "trust": settings.w_trust,
        },
        "clustering": {
            "distance_m": settings.cluster_distance_m,
            "window_hours": settings.cluster_window_hours,
        },
        "trust_tiers": {
            "trusted_min": settings.tier_trusted_min,
            "verifier_min": settings.tier_verifier_min,
            "decay_per_week": settings.trust_decay_per_week,
        },
        "max_fabrication_payout_fraction": settings.g_verification * settings.f_discoverer,
    }


# Registered last on purpose: a StaticFiles mount at "/" would otherwise
# shadow routes declared after it.
mount_dashboard()
