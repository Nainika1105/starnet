"""Collusion detection over the contribution graph (design doc section 8).

The independence rules in `engines.routing` stop one person holding two roles on
the SAME issue. They do nothing about the attack that actually threatens the
mechanism, which is two accounts taking turns across many issues:

    issue 1:  A reports  ->  B confirms the repair
    issue 2:  B reports  ->  A confirms the repair
    issue 3:  A reports  ->  B confirms the repair
                              ...

Every one of those issues passes every per-issue check. A is not B, neither
reported the issue the other settled, and both clear their trust tier -- which
they keep, because confirmation is what raises trust. The pair can mint the
confirmation slice indefinitely, and that slice is 45% of the pool: the single
most valuable thing in the system to be able to release at will.

It is only visible in aggregate, as an edge in the graph of who appears
alongside whom. So this module builds that view and exposes it in two places:

  - `routing` refuses a confirmer whose history is entangled with the people
    who stand to be paid. That is the fix -- it stops the ring forming rather
    than discounting it afterwards.
  - `confidence` uses it as the c and p terms of the independence weight, so
    corroboration from an entangled account counts for less.

Everything here is a ratio against the accounts' own activity rather than an
absolute count, because absolute counts punish the people a city most wants:
two neighbours who genuinely report the same street for years would look
identical to a ring under a raw threshold. What distinguishes a ring is that
the pairing is nearly ALL of what they do.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.enums import Role
from app.models import Contribution, utcnow

# Roles that assert a fault is real, and roles that settle whether it was.
# A ring needs one of each, and needs them to swap over.
CLAIM_ROLES = (Role.DISCOVERER, Role.CORROBORATOR)
SETTLE_ROLES = (Role.VERIFIER, Role.CONFIRMER)


@dataclass(frozen=True)
class PairHistory:
    """What two accounts have done alongside each other."""

    shared_issues: int = 0
    activity_a: int = 0
    activity_b: int = 0
    # Issues where a claimed and b settled, and the reverse.
    a_claimed_b_settled: int = 0
    b_claimed_a_settled: int = 0

    @property
    def affinity(self) -> float:
        """Share of the less active account's work that involves the other.

        Measured against the smaller activity on purpose. A new account whose
        every contribution is alongside one established user is the suspicious
        party, and dividing by the established user's large total would hide
        exactly that.
        """
        floor = min(self.activity_a, self.activity_b)
        if floor <= 0:
            return 0.0
        return min(1.0, self.shared_issues / floor)

    @property
    def reciprocity(self) -> int:
        """How many times the pair has swapped claim and settle roles.

        The minimum of the two directions, so one-directional history -- which
        is what a genuinely prolific confirmer looks like -- scores zero.
        """
        return min(self.a_claimed_b_settled, self.b_claimed_a_settled)

    @property
    def is_reciprocal(self) -> bool:
        return self.reciprocity >= settings.collusion_min_reciprocal_swaps

    def suspicion(self) -> float:
        """[0, 1]. 0 means nothing to say; 1 means these two only work together.

        Below `collusion_min_shared_issues` this is always 0: appearing on two
        issues together is a street, not a conspiracy, and a measure that fires
        on coincidence is worse than none.
        """
        if self.shared_issues < settings.collusion_min_shared_issues:
            return 0.0

        # Not enough history for a ratio to mean anything. This matters most in
        # a small or new deployment, where there is hardly anybody else to have
        # worked with and every pair looks exclusive; without it the first
        # users would be flagged for being early.
        if min(self.activity_a, self.activity_b) < settings.collusion_min_activity:
            return 0.0

        threshold = settings.collusion_affinity_threshold
        if self.affinity <= threshold:
            return 0.0

        # Scale the part of the affinity above the threshold onto [0, 1], so
        # the penalty ramps rather than snapping on at the boundary.
        scaled = (self.affinity - threshold) / max(1e-9, 1.0 - threshold)

        if self.is_reciprocal:
            # Swapping claim and settle roles is the signature of the attack
            # this module exists for, not merely of a close pairing.
            scaled = scaled + (1.0 - scaled) * settings.collusion_reciprocal_weight

        return max(0.0, min(1.0, scaled))


def _window_start():
    return utcnow() - timedelta(days=settings.collusion_window_days)


def _activity(db: Session, user_ids: set) -> dict:
    """Issues each user has contributed to inside the window."""
    if not user_ids:
        return {}
    rows = db.execute(
        select(Contribution.user_id, func.count(func.distinct(Contribution.issue_id)))
        .where(
            Contribution.user_id.in_(user_ids),
            Contribution.created_at >= _window_start(),
        )
        .group_by(Contribution.user_id)
    ).all()
    return {user_id: count for user_id, count in rows}


def pair_history(db: Session, a, b) -> PairHistory:
    """The full picture for one pair. Used by tests and by the admin view."""
    if a == b:
        return PairHistory()

    since = _window_start()
    mine = select(Contribution.issue_id, Contribution.role).where(
        Contribution.user_id == a, Contribution.created_at >= since
    ).subquery()
    theirs = select(Contribution.issue_id, Contribution.role).where(
        Contribution.user_id == b, Contribution.created_at >= since
    ).subquery()

    rows = db.execute(
        select(mine.c.role, theirs.c.role).join(
            theirs, theirs.c.issue_id == mine.c.issue_id
        )
    ).all()

    shared_ids = db.execute(
        select(func.count(func.distinct(mine.c.issue_id))).join(
            theirs, theirs.c.issue_id == mine.c.issue_id
        )
    ).scalar_one()

    a_claimed_b_settled = sum(
        1 for mine_role, their_role in rows
        if mine_role in CLAIM_ROLES and their_role in SETTLE_ROLES
    )
    b_claimed_a_settled = sum(
        1 for mine_role, their_role in rows
        if their_role in CLAIM_ROLES and mine_role in SETTLE_ROLES
    )

    activity = _activity(db, {a, b})
    return PairHistory(
        shared_issues=shared_ids,
        activity_a=activity.get(a, 0),
        activity_b=activity.get(b, 0),
        a_claimed_b_settled=a_claimed_b_settled,
        b_claimed_a_settled=b_claimed_a_settled,
    )


def beneficiaries(db: Session, issue_id) -> set:
    """Whose money the confirmation stage releases.

    The claim side only -- the discoverer and corroborators. A verifier's fee
    vested back at the verification stage and does not depend on the repair
    being confirmed, so a confirmer who happens to know the verifier unlocks
    nothing for them. Including verifiers here was starving routing in small
    deployments: every eligible candidate had worked alongside the handful of
    active verifiers, so all of them were vetoed and nobody could confirm
    anything.

    The eligibility exclusions in `routing.excluded_ids` are unaffected and
    still cover every role. This is narrower on purpose: it answers "who would
    this person be paying?", not "who has touched this issue?".
    """
    return set(
        db.execute(
            select(Contribution.user_id).where(
                Contribution.issue_id == issue_id,
                Contribution.role.in_(CLAIM_ROLES),
            )
        ).scalars()
    )


def suspicion_against(db: Session, candidate, counterparties: set) -> float:
    """How entangled `candidate` is with any of `counterparties`.

    The maximum rather than the mean: being clean with nine people does not
    make an exclusive partnership with the tenth acceptable, and it is the
    tenth who would be paid.
    """
    counterparties = {c for c in counterparties if c and c != candidate}
    if not counterparties:
        return 0.0

    return max(
        (pair_history(db, candidate, other).suspicion() for other in counterparties),
        default=0.0,
    )


def worst_pair(db: Session, candidate, counterparties: set):
    """The counterparty driving the score, for the audit trail."""
    counterparties = {c for c in counterparties if c and c != candidate}
    if not counterparties:
        return None, 0.0

    scored = [
        (other, pair_history(db, candidate, other).suspicion()) for other in counterparties
    ]
    return max(scored, key=lambda pair: pair[1])
