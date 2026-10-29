"""Background jobs. Phase 1 uses APScheduler in-process, which the design doc
allows for the MVP; the Celery/Redis seam is a swap of this module."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app import lifecycle
from app.db import SessionLocal
from app.enums import TaskStatus, TaskType
from app.models import Issue, Task, utcnow
from app.routers.reports import OPEN_STATUSES

log = logging.getLogger("starnet.jobs")


def expire_stale_confirmations() -> int:
    """A repair nobody confirms within the window is not a repair.

    The issue reopens rather than quietly closing, which is what keeps the
    final vesting slice honest: silence does not pay.
    """
    expired = 0
    with SessionLocal() as db:
        tasks = db.execute(
            select(Task).where(
                Task.type == TaskType.CONFIRM,
                Task.status == TaskStatus.OPEN,
                Task.expires_at.is_not(None),
                Task.expires_at < utcnow(),
            )
        ).unique().scalars().all()

        for task in tasks:
            task.status = TaskStatus.EXPIRED
            lifecycle.reopen(
                db,
                task.issue,
                reason="No fix-confirmation inside the confirmation window",
            )
            expired += 1

        if expired:
            db.commit()
            log.info("Expired %d unconfirmed repairs", expired)

    return expired


def expire_stale_verifications() -> int:
    """A verify task nobody attended inside the window moves to someone else.

    Deliberately NOT a rejection. An unattended task says something about
    verifier coverage in that ward, not about whether the fault is real, and
    rejecting on silence would quietly forfeit an honest reporter's stake
    because nobody happened to be nearby. After `max_verify_reroutes` the
    issue simply waits, unpaid and unrejected.
    """
    rerouted = 0
    abandoned = 0
    with SessionLocal() as db:
        tasks = db.execute(
            select(Task).where(
                Task.type == TaskType.VERIFY,
                Task.status == TaskStatus.OPEN,
                Task.expires_at.is_not(None),
                Task.expires_at < utcnow(),
            )
        ).unique().scalars().all()

        for task in tasks:
            if lifecycle.reroute_verification(db, task) is not None:
                rerouted += 1
            else:
                abandoned += 1

        if tasks:
            db.commit()
            log.info(
                "Verify tasks expired: %d re-routed, %d left waiting", rerouted, abandoned
            )

    return rerouted + abandoned


def reprioritise_open_issues() -> int:
    """Age the open queue up.

    The escalation component of P(I) grows while an issue sits unfixed, so a
    neglected fault climbs the ranking on its own instead of being overtaken
    forever by newer reports. Only the ranking moves -- `base_priority` and
    `reward_total` are frozen at filing, so this can never change what a
    contributor was promised.

    Priority is a continuous function of age, so this job does not converge on
    a fixed value the way the expiry jobs do: every run writes the value for
    now. That makes it safe to miss and safe to repeat, which is the property
    that matters -- the result never depends on how often it has run.

    Returns the number of issues whose ranking actually moved.
    """
    touched = 0
    with SessionLocal() as db:
        issues = db.execute(
            select(Issue).where(Issue.status.in_(OPEN_STATUSES))
        ).unique().scalars().all()

        for issue in issues:
            before = issue.priority
            after = lifecycle.rerun_priority(db, issue)
            if abs(after - before) > 1e-9:
                touched += 1

        if touched:
            db.commit()
            log.info("Re-ranked %d open issues on age", touched)

    return touched
