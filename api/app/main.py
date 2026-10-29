from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401  (registers the mapped classes)
from app.config import settings
from app.db import Base, engine
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
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    log.info("STAR-Net API ready (g_verification=%s)", settings.g_verification)

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(
    title="STAR-Net API",
    version="0.1.0",
    summary="Civic fault reporting where reward vests on confirmed resolution.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, reports.router, issues.router, tasks.router, wallet.router, admin.router):
    app.include_router(r)

Path(settings.media_root).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_root), name="media")


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
