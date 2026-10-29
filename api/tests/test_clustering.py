"""Dedup -> corroboration, driven through the real endpoint.

Two claims are under test. First, a duplicate is never discarded: it attaches
to the issue it matched and raises that issue's confidence, which is both
fairer to the second reporter and better evidence than two half-believed
issues. Second, a fault reported again after the city signed off its repair
reopens the issue and claws back the confirmation slice -- the citizen-side
counterpart of a failed fix-confirmation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import lifecycle
from app.engines import reward
from app.enums import IssueStatus, Role, Stage, Verdict, VestingState
from app.models import Contribution, Issue, LedgerEntry, Task

JUNCTION = (12.9719, 77.5937)


def _file(client, headers, photo, *, lat=JUNCTION[0], lng=JUNCTION[1], category="POTHOLE"):
    """Post a report the way the mobile app does: multipart, live photo."""
    return client.post(
        "/reports",
        headers=headers,
        data={"category": category, "lat": str(lat), "lng": str(lng), "description": "x"},
        files={"photo": ("live.jpg", photo, "image/jpeg")},
    )


@pytest.fixture
def live_photo(photo_bytes):
    """A photo captured here, now -- the strongest geo evidence available."""
    return lambda: photo_bytes(
        captured_at=datetime.now(timezone.utc), lat=JUNCTION[0], lng=JUNCTION[1]
    )


def test_second_report_nearby_becomes_a_corroboration(db, client, register, live_photo):
    _, first = register("finder")
    _, second = register("neighbour")

    created = _file(client, first, live_photo()).json()
    assert created["joined_existing_issue"] is False

    # ~15 m down the kerb: the same pothole, not a new one.
    joined = _file(client, second, live_photo(), lat=JUNCTION[0] + 0.00013).json()

    assert joined["joined_existing_issue"] is True
    assert joined["your_role"] == Role.CORROBORATOR
    assert joined["issue"]["id"] == created["issue"]["id"]
    assert joined["issue"]["corroboration_count"] == 1
    assert db.execute(select(Issue)).unique().scalars().all().__len__() == 1


def test_corroboration_raises_confidence_and_can_admit_the_issue(db, client, register, live_photo):
    """Under fusion a lone report waits for a verifier; corroboration is what
    carries it over theta_hi without one."""
    _, first = register("finder")
    _, second = register("neighbour")
    _, third = register("passerby")

    created = _file(client, first, live_photo()).json()
    assert created["decision"] == "VERIFY"
    confidence_alone = created["issue"]["confidence"]

    _file(client, second, live_photo(), lat=JUNCTION[0] + 0.0001)
    joined = _file(client, third, live_photo(), lng=JUNCTION[1] + 0.0001).json()

    assert joined["issue"]["confidence"] > confidence_alone


def test_a_fault_far_away_is_its_own_issue(db, client, register, live_photo):
    _, first = register("finder")
    _, second = register("other")

    _file(client, first, live_photo())
    # ~1.1 km north: a different pothole.
    other = _file(client, second, live_photo(), lat=JUNCTION[0] + 0.01).json()

    assert other["joined_existing_issue"] is False
    assert len(db.execute(select(Issue)).unique().scalars().all()) == 2


def test_a_different_category_at_the_same_spot_is_its_own_issue(db, client, register, live_photo):
    _, first = register("finder")
    _, second = register("other")

    _file(client, first, live_photo())
    other = _file(client, second, live_photo(), category="WATER_LEAK").json()

    assert other["joined_existing_issue"] is False


def test_you_cannot_corroborate_your_own_report(client, register, live_photo):
    _, headers = register("finder")
    _file(client, headers, live_photo())

    again = _file(client, headers, live_photo(), lat=JUNCTION[0] + 0.0001)
    assert again.status_code == 409


def test_a_report_outside_the_window_does_not_cluster(db, client, register, live_photo):
    from app.config import settings

    _, first = register("finder")
    _, second = register("neighbour")

    created = _file(client, first, live_photo()).json()
    issue = db.get(Issue, created["issue"]["id"])
    issue.created_at = datetime.now(timezone.utc) - timedelta(
        hours=settings.cluster_window_hours + 1
    )
    db.flush()

    later = _file(client, second, live_photo(), lat=JUNCTION[0] + 0.0001).json()
    assert later["joined_existing_issue"] is False


def test_reporting_a_signed_off_fault_again_reopens_it_and_reverses_the_payout(
    db, client, register, live_photo, make_user
):
    """The strongest evidence a repair failed is the fault being reported again."""
    reporter, first = register("finder")
    _, second = register("neighbour")
    verifier = make_user("verifier", trust_a=9, trust_b=0)
    confirmer = make_user("confirmer", trust_a=9, trust_b=0)
    admin = make_user("city", trust_a=0, trust_b=0, admin=True)

    created = _file(client, first, live_photo()).json()
    issue = db.get(Issue, created["issue"]["id"])

    # Walk it all the way through: verified, resolved, confirmed, paid.
    verify_task = db.execute(
        select(Task).where(Task.issue_id == issue.id)
    ).unique().scalars().first()
    lifecycle.complete_verification(
        db, issue, verify_task, verifier=verifier, verdict=Verdict.CONFIRMED, photo_key="v.jpg"
    )
    confirm_task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, confirm_task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="c.jpg"
    )
    db.flush()

    assert issue.status == IssueStatus.CONFIRMED
    paid_in_full = reward.balance(db, reporter.id)
    confirmation_slice = _stage_total(db, reporter.id, Stage.CONFIRMATION)
    assert confirmation_slice > 0

    # Someone walks past and finds the pothole still there.
    response = _file(client, second, live_photo(), lat=JUNCTION[0] + 0.0001)
    assert response.status_code == 201

    db.refresh(issue)
    assert issue.status == IssueStatus.REOPENED
    assert issue.reopen_count == 1

    # The confirmation slice is clawed back -- by a negative entry, never by
    # editing or deleting the one that paid it.
    assert reward.balance(db, reporter.id) == paid_in_full - confirmation_slice
    assert _stage_total(db, reporter.id, Stage.CONFIRMATION) == 0

    # And the confirmer who signed off a repair that had not held loses trust.
    db.refresh(confirmer)
    assert confirmer.trust_b > 0

    # The reporter's contribution is back to awaiting confirmation, so the
    # slice can be earned once -- and only once -- if the repair is redone.
    contribution = db.execute(
        select(Contribution).where(
            Contribution.issue_id == issue.id,
            Contribution.user_id == reporter.id,
            Contribution.role == Role.DISCOVERER,
        )
    ).scalar_one()
    assert contribution.vesting_state == VestingState.PENDING_CONFIRMATION


def _stage_total(db, user_id, stage) -> object:
    """Net points this user holds from one vesting stage, reversals included."""
    entries = db.execute(
        select(LedgerEntry).where(
            LedgerEntry.user_id == user_id, LedgerEntry.stage == stage
        )
    ).unique().scalars().all()
    return sum((e.amount for e in entries), start=entries[0].amount * 0 if entries else 0)
