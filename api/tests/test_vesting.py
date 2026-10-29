"""Tests for the reward ledger and vesting state machine.

This is the part the design doc says must be correct and auditable, so these
tests are about value conservation and idempotency rather than HTTP plumbing.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import lifecycle
from app.config import settings
from app.engines import reward
from app.enums import EntryType, IssueStatus, Role, Stage, Verdict, VestingState
from app.models import Contribution, LedgerEntry

D = lambda x: Decimal(str(x))  # noqa: E731


def ledger_sum(db, user_id) -> Decimal:
    return reward.balance(db, user_id)


def entries_for(db, issue_id) -> list[LedgerEntry]:
    return list(
        db.execute(select(LedgerEntry).where(LedgerEntry.issue_id == issue_id)).scalars()
    )


# --- R_I and pool arithmetic ----------------------------------------------


def test_reward_total_is_bounded():
    assert reward.compute_reward_total(1.0, settings.e_max, 1.0) == D(
        settings.r0_base_points * settings.e_max
    )
    assert reward.compute_reward_total(0.0, 1.0, 0.5) == D("0")
    # Nothing can exceed R0 * E_max even with absurd inputs.
    assert reward.compute_reward_total(5.0, 99.0, 9.0) == D(settings.r0_base_points * settings.e_max)


def test_split_pool_is_exact():
    """A pool must never leak or overpay a rounding unit."""
    total = D("100.0001")
    shares = reward.split_pool(total, [D("1"), D("1"), D("1")])
    assert sum(shares) == total

    shares = reward.split_pool(D("10"), [D("0.3"), D("0.7")])
    assert sum(shares) == D("10")

    assert reward.split_pool(D("10"), []) == []
    assert reward.split_pool(D("10"), [D("0"), D("0")]) == [D("0"), D("0")]


def test_role_and_vesting_fractions_sum_to_one():
    assert sum(reward.ROLE_FRACTION.values()) == D("1")
    for role in (Role.DISCOVERER, Role.CORROBORATOR):
        assert sum(reward.STAGE_PLAN[role].values()) == D("1")


# --- the happy path -------------------------------------------------------


def test_full_lifecycle_pays_discoverer_and_confirmer(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4, trust_b=1)
    confirmer = make_user("meera", trust_a=8, trust_b=0)
    admin = make_user("admin", admin=True)

    issue = make_issue(discoverer)
    r_i = D(issue.reward_total)
    assert r_i > 0

    discoverer_full = reward.quantize_points(r_i * reward.ROLE_FRACTION[Role.DISCOVERER])

    # Verification: only the small slice, by design. Stage slices telescope, so
    # an individual stage may differ from its nominal fraction by one rounding
    # unit -- what must be exact is the total, asserted at the end.
    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    after_verification = ledger_sum(db, discoverer.id)
    assert abs(after_verification - discoverer_full * D(settings.g_verification)) <= reward.QUANTUM
    assert after_verification < discoverer_full * D("0.2"), "verification must stay a small slice"

    # Resolution.
    lifecycle.assign(db, issue, assignee_label="Crew 1", actor_id=admin.id)
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    assert task is not None, "resolving must open a fix-confirmation task"
    assert task.assignee_id == confirmer.id, "confirmer must be the independent trusted user"

    after_resolution = ledger_sum(db, discoverer.id)
    assert after_resolution > after_verification
    assert after_resolution < discoverer_full, "the final slice must still be withheld"

    # Confirmation: the final and largest slice.
    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="fix.jpg"
    )
    db.flush()

    discoverer_total = reward.quantize_points(r_i * reward.ROLE_FRACTION[Role.DISCOVERER])
    assert ledger_sum(db, discoverer.id) == discoverer_total
    assert issue.status == IssueStatus.CONFIRMED

    confirmer_total = reward.quantize_points(r_i * reward.ROLE_FRACTION[Role.CONFIRMER])
    assert ledger_sum(db, confirmer.id) == confirmer_total

    contribution = db.execute(
        select(Contribution).where(
            Contribution.issue_id == issue.id, Contribution.role == Role.DISCOVERER
        )
    ).scalar_one()
    assert contribution.vesting_state == VestingState.PAID


def test_vesting_states_advance_in_order(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4)
    make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    def state() -> VestingState:
        db.expire_all()
        return db.execute(
            select(Contribution.vesting_state).where(
                Contribution.issue_id == issue.id, Contribution.role == Role.DISCOVERER
            )
        ).scalar_one()

    assert state() == VestingState.PENDING_VERIFICATION
    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    assert state() == VestingState.PENDING_RESOLUTION
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    assert state() == VestingState.PENDING_CONFIRMATION
    lifecycle.confirm_fix(
        db, issue, task, confirmer=db.get(type(discoverer), task.assignee_id),
        verdict=Verdict.CONFIRMED, photo_key="x.jpg",
    )
    assert state() == VestingState.PAID


# --- idempotency ----------------------------------------------------------


def test_replaying_a_stage_does_not_double_pay(db, make_user, make_issue):
    """The whole defence against a retried webhook or a double-clicked button."""
    discoverer = make_user("asha", trust_a=4)
    issue = make_issue(discoverer)

    first = reward.vest_stage(db, issue, Stage.VERIFICATION)
    assert len(first) == 1
    after_first = ledger_sum(db, discoverer.id)

    for _ in range(5):
        assert reward.vest_stage(db, issue, Stage.VERIFICATION) == []

    assert ledger_sum(db, discoverer.id) == after_first


def test_mark_verified_is_idempotent(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="first")
    balance = ledger_sum(db, discoverer.id)
    lifecycle.mark_verified(db, issue, actor_id=None, reason="again")
    assert ledger_sum(db, discoverer.id) == balance


def test_redemption_nonce_prevents_double_spend(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=9, trust_b=0)
    confirmer = make_user("meera", trust_a=9, trust_b=0)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="f.jpg"
    )
    db.flush()

    before = ledger_sum(db, discoverer.id)
    cost = D("10.0000")

    first = reward.record_redemption(
        db, user_id=discoverer.id, sku="BADGE-CIVIC", name="Badge", cost=cost, nonce="abc123"
    )
    assert first is not None
    repeat = reward.record_redemption(
        db, user_id=discoverer.id, sku="BADGE-CIVIC", name="Badge", cost=cost, nonce="abc123"
    )
    assert repeat is None, "same nonce must not charge twice"
    assert ledger_sum(db, discoverer.id) == before - cost


# --- the anti-fabrication property ---------------------------------------


def test_fabricated_report_can_never_collect_more_than_gv_slice(db, make_user, make_issue):
    """The core claim of the whole system.

    A fabricated fault cannot be repaired, so it can never reach the resolution
    or confirmation stages. Whatever else happens, the fabricator's take is
    capped at g_verification * f_discoverer * R_I.
    """
    faker = make_user("faker", trust_a=20, trust_b=0)  # even a high-trust account
    issue = make_issue(faker)
    r_i = D(issue.reward_total)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="slipped past confidence")

    full = reward.quantize_points(r_i * reward.ROLE_FRACTION[Role.DISCOVERER])
    cap = reward.quantize_points(full * D(settings.g_verification))
    assert ledger_sum(db, faker.id) == cap

    # The fault is not real, so nobody can confirm it; eventually it is rejected.
    lifecycle.reject(db, issue, reason="no fault at location")
    db.flush()

    assert ledger_sum(db, faker.id) == cap, "rejection must not pay anything further"
    assert cap <= D(settings.r0_base_points) * D(settings.e_max) * D("0.05")


def test_rejection_forfeits_the_unvested_remainder(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4)
    issue = make_issue(discoverer)
    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")

    lifecycle.reject(db, issue, reason="audit")
    db.flush()

    contribution = db.execute(
        select(Contribution).where(Contribution.issue_id == issue.id)
    ).scalar_one()
    assert contribution.vesting_state == VestingState.FORFEITED

    forfeits = [e for e in entries_for(db, issue.id) if e.entry_type == EntryType.FORFEIT]
    assert len(forfeits) == 1
    assert forfeits[0].amount == D("0"), "forfeit is a marker, not a clawback"
    assert "forfeited" in (forfeits[0].note or "")

    # Forfeiture is terminal: a later stage cannot pay a forfeited contribution.
    assert reward.vest_stage(db, issue, Stage.RESOLUTION) == []


def test_forfeiture_leaves_already_vested_entries_alone(db, make_user, make_issue):
    """The ledger is append-only: a correct report that reached verification
    keeps that slice even if the issue is later abandoned."""
    discoverer = make_user("asha", trust_a=4)
    issue = make_issue(discoverer)
    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    vested = ledger_sum(db, discoverer.id)

    lifecycle.reject(db, issue, reason="never resolved")
    db.flush()
    assert ledger_sum(db, discoverer.id) == vested


# --- reopen handling ------------------------------------------------------


def test_reopen_after_confirmation_reverses_the_confirmation_slice(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4)
    confirmer = make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    before_confirmation = ledger_sum(db, discoverer.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="f.jpg"
    )
    db.flush()

    paid_in_full = ledger_sum(db, discoverer.id)
    confirmer_paid = ledger_sum(db, confirmer.id)
    trust_b_before = confirmer.trust_b

    lifecycle.reopen(db, issue, reason="pothole is back", actor_id=admin.id)
    db.flush()

    # The reversal must restore the balance to exactly its pre-confirmation
    # value -- no residue left behind by rounding in either direction.
    assert ledger_sum(db, discoverer.id) == before_confirmation
    assert ledger_sum(db, discoverer.id) < paid_in_full
    assert ledger_sum(db, confirmer.id) == D("0"), "the confirmer fee is clawed back in full"
    assert confirmer_paid > 0, "the confirmer had in fact been paid"
    assert confirmer.trust_b > trust_b_before, "a contradicted confirmer loses trust"

    reversals = [e for e in entries_for(db, issue.id) if e.entry_type == EntryType.REVERSAL]
    assert reversals and all(e.amount < 0 for e in reversals)

    contribution = db.execute(
        select(Contribution).where(
            Contribution.issue_id == issue.id, Contribution.role == Role.DISCOVERER
        )
    ).scalar_one()
    assert contribution.vesting_state == VestingState.PENDING_CONFIRMATION


def test_reopen_then_genuine_reconfirm_pays_exactly_once(db, make_user, make_issue):
    """A reopen/reconfirm cycle must leave the discoverer with exactly their
    full entitlement -- no double pay, no missing slice."""
    discoverer = make_user("asha", trust_a=4)
    confirmer = make_user("meera", trust_a=8)
    other = make_user("vikram", trust_a=9)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=db.get(type(discoverer), task.assignee_id),
        verdict=Verdict.CONFIRMED, photo_key="f.jpg",
    )
    db.flush()

    lifecycle.reopen(db, issue, reason="came back", actor_id=admin.id)
    db.flush()

    # Fixed properly the second time.
    task2 = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    assert task2 is not None
    lifecycle.confirm_fix(
        db, issue, task2, confirmer=db.get(type(discoverer), task2.assignee_id),
        verdict=Verdict.CONFIRMED, photo_key="f2.jpg",
    )
    db.flush()

    expected = reward.quantize_points(D(issue.reward_total) * reward.ROLE_FRACTION[Role.DISCOVERER])
    assert ledger_sum(db, discoverer.id) == expected


def test_failed_confirmation_reopens_without_paying(db, make_user, make_issue):
    discoverer = make_user("asha", trust_a=4)
    confirmer = make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    before = ledger_sum(db, discoverer.id)

    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.REJECTED, photo_key="still.jpg"
    )
    db.flush()

    assert issue.status == IssueStatus.REOPENED
    assert ledger_sum(db, discoverer.id) == before, "a failed confirmation pays nothing"
    assert ledger_sum(db, confirmer.id) == D("0"), "and the confirmer earns no fee"


# --- corroborators --------------------------------------------------------


def test_corroborator_pool_splits_pro_rata_and_conserves_value(db, make_user, make_issue):
    from app.engines import reward as rw
    from app.models import Report, utcnow

    discoverer = make_user("asha", trust_a=4)
    high = make_user("high", trust_a=9, trust_b=0)
    low = make_user("low", trust_a=0, trust_b=0)
    confirmer = make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)

    issue = make_issue(discoverer)

    for user, rho in ((high, 1.0), (low, 1.0)):
        db.add(
            Report(
                issue_id=issue.id,
                reporter_id=user.id,
                role=Role.CORROBORATOR,
                lat=issue.lat,
                lng=issue.lng,
                submitted_at=utcnow(),
                s_img=0.5,
                s_geo=0.9,
                independence_weight=rho,
            )
        )
        db.flush()
        rw.create_contribution(db, issue=issue, user_id=user.id, role=Role.CORROBORATOR)
    db.flush()

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=db.get(type(discoverer), task.assignee_id),
        verdict=Verdict.CONFIRMED, photo_key="f.jpg",
    )
    db.flush()

    pool = rw.quantize_points(D(issue.reward_total) * rw.ROLE_FRACTION[Role.CORROBORATOR])
    paid = ledger_sum(db, high.id) + ledger_sum(db, low.id)
    assert paid == pool, "the corroborator pool is fully distributed, no more and no less"
    assert ledger_sum(db, high.id) > ledger_sum(db, low.id), "trust-weighted"


def test_late_corroborator_starts_at_the_next_unfired_stage(db, make_user, make_issue):
    """Someone who corroborates an already-verified issue cannot collect the
    verification slice retroactively."""
    discoverer = make_user("asha", trust_a=4)
    latecomer = make_user("late", trust_a=5)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")

    contribution = reward.create_contribution(
        db,
        issue=issue,
        user_id=latecomer.id,
        role=Role.CORROBORATOR,
        stages_already_passed=lifecycle.stages_passed(issue),
    )
    db.flush()
    assert contribution.vesting_state == VestingState.PENDING_RESOLUTION
    assert ledger_sum(db, latecomer.id) == D("0")


# --- balance definition ---------------------------------------------------


def test_balance_is_the_signed_sum_of_entries(db, make_user, make_issue):
    """There is no stored balance anywhere -- this is the only definition."""
    discoverer = make_user("asha", trust_a=4)
    confirmer = make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="f.jpg"
    )
    db.flush()

    rows = db.execute(
        select(LedgerEntry.amount).where(LedgerEntry.user_id == discoverer.id)
    ).scalars().all()
    assert reward.balance(db, discoverer.id) == sum(rows, D("0"))


def test_no_ledger_entry_is_ever_mutated(db, make_user, make_issue):
    """Append-only in practice: every transition adds rows, none updates them."""
    discoverer = make_user("asha", trust_a=4)
    confirmer = make_user("meera", trust_a=8)
    admin = make_user("admin", admin=True)
    issue = make_issue(discoverer)

    lifecycle.mark_verified(db, issue, actor_id=None, reason="test")
    snapshot = {e.id: e.amount for e in entries_for(db, issue.id)}

    task = lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    lifecycle.confirm_fix(
        db, issue, task, confirmer=confirmer, verdict=Verdict.CONFIRMED, photo_key="f.jpg"
    )
    lifecycle.reopen(db, issue, reason="back again", actor_id=admin.id)
    db.flush()
    db.expire_all()

    after = {e.id: e.amount for e in entries_for(db, issue.id)}
    for entry_id, amount in snapshot.items():
        assert after[entry_id] == amount, "an existing ledger entry was modified"
    assert len(after) > len(snapshot), "transitions must append"
