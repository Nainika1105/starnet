"""Issue lifecycle. Every vesting transition in STAR-Net is driven from here.

Status flow:
    REPORTED -> VERIFIED -> ASSIGNED -> RESOLVED -> CONFIRMED -> CLOSED
with REJECTED (confidence too low / admin audit) and REOPENED (the repair did
not hold) as the off-ramps.

The vesting stages hang off three of those transitions, and only those:
    VERIFIED   -> g_verification slice + the verifier fee
    RESOLVED   -> g_resolution slice
    CONFIRMED  -> g_confirmation slice + the fix-confirmer fee
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.engines import confidence as conf
from app.engines import priority as priority_engine
from app.engines import reward, routing, trust
from app.enums import (
    IssueStatus,
    Role,
    Stage,
    TaskStatus,
    TaskType,
    Verdict,
)
from app.models import Contribution, Issue, IssueEvent, Task, User, utcnow


def log_event(
    db: Session, issue: Issue, event_type: str, *, actor_id: uuid.UUID | None = None, **payload
) -> IssueEvent:
    event = IssueEvent(issue_id=issue.id, event_type=event_type, actor_id=actor_id, payload=payload)
    db.add(event)
    return event


def stages_passed(issue: Issue) -> list[Stage]:
    """Which vesting stages this issue has already fired -- used to start a
    late-joining contribution at the right point in the machine."""
    passed: list[Stage] = []
    if issue.verified_at:
        passed.append(Stage.VERIFICATION)
    if issue.resolved_at:
        passed.append(Stage.RESOLUTION)
    if issue.confirmed_at:
        passed.append(Stage.CONFIRMATION)
    return passed


def observed_report_rate(db: Session, ward: str, *, days: int = 30) -> float:
    since = utcnow() - timedelta(days=days)
    count = db.execute(
        select(func.count(Issue.id)).where(Issue.ward == ward, Issue.created_at >= since)
    ).scalar_one()
    return float(count)


def recompute_confidence(db: Session, issue: Issue) -> float:
    """Re-score the issue with its corroborations folded in.

    s_cor = 1 - exp(-S/s0) over the trust-weighted independent corroborators,
    which saturates: the first independent corroboration moves the needle a
    lot, the tenth barely at all, so flooding an issue with accounts buys
    almost nothing. s_img and s_geo stay the discoverer's -- those describe the
    original submission and do not change when someone agrees with it.

    Called from both the report endpoint and the seed, so a corroboration
    scores identically however it arrived.
    """
    weighted_sum = 0.0
    for report in issue.reports:
        if report.role != Role.CORROBORATOR:
            continue
        reporter = db.get(User, report.reporter_id)
        if reporter is None:
            continue
        weighted_sum += report.independence_weight * trust.trust_of(reporter)

    s_cor = conf.score_corroboration(weighted_sum)
    discoverer_report = next((r for r in issue.reports if r.role == Role.DISCOVERER), None)
    discoverer = db.get(User, issue.discoverer_id)

    issue.confidence = conf.confidence(
        s_img=discoverer_report.s_img if discoverer_report else 0.5,
        s_geo=discoverer_report.s_geo if discoverer_report else 0.5,
        s_cor=s_cor,
        tau=trust.trust_of(discoverer) if discoverer else 0.5,
    )
    return issue.confidence


# --- transitions -----------------------------------------------------------


def mark_verified(db: Session, issue: Issue, *, actor_id: uuid.UUID | None, reason: str) -> None:
    """Issue enters the municipal queue. Releases g_verification -- small on
    purpose, so a fabricated report that somehow clears confidence still earns
    almost nothing."""
    if issue.verified_at:
        return
    issue.status = IssueStatus.VERIFIED
    issue.verified_at = utcnow()

    # Any outstanding verify task is moot now, whichever route got the issue
    # here. Closing it keeps it off its assignee's task list and out of the
    # expiry job's way.
    for task in db.execute(
        select(Task).where(
            Task.issue_id == issue.id,
            Task.type == TaskType.VERIFY,
            Task.status == TaskStatus.OPEN,
        )
    ).scalars():
        if task.completed_at is None:
            task.status = TaskStatus.SUPERSEDED

    log_event(db, issue, "VERIFIED", actor_id=actor_id, reason=reason)
    reward.vest_stage(db, issue, Stage.VERIFICATION, note="Verification stage")


def assign(db: Session, issue: Issue, *, assignee_label: str, actor_id: uuid.UUID) -> None:
    issue.status = IssueStatus.ASSIGNED
    issue.assignee_label = assignee_label
    issue.assigned_at = utcnow()
    log_event(db, issue, "ASSIGNED", actor_id=actor_id, assignee=assignee_label)


def mark_resolved(db: Session, issue: Issue, *, actor_id: uuid.UUID) -> Task | None:
    """Work is reported done. Releases g_resolution and opens a fix-confirmer
    task -- the reward is not finished until someone independent stands at the
    spot and photographs the repair."""
    if issue.resolved_at and issue.status != IssueStatus.REOPENED:
        return None

    issue.status = IssueStatus.RESOLVED
    issue.resolved_at = utcnow()
    log_event(db, issue, "RESOLVED", actor_id=actor_id)
    reward.vest_stage(db, issue, Stage.RESOLUTION, note="Resolution stage")
    return open_confirmation_task(db, issue)


def open_confirmation_task(db: Session, issue: Issue) -> Task | None:
    """Open the fix-confirmer task.

    The three structural rules -- system-chosen, never a reporter, must clear
    the trust tier -- live in `engines.routing`. Returns None when no eligible
    independent user exists: the issue then waits rather than falling back to a
    compromised confirmer.
    """
    existing = db.execute(
        select(Task).where(
            Task.issue_id == issue.id,
            Task.type == TaskType.CONFIRM,
            Task.status == TaskStatus.OPEN,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    picked = routing.select_confirmer(db, issue)
    if picked is None:
        log_event(db, issue, "CONFIRMER_UNAVAILABLE")
        return None

    task = Task(
        type=TaskType.CONFIRM,
        issue_id=issue.id,
        assignee_id=picked.user.id,
        expires_at=utcnow() + timedelta(hours=settings.confirmation_window_hours),
    )
    db.add(task)
    db.flush()
    log_event(
        db,
        issue,
        "CONFIRM_TASK_OPENED",
        assignee=str(picked.user.id),
        distance_m=round(picked.distance_m) if picked.distance_m is not None else None,
        open_tasks=picked.open_tasks,
    )
    return task


def confirm_fix(
    db: Session, issue: Issue, task: Task, *, confirmer: User, verdict: Verdict, photo_key: str | None
) -> None:
    """The fix-confirmer's verdict. This is where reward actually vests -- or
    where the issue reopens and the confirmation slice is clawed back."""
    task.status = TaskStatus.COMPLETED
    task.verdict = verdict
    task.photo_path = photo_key
    task.completed_at = utcnow()

    if verdict == Verdict.CONFIRMED:
        issue.status = IssueStatus.CONFIRMED
        issue.confirmed_at = utcnow()
        log_event(db, issue, "CONFIRMED", actor_id=confirmer.id)

        # The confirmer's own entitlement exists only now, and vests at once.
        reward.create_contribution(
            db,
            issue=issue,
            user_id=confirmer.id,
            role=Role.CONFIRMER,
            stages_already_passed=[Stage.VERIFICATION, Stage.RESOLUTION],
        )
        reward.vest_stage(db, issue, Stage.CONFIRMATION, note="Fix-confirmation stage")

        # Physical confirmation is the only thing that moves trust upward: the
        # fault was real and is now fixed, so everyone who reported it was right.
        for contribution in db.execute(
            select(Contribution).where(
                Contribution.issue_id == issue.id,
                Contribution.role.in_([Role.DISCOVERER, Role.CORROBORATOR]),
            )
        ).scalars():
            reporter = db.get(User, contribution.user_id)
            if reporter is not None:
                trust.reward_correct(reporter)
    else:
        reopen(db, issue, reason="Fix-confirmer reported the fault still present", actor_id=confirmer.id)


def reopen(db: Session, issue: Issue, *, reason: str, actor_id: uuid.UUID | None = None) -> None:
    """The repair did not hold.

    If the issue had already been confirmed, the confirmation slice was paid
    against a signal we now know was wrong, so it is reversed and the
    contributions roll back to awaiting confirmation. The confirmer who was
    contradicted loses trust.
    """
    was_confirmed = issue.confirmed_at is not None

    if was_confirmed:
        reward.reverse_stage(db, issue, Stage.CONFIRMATION, reason=f"Reopened: {reason}")

        prior = db.execute(
            select(Task).where(
                Task.issue_id == issue.id,
                Task.type == TaskType.CONFIRM,
                Task.verdict == Verdict.CONFIRMED,
            ).order_by(Task.completed_at.desc())
        ).scalars().first()
        if prior is not None:
            stale_confirmer = db.get(User, prior.assignee_id)
            if stale_confirmer is not None:
                trust.penalise_incorrect(stale_confirmer)

    issue.reopen_count += 1
    issue.confirmed_at = None
    issue.resolved_at = None
    issue.status = IssueStatus.REOPENED
    log_event(db, issue, "REOPENED", actor_id=actor_id, reason=reason, was_confirmed=was_confirmed)


def rerun_priority(db: Session, issue: Issue) -> float:
    """Recompute the live ranking priority from the issue's current age.

    Only `priority` moves. `base_priority`, `equity_multiplier` and
    `reward_total` are what they were at filing and stay that way, so ageing an
    issue up the queue never changes anybody's entitlement.
    """
    age_hours = max(0.0, (utcnow() - issue.created_at).total_seconds() / 3600.0)
    issue.priority = priority_engine.priority(issue.category, issue.ward, age_hours)
    return issue.priority


def reject(db: Session, issue: Issue, *, reason: str, actor_id: uuid.UUID | None = None) -> None:
    """Confidence too low, or an admin audit found it baseless. The unvested
    remainder is forfeited; the discoverer's trust takes the hit."""
    issue.status = IssueStatus.REJECTED
    log_event(db, issue, "REJECTED", actor_id=actor_id, reason=reason)
    reward.forfeit_unvested(db, issue, reason=f"Issue rejected: {reason}")

    discoverer = db.get(User, issue.discoverer_id)
    if discoverer is not None:
        trust.penalise_incorrect(discoverer)


def close(db: Session, issue: Issue, *, actor_id: uuid.UUID, reason: str) -> None:
    issue.status = IssueStatus.CLOSED
    log_event(db, issue, "CLOSED", actor_id=actor_id, reason=reason)


def open_verification_task(
    db: Session, issue: Issue, *, exclude: set | None = None, reroute_count: int = 0
) -> Task | None:
    """Borderline confidence: route a verify task to a nearby eligible verifier.

    Verification is the gate on the municipal queue, so this requires
    VERIFIER_ELIGIBLE with no fallback (see `routing.select_verifier`). With
    nobody reachable the issue stays REPORTED and unvested -- it is neither
    paid nor rejected, because an absent verifier is not evidence either way.
    """
    picked = routing.select_verifier(db, issue, exclude=exclude)
    if picked is None:
        log_event(db, issue, "VERIFIER_UNAVAILABLE", reroute_count=reroute_count)
        return None

    task = Task(
        type=TaskType.VERIFY,
        issue_id=issue.id,
        assignee_id=picked.user.id,
        expires_at=utcnow() + timedelta(hours=settings.verification_window_hours),
        reroute_count=reroute_count,
    )
    db.add(task)
    db.flush()
    log_event(
        db,
        issue,
        "VERIFY_TASK_OPENED",
        assignee=str(picked.user.id),
        distance_m=round(picked.distance_m) if picked.distance_m is not None else None,
        open_tasks=picked.open_tasks,
        reroute_count=reroute_count,
    )
    return task


def reroute_verification(db: Session, task: Task) -> Task | None:
    """The assigned verifier let the window lapse. Hand it to the next-best.

    The lapsed assignee is excluded from the retry but NOT penalised: declining
    to walk somewhere is not dishonesty, and docking trust for it would push
    verifiers toward rubber-stamping tasks they cannot actually attend.
    """
    issue = task.issue
    task.status = TaskStatus.EXPIRED

    if task.reroute_count >= settings.max_verify_reroutes:
        log_event(
            db,
            issue,
            "VERIFICATION_ABANDONED",
            reroute_count=task.reroute_count,
            reason="No eligible verifier attended inside the window",
        )
        return None

    previous = set(
        db.execute(
            select(Task.assignee_id).where(
                Task.issue_id == issue.id, Task.type == TaskType.VERIFY
            )
        ).scalars()
    )
    return open_verification_task(
        db, issue, exclude=previous, reroute_count=task.reroute_count + 1
    )


def complete_verification(
    db: Session, issue: Issue, task: Task, *, verifier: User, verdict: Verdict, photo_key: str | None
) -> None:
    task.status = TaskStatus.COMPLETED
    task.verdict = verdict
    task.photo_path = photo_key
    task.completed_at = utcnow()

    if verdict == Verdict.CONFIRMED:
        reward.create_contribution(db, issue=issue, user_id=verifier.id, role=Role.VERIFIER)
        mark_verified(db, issue, actor_id=verifier.id, reason="Verified on site by trusted user")
    else:
        reject(db, issue, reason="Verifier found no fault at the location", actor_id=verifier.id)
