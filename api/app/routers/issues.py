from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.enums import IssueStatus
from app.models import Issue, User
from app.routers.reports import OPEN_STATUSES, _attach_as_corroboration
from app.schemas import CorroborateIn, IssueDetail, IssueSummary, ReportAccepted
from app.serializers import issue_detail, issue_summary
from app.models import utcnow

router = APIRouter(prefix="/issues", tags=["issues"])


@router.get("", response_model=list[IssueSummary])
def nearby_issues(
    near: str | None = Query(None, description="lat,lng"),
    radius_m: float = Query(2000, ge=10, le=50_000),
    status_filter: IssueStatus | None = Query(None, alias="status"),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
) -> list[IssueSummary]:
    stmt = select(Issue)

    if status_filter is not None:
        stmt = stmt.where(Issue.status == status_filter)
    else:
        stmt = stmt.where(Issue.status.in_(OPEN_STATUSES))

    if near:
        try:
            lat_s, lng_s = near.split(",")
            lat, lng = float(lat_s), float(lng_s)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "near must be 'lat,lng'")
        point = func.ST_GeogFromText(f"POINT({lng} {lat})")
        stmt = stmt.where(func.ST_DWithin(Issue.location, point, radius_m)).order_by(
            func.ST_Distance(Issue.location, point)
        )
    else:
        stmt = stmt.order_by(Issue.created_at.desc())

    issues = db.execute(stmt.limit(limit)).unique().scalars().all()
    return [issue_summary(i) for i in issues]


@router.get("/{issue_id}", response_model=IssueDetail)
def issue_detail_route(
    issue_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    return issue_detail(db, issue, viewer_id=user.id)


@router.post("/{issue_id}/corroborate", response_model=ReportAccepted)
def corroborate(
    issue_id: uuid.UUID,
    payload: CorroborateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReportAccepted:
    """'I see this too.' Raises the issue's corroboration score and buys the
    user a share of the corroborator pool -- which, like every other share,
    only pays out once the repair is confirmed."""
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    if issue.status not in OPEN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Issue is {issue.status}, not open")
    if issue.discoverer_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You discovered this issue")

    from app.engines import confidence as conf

    s_img = conf.score_image(issue.category, None)
    s_geo = conf.score_geo(lat=payload.lat, lng=payload.lng, exif_time=None, submitted_at=utcnow())

    return _attach_as_corroboration(
        db,
        issue=issue,
        user=user,
        lat=payload.lat,
        lng=payload.lng,
        description=payload.description,
        photo_key=None,
        exif_time=None,
        s_img=s_img,
        s_geo=s_geo,
        submitted_at=utcnow(),
    )
