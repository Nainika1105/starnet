"""Who gets asked to verify a report, or to confirm a repair (section 8).

Routing is a trust-and-independence problem before it is a logistics problem.
Three rules are structural, not preferences, and every path through this module
enforces them:

  1. The system picks. A contractor can never nominate the person who signs off
     their own work.
  2. Nobody who reported the issue can verify or confirm it. Marking your own
     report correct is exactly the fabrication the mechanism exists to price
     out.
  3. The candidate must clear the trust tier -- and for a VERIFY task, which is
     what admits work to the city's queue, VERIFIER_ELIGIBLE with no fallback.

Within the candidates those rules leave, routing prefers whoever is most likely
to actually walk past the spot, then spreads load, then prefers trust. Distance
comes from the candidate's own last report, measured in PostGIS against the
issue's location -- not a ward-string match.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.engines import trust
from app.enums import TaskStatus, Tier
from app.models import Contribution, Issue, Report, Task, User

# Distance is bucketed rather than compared raw: 180 m and 240 m away say the
# same thing about whether someone can detour past a pothole, so inside a
# bucket the tie is broken on load and trust instead of on GPS noise.
DISTANCE_BUCKET_M = 500.0

# Sorts after every candidate with a usable known position.
_UNKNOWN_BUCKET = 10_000


@dataclass(frozen=True)
class Candidate:
    user: User
    distance_m: float | None
    open_tasks: int
    tau: float
    last_seen_at: datetime | None

    @property
    def bucket(self) -> int:
        if self.distance_m is None or self.distance_m > settings.routing_max_distance_m:
            return _UNKNOWN_BUCKET
        return int(self.distance_m // DISTANCE_BUCKET_M)

    @property
    def sort_key(self) -> tuple[int, int, float]:
        # Nearer first, then least loaded, then most trusted.
        return (self.bucket, self.open_tasks, -self.tau)


def _last_known_positions(db: Session, issue: Issue):
    """Each user's most recent report, with its distance to this issue.

    One query, computed by PostGIS on the geography type, so the ordering is
    true great-circle distance rather than a bounding-box approximation.
    """
    ranked = select(
        Report.reporter_id.label("uid"),
        Report.lat.label("lat"),
        Report.lng.label("lng"),
        Report.submitted_at.label("submitted_at"),
        func.row_number()
        .over(partition_by=Report.reporter_id, order_by=Report.submitted_at.desc())
        .label("rn"),
    ).subquery()

    latest = select(ranked).where(ranked.c.rn == 1).subquery()

    candidate_point = cast(
        func.ST_SetSRID(func.ST_MakePoint(latest.c.lng, latest.c.lat), 4326),
        Geography(geometry_type="POINT", srid=4326),
    )
    # The issue's own point goes in as a literal rather than as a join back to
    # `issues`: one row against many is a cross join, and spelling it out that
    # way is both clearer and cheaper than filtering a product down to one id.
    issue_point = func.ST_GeogFromText(f"POINT({issue.lng} {issue.lat})")

    rows = db.execute(
        select(
            latest.c.uid,
            func.ST_Distance(candidate_point, issue_point).label("distance_m"),
            latest.c.submitted_at,
        )
    ).all()

    return {row.uid: (float(row.distance_m), row.submitted_at) for row in rows}


def _open_task_counts(db: Session) -> dict:
    rows = db.execute(
        select(Task.assignee_id, func.count(Task.id))
        .where(Task.status == TaskStatus.OPEN)
        .group_by(Task.assignee_id)
    ).all()
    return {uid: count for uid, count in rows}


def excluded_ids(db: Session, issue: Issue) -> set:
    """Everyone with a stake in this issue being called real.

    Reporters are the obvious case. Contributors in any role are excluded too,
    which matters for the verifier: someone who attested on site that the fault
    was real has already staked their reputation on that call, so asking them
    to also sign off the repair lets one person carry an issue from report to
    payout. Different people at each gate is the whole point.
    """
    ids = set(
        db.execute(select(Report.reporter_id).where(Report.issue_id == issue.id)).scalars()
    )
    ids.update(
        db.execute(
            select(Contribution.user_id).where(Contribution.issue_id == issue.id)
        ).scalars()
    )
    ids.add(issue.discoverer_id)
    return ids


def candidates(
    db: Session, issue: Issue, *, tiers: tuple[Tier, ...], exclude: set | None = None
) -> list[Candidate]:
    exclude = set(exclude or set()) | excluded_ids(db, issue)

    positions = _last_known_positions(db, issue)
    loads = _open_task_counts(db)

    out: list[Candidate] = []
    for user in db.execute(
        select(User).where(User.is_admin == False, User.id.notin_(exclude))  # noqa: E712
    ).scalars():
        if trust.tier_of(user) not in tiers:
            continue
        distance, last_seen = positions.get(user.id, (None, None))
        out.append(
            Candidate(
                user=user,
                distance_m=distance,
                open_tasks=loads.get(user.id, 0),
                tau=trust.trust_of(user),
                last_seen_at=last_seen,
            )
        )

    return sorted(out, key=lambda c: c.sort_key)


def select_verifier(db: Session, issue: Issue, *, exclude: set | None = None) -> Candidate | None:
    """VERIFIER_ELIGIBLE only.

    No TRUSTED fallback on purpose: a verification is what puts a fault on the
    municipal queue and releases the first vesting slice, so if no eligible
    verifier is reachable the report waits. An under-verified queue is a
    tractable problem; a queue admitted by whoever happened to be available is
    not.
    """
    pool = candidates(db, issue, tiers=(Tier.VERIFIER_ELIGIBLE,), exclude=exclude)
    return pool[0] if pool else None


def select_confirmer(db: Session, issue: Issue, *, exclude: set | None = None) -> Candidate | None:
    """VERIFIER_ELIGIBLE, falling back to TRUSTED.

    The fallback is allowed here and not for verification because the asymmetry
    runs the other way: a repair that nobody confirms costs the contributors
    their final 45%, so refusing to route a confirmation punishes the honest
    reporter for the city's thin verifier coverage. The independence rules are
    not relaxed -- only the tier.
    """
    pool = candidates(db, issue, tiers=(Tier.VERIFIER_ELIGIBLE,), exclude=exclude)
    if not pool:
        pool = candidates(db, issue, tiers=(Tier.TRUSTED,), exclude=exclude)
    return pool[0] if pool else None
