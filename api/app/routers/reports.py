"""Report intake: the entry point of the whole mechanism."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import lifecycle, storage
from app.auth import current_user
from app.config import settings
from app.db import get_db
from app.engines import confidence as conf
from app.engines import priority as priority_engine
from app.engines import reward, trust
from app.engines.geo import in_service_area, ward_for, wkt_point
from app.enums import Category, IssueStatus, Role
from app.models import Issue, Report, User, utcnow
from app.schemas import ReportAccepted
from app.serializers import issue_summary

router = APIRouter(prefix="/reports", tags=["reports"])

OPEN_STATUSES = [
    IssueStatus.REPORTED,
    IssueStatus.VERIFIED,
    IssueStatus.ASSIGNED,
    IssueStatus.RESOLVED,
    IssueStatus.REOPENED,
]

# A new report can cluster onto any of these. CONFIRMED is included so that a
# fault reported again after the city signed off its repair lands on the same
# issue and reopens it, rather than starting a fresh one that loses the history.
CLUSTERABLE_STATUSES = OPEN_STATUSES + [IssueStatus.CONFIRMED]


def enforce_rate_caps(db: Session, user: User) -> None:
    """Per-user rate caps (section 8). Cheap, rule-based, and the first thing
    a scripted report flood runs into."""
    now = utcnow()
    hourly = db.execute(
        select(func.count(Report.id)).where(
            Report.reporter_id == user.id, Report.submitted_at >= now - timedelta(hours=1)
        )
    ).scalar_one()
    if hourly >= settings.max_reports_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate cap: {settings.max_reports_per_hour} reports per hour",
        )

    daily = db.execute(
        select(func.count(Report.id)).where(
            Report.reporter_id == user.id, Report.submitted_at >= now - timedelta(days=1)
        )
    ).scalar_one()
    if daily >= settings.max_reports_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate cap: {settings.max_reports_per_day} reports per day",
        )


def last_report_position(db: Session, user: User) -> tuple[float | None, float | None, object | None]:
    """Where and when this user last filed, for the travel-speed check."""
    previous = db.execute(
        select(Report)
        .where(Report.reporter_id == user.id)
        .order_by(Report.submitted_at.desc())
        .limit(1)
    ).scalars().first()
    if previous is None:
        return None, None, None
    return previous.lat, previous.lng, previous.submitted_at


def find_cluster_match(db: Session, *, category: Category, lat: float, lng: float) -> Issue | None:
    """Dedup -> corroboration (section 6).

    Same category, within cluster_distance_m, inside the time window -- a
    spatial-temporal rule evaluated by PostGIS on the geography type. A
    duplicate is never discarded: it attaches to the issue it matched and
    raises its corroboration score, which is both fairer to the second reporter
    and better evidence than two half-believed issues.

    The clean seam for Phase 3 is the predicate itself: HDBSCAN over a combined
    distance that adds CLIP embedding similarity to the metres, so two photos of
    the same pothole from opposite kerbs cluster and two different faults at one
    junction do not.

    RESOLVED and CONFIRMED issues are inside the window on purpose -- a fresh
    report of a fault the city has just signed off is the strongest evidence
    there is that the repair did not hold.
    """
    if not settings.clustering_enabled:
        return None

    since = utcnow() - timedelta(hours=settings.cluster_window_hours)
    point = func.ST_GeogFromText(f"POINT({lng} {lat})")
    return db.execute(
        select(Issue)
        .where(
            Issue.category == category,
            Issue.status.in_(CLUSTERABLE_STATUSES),
            Issue.created_at >= since,
            func.ST_DWithin(Issue.location, point, settings.cluster_distance_m),
        )
        .order_by(func.ST_Distance(Issue.location, point))
        .limit(1)
    ).scalar_one_or_none()


@router.post("", response_model=ReportAccepted, status_code=status.HTTP_201_CREATED)
async def create_report(
    category: Category = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    description: str | None = Form(None),
    photo: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReportAccepted:
    enforce_rate_caps(db, user)

    if not in_service_area(lat, lng):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Location is outside the serviced area")

    photo_key = None
    meta = storage.PhotoMeta()
    if photo is not None:
        raw = await photo.read()
        # Read what the geo check needs, then store the image stripped of the rest.
        meta = storage.read_photo_meta(raw)
        photo_key = storage.save(raw)

    submitted_at = utcnow()
    previous_lat, previous_lng, previous_at = last_report_position(db, user)

    s_img = conf.score_image(category, photo_key)
    s_geo = conf.score_geo(
        lat=lat,
        lng=lng,
        exif_time=meta.captured_at,
        submitted_at=submitted_at,
        photo_lat=meta.lat,
        photo_lng=meta.lng,
        previous_lat=previous_lat,
        previous_lng=previous_lng,
        previous_at=previous_at,
    )
    tau = trust.trust_of(user)

    existing = find_cluster_match(db, category=category, lat=lat, lng=lng)
    if existing is not None:
        return _attach_as_corroboration(
            db,
            issue=existing,
            user=user,
            lat=lat,
            lng=lng,
            description=description,
            photo_key=photo_key,
            meta=meta,
            s_img=s_img,
            s_geo=s_geo,
            submitted_at=submitted_at,
        )

    # --- new issue --------------------------------------------------------
    ward = ward_for(lat, lng)
    confidence_value = conf.confidence(s_img=s_img, s_geo=s_geo, s_cor=0.0, tau=tau)

    observed = lifecycle.observed_report_rate(db, ward)
    equity = priority_engine.equity_multiplier(ward, observed)
    priority_value = priority_engine.priority(category, ward)

    issue = Issue(
        category=category,
        status=IssueStatus.REPORTED,
        location=wkt_point(lat, lng),
        lat=lat,
        lng=lng,
        ward=ward,
        confidence=confidence_value,
        priority=priority_value,
        base_priority=priority_value,
        equity_multiplier=equity,
        reward_total=reward.compute_reward_total(priority_value, equity, tau),
        discoverer_id=user.id,
    )
    db.add(issue)
    db.flush()

    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=user.id,
            role=Role.DISCOVERER,
            photo_path=photo_key,
            lat=lat,
            lng=lng,
            description=description,
            exif_time=meta.captured_at,
            photo_lat=meta.lat,
            photo_lng=meta.lng,
            submitted_at=submitted_at,
            s_img=s_img,
            s_geo=s_geo,
            independence_weight=1.0,
        )
    )
    lifecycle.log_event(
        db,
        issue,
        "REPORTED",
        actor_id=user.id,
        confidence=round(confidence_value, 4),
        s_img=round(s_img, 4),
        s_geo=round(s_geo, 4),
        tau=round(tau, 4),
    )

    reward.create_contribution(db, issue=issue, user_id=user.id, role=Role.DISCOVERER)

    decision = conf.decide(confidence_value)
    if decision == conf.Decision.ACCEPT:
        lifecycle.mark_verified(
            db, issue, actor_id=None, reason=f"Auto-accepted: confidence {confidence_value:.2f} >= theta_hi"
        )
        message = "Accepted into the municipal queue. A small verification credit has vested."
    elif decision == conf.Decision.REJECT:
        lifecycle.reject(db, issue, reason=f"Confidence {confidence_value:.2f} < theta_lo")
        message = "Rejected: the report did not clear the confidence floor."
    else:
        lifecycle.open_verification_task(db, issue)
        message = "Sent to a nearby trusted user for on-site verification."

    db.commit()
    db.refresh(issue)
    return ReportAccepted(
        issue=issue_summary(issue),
        decision=decision,
        joined_existing_issue=False,
        your_role=Role.DISCOVERER,
        message=message,
    )


def _attach_as_corroboration(
    db: Session,
    *,
    issue: Issue,
    user: User,
    lat: float,
    lng: float,
    description: str | None,
    photo_key: str | None,
    meta: storage.PhotoMeta,
    s_img: float,
    s_geo: float,
    submitted_at,
) -> ReportAccepted:
    """A duplicate is evidence, never waste: it attaches to the existing issue
    and raises its corroboration score."""
    already = db.execute(
        select(Report).where(Report.issue_id == issue.id, Report.reporter_id == user.id)
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already reported this issue")

    rho = conf.independence_weight(
        shared_device=_shares_device(db, issue, user),
        temporal_burst=_is_burst(db, issue),
    )
    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=user.id,
            role=Role.CORROBORATOR,
            photo_path=photo_key,
            lat=lat,
            lng=lng,
            description=description,
            exif_time=meta.captured_at,
            photo_lat=meta.lat,
            photo_lng=meta.lng,
            submitted_at=submitted_at,
            s_img=s_img,
            s_geo=s_geo,
            independence_weight=rho,
        )
    )
    db.flush()

    lifecycle.recompute_confidence(db, issue)
    reward.create_contribution(
        db,
        issue=issue,
        user_id=user.id,
        role=Role.CORROBORATOR,
        stages_already_passed=lifecycle.stages_passed(issue),
    )
    lifecycle.log_event(
        db, issue, "CORROBORATED", actor_id=user.id, rho=round(rho, 4),
        confidence=round(issue.confidence, 4),
    )

    message = "This matched an existing issue nearby, so it was recorded as corroboration."

    if issue.status in (IssueStatus.RESOLVED, IssueStatus.CONFIRMED):
        # The fault is still there after the city called it fixed. This is the
        # citizen-side counterpart of a failed fix-confirmation: the issue
        # reopens, and if the confirmation slice was already paid, `reopen`
        # reverses it.
        lifecycle.reopen(
            db,
            issue,
            reason="Reported again after the repair was signed off",
            actor_id=user.id,
        )
        message = (
            "This fault was marked repaired. Your report has reopened it and the "
            "repair credit has been withdrawn."
        )
    elif issue.status == IssueStatus.REPORTED and conf.decide(issue.confidence) == conf.Decision.ACCEPT:
        lifecycle.mark_verified(
            db, issue, actor_id=None, reason="Corroboration pushed confidence over theta_hi"
        )
        message = (
            "This matched an existing issue nearby. Your corroboration pushed it "
            "over the confidence threshold and into the municipal queue."
        )

    db.commit()
    db.refresh(issue)
    return ReportAccepted(
        issue=issue_summary(issue),
        decision="CORROBORATED",
        joined_existing_issue=True,
        your_role=Role.CORROBORATOR,
        message=message,
    )


def _shares_device(db: Session, issue: Issue, user: User) -> bool:
    if not user.device_fingerprint:
        return False
    for report in issue.reports:
        other = db.get(User, report.reporter_id)
        if other is not None and other.id != user.id and other.device_fingerprint == user.device_fingerprint:
            return True
    return False


def _is_burst(db: Session, issue: Issue, *, seconds: int = 120) -> bool:
    """Co-submission inside a tight window is the signature of a coordinated
    push rather than independent discovery."""
    now = utcnow()
    return any(
        (now - r.submitted_at).total_seconds() < seconds
        for r in issue.reports
        if r.role == Role.CORROBORATOR
    )
