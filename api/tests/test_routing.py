"""Who gets asked, and what happens when nobody answers.

Routing carries three structural rules -- system-chosen, never a reporter, must
clear the tier -- and each one is a way the mechanism could be gamed if it were
merely a preference. These tests treat them as invariants.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app import lifecycle
from app.config import settings
from app.engines import routing, trust
from app.enums import IssueStatus, Role, TaskStatus, TaskType, Tier, Verdict
from app.models import Report, Task, utcnow

# Trust counts that land a user in each tier. tau = (a+1)/(a+b+2).
ELIGIBLE = dict(trust_a=9, trust_b=0)    # 0.909
TRUSTED = dict(trust_a=2, trust_b=0)     # 0.75 -> also eligible; see below
MID = dict(trust_a=1, trust_b=0)         # 0.667 -> TRUSTED
NEW = dict(trust_a=0, trust_b=0)         # 0.5  -> NEW


def test_tier_boundaries_are_what_the_config_says(make_user):
    assert trust.tier_of(make_user("a", **NEW)) == Tier.NEW
    assert trust.tier_of(make_user("b", **MID)) == Tier.TRUSTED
    assert trust.tier_of(make_user("c", **ELIGIBLE)) == Tier.VERIFIER_ELIGIBLE


def test_verifier_is_never_a_reporter(db, make_user, make_issue):
    discoverer = make_user("discoverer", **ELIGIBLE)
    corroborator = make_user("corroborator", **ELIGIBLE)
    outsider = make_user("outsider", **ELIGIBLE)

    issue = make_issue(discoverer)
    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=corroborator.id,
            role=Role.CORROBORATOR,
            lat=issue.lat,
            lng=issue.lng,
            submitted_at=utcnow(),
        )
    )
    db.flush()

    picked = routing.select_verifier(db, issue)
    assert picked is not None
    assert picked.user.id == outsider.id


def test_verification_requires_the_top_tier_with_no_fallback(db, make_user, make_issue):
    """A verification admits work to the city's queue, so the gate does not bend."""
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    make_user("merely_trusted", **MID)

    assert routing.select_verifier(db, issue) is None
    # The same candidate is acceptable for a confirmation.
    confirmer = routing.select_confirmer(db, issue)
    assert confirmer is not None and trust.tier_of(confirmer.user) == Tier.TRUSTED


def test_the_verifier_cannot_also_confirm_the_repair(db, make_user, make_issue):
    """One person must not be able to carry an issue from report to payout.

    The on-site verifier already staked their reputation on the fault being
    real, so they are excluded from confirming the repair that settles it.
    """
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    verifier = make_user("verifier", **ELIGIBLE)
    _place(db, verifier, lat=issue.lat, lng=issue.lng)

    task = lifecycle.open_verification_task(db, issue)
    assert task.assignee_id == verifier.id
    lifecycle.complete_verification(
        db, issue, task, verifier=verifier, verdict=Verdict.CONFIRMED, photo_key="v.jpg"
    )
    db.flush()

    # The verifier is now the only trusted user in the system, and still is not
    # eligible -- the issue waits rather than being self-certified.
    assert routing.select_confirmer(db, issue) is None

    outsider = make_user("outsider", **ELIGIBLE)
    _place(db, outsider, lat=issue.lat, lng=issue.lng)
    picked = routing.select_confirmer(db, issue)
    assert picked is not None and picked.user.id == outsider.id


def test_new_accounts_are_never_routed_anything(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    make_user("newcomer", **NEW)

    assert routing.select_verifier(db, issue) is None
    assert routing.select_confirmer(db, issue) is None


def test_admins_are_not_candidates(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    make_user("city_admin", admin=True, **ELIGIBLE)
    assert routing.select_confirmer(db, issue) is None


def test_nearer_candidate_wins_over_more_trusted_one(db, make_user, make_issue):
    """Someone who can actually walk past the fault beats someone who cannot."""
    issue = make_issue(make_user("discoverer", **ELIGIBLE), lat=12.9719, lng=77.5937)

    near = make_user("near", trust_a=3, trust_b=0)      # tau 0.80
    far = make_user("far", trust_a=30, trust_b=0)       # tau 0.97, but across town

    _place(db, near, lat=12.9725, lng=77.5940)          # ~70 m
    _place(db, far, lat=13.0500, lng=77.6300)           # ~9 km

    picked = routing.select_verifier(db, issue)
    assert picked.user.id == near.id


def test_load_is_spread_between_equally_near_candidates(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))

    busy = make_user("busy", trust_a=30, trust_b=0)     # most trusted
    idle = make_user("idle", trust_a=3, trust_b=0)
    _place(db, busy, lat=issue.lat, lng=issue.lng)
    _place(db, idle, lat=issue.lat, lng=issue.lng)

    other = make_issue(make_user("other_discoverer", **ELIGIBLE), lat=12.9800, lng=77.5900)
    for _ in range(2):
        db.add(Task(type=TaskType.VERIFY, issue_id=other.id, assignee_id=busy.id))
    db.flush()

    picked = routing.select_verifier(db, issue)
    assert picked.user.id == idle.id


def test_candidate_beyond_the_routing_radius_sorts_with_the_unknowns(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    distant = make_user("distant", **ELIGIBLE)
    _place(db, distant, lat=13.1500, lng=77.7800)  # ~26 km

    candidates = routing.candidates(db, issue, tiers=(Tier.VERIFIER_ELIGIBLE,))
    assert len(candidates) == 1
    assert candidates[0].distance_m > settings.routing_max_distance_m
    assert candidates[0].bucket == routing._UNKNOWN_BUCKET


# --- expiry and re-routing -------------------------------------------------


def test_lapsed_verify_task_moves_to_the_next_candidate(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    first = make_user("first", trust_a=30, trust_b=0)
    second = make_user("second", trust_a=9, trust_b=0)
    _place(db, first, lat=issue.lat, lng=issue.lng)
    _place(db, second, lat=issue.lat, lng=issue.lng)

    task = lifecycle.open_verification_task(db, issue)
    assert task.assignee_id == first.id

    rerouted = lifecycle.reroute_verification(db, task)

    assert task.status == TaskStatus.EXPIRED
    assert rerouted is not None and rerouted.assignee_id == second.id
    assert rerouted.reroute_count == 1
    # Not turning up is not dishonesty: no penalty is recorded against the
    # lapsed verifier. (tau itself drifts continuously with decay, so the
    # evidence count is what to assert on, not the score.)
    assert first.trust_b == 0.0


def test_re_routing_stops_at_the_configured_limit(db, make_user, make_issue):
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    for n in range(settings.max_verify_reroutes + 2):
        user = make_user(f"verifier{n}", **ELIGIBLE)
        _place(db, user, lat=issue.lat, lng=issue.lng)

    task = lifecycle.open_verification_task(db, issue)
    for _ in range(settings.max_verify_reroutes):
        task = lifecycle.reroute_verification(db, task)
        assert task is not None

    assert lifecycle.reroute_verification(db, task) is None
    assert _event_types(db, issue).count("VERIFICATION_ABANDONED") == 1


def test_an_unverified_issue_is_neither_paid_nor_rejected(db, make_user, make_issue):
    """Silence is not evidence. The stake stays unvested and the issue stays open."""
    from app.engines import reward

    discoverer = make_user("discoverer", **ELIGIBLE)
    issue = make_issue(discoverer)
    make_user("verifier", **ELIGIBLE)

    task = lifecycle.open_verification_task(db, issue)
    for _ in range(settings.max_verify_reroutes + 1):
        task = lifecycle.reroute_verification(db, task)
        if task is None:
            break

    assert issue.status == IssueStatus.REPORTED
    assert reward.balance(db, discoverer.id) == 0


def test_verifying_closes_the_task_and_vests_the_first_slice(db, make_user, make_issue):
    from app.engines import reward

    discoverer = make_user("discoverer", **ELIGIBLE)
    issue = make_issue(discoverer)
    verifier = make_user("verifier", **ELIGIBLE)
    _place(db, verifier, lat=issue.lat, lng=issue.lng)

    task = lifecycle.open_verification_task(db, issue)
    lifecycle.complete_verification(
        db, issue, task, verifier=verifier, verdict=Verdict.CONFIRMED, photo_key="x.jpg"
    )

    assert issue.status == IssueStatus.VERIFIED
    assert task.status == TaskStatus.COMPLETED
    assert reward.balance(db, discoverer.id) > 0
    assert reward.balance(db, verifier.id) > 0


def test_a_superseded_verify_task_is_not_left_open(db, make_user, make_issue):
    """Corroboration can carry an issue over theta_hi while a verify task is out."""
    issue = make_issue(make_user("discoverer", **ELIGIBLE))
    verifier = make_user("verifier", **ELIGIBLE)
    _place(db, verifier, lat=issue.lat, lng=issue.lng)

    task = lifecycle.open_verification_task(db, issue)
    lifecycle.mark_verified(db, issue, actor_id=None, reason="Corroborated over theta_hi")

    assert task.status == TaskStatus.SUPERSEDED
    assert _open_tasks(db, verifier) == 0


# --- helpers ---------------------------------------------------------------


def _place(db, user, *, lat: float, lng: float) -> None:
    """Give a user a last-known position by filing them a report elsewhere.

    Routing reads position from the candidate's own most recent report, which
    is the only location signal the system legitimately has.
    """
    from app.enums import Category
    from app.engines.geo import ward_for, wkt_point
    from app.models import Issue

    issue = Issue(
        category=Category.GARBAGE,
        status=IssueStatus.CLOSED,
        location=wkt_point(lat, lng),
        lat=lat,
        lng=lng,
        ward=ward_for(lat, lng),
        reward_total=0,
        discoverer_id=user.id,
    )
    db.add(issue)
    db.flush()
    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=user.id,
            role=Role.DISCOVERER,
            lat=lat,
            lng=lng,
            submitted_at=utcnow() - timedelta(days=1),
        )
    )
    db.flush()


def _open_tasks(db, user) -> int:
    return len(
        db.execute(
            select(Task).where(Task.assignee_id == user.id, Task.status == TaskStatus.OPEN)
        ).unique().scalars().all()
    )


def _event_types(db, issue) -> list[str]:
    """Read the audit trail from the table, not the loaded relationship, which
    can be a snapshot from before the transition under test.

    The flush is needed because the test session runs with autoflush off (so
    that engine code is exercised exactly as written); in a request the commit
    does this.
    """
    from app.models import IssueEvent

    db.flush()
    return list(
        db.execute(
            select(IssueEvent.event_type).where(IssueEvent.issue_id == issue.id)
        ).scalars()
    )
