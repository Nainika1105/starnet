from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.geo import haversine_m
from app.enums import Role
from app.models import Contribution, Issue, Report
from app.schemas import IssueDetail, IssueSummary, ReportOut, TimelineEntry
from app.storage import url_for


def corroboration_count(issue: Issue) -> int:
    return sum(1 for r in issue.reports if r.role == Role.CORROBORATOR)


def _cover_photo(issue: Issue) -> str | None:
    for report in sorted(issue.reports, key=lambda r: r.submitted_at):
        if report.photo_path:
            return url_for(report.photo_path)
    return None


def issue_summary(issue: Issue) -> IssueSummary:
    return IssueSummary(
        id=issue.id,
        category=issue.category,
        status=issue.status,
        lat=issue.lat,
        lng=issue.lng,
        ward=issue.ward,
        confidence=issue.confidence,
        priority=issue.priority,
        base_priority=issue.base_priority,
        equity_multiplier=issue.equity_multiplier,
        reward_total=issue.reward_total,
        corroboration_count=corroboration_count(issue),
        created_at=issue.created_at,
        photo_url=_cover_photo(issue),
    )


def _photo_gps_distance(report: Report) -> float | None:
    if report.photo_lat is None or report.photo_lng is None:
        return None
    return round(
        haversine_m(report.lat, report.lng, report.photo_lat, report.photo_lng), 1
    )


def report_out(report: Report) -> ReportOut:
    return ReportOut(
        id=report.id,
        reporter_handle=report.reporter.handle,
        role=report.role,
        photo_url=url_for(report.photo_path),
        lat=report.lat,
        lng=report.lng,
        description=report.description,
        submitted_at=report.submitted_at,
        s_img=report.s_img,
        s_geo=report.s_geo,
        independence_weight=report.independence_weight,
        photo_gps_distance_m=_photo_gps_distance(report),
    )


def issue_detail(db: Session, issue: Issue, *, viewer_id: uuid.UUID | None = None) -> IssueDetail:
    my_roles: list[Role] = []
    if viewer_id is not None:
        my_roles = list(
            db.execute(
                select(Contribution.role).where(
                    Contribution.issue_id == issue.id, Contribution.user_id == viewer_id
                )
            ).scalars()
        )

    return IssueDetail(
        **issue_summary(issue).model_dump(),
        discoverer_handle=issue.discoverer.handle,
        assignee_label=issue.assignee_label,
        reopen_count=issue.reopen_count,
        resolved_at=issue.resolved_at,
        confirmed_at=issue.confirmed_at,
        reports=[report_out(r) for r in sorted(issue.reports, key=lambda r: r.submitted_at)],
        timeline=[
            TimelineEntry(event_type=e.event_type, created_at=e.created_at, payload=e.payload or {})
            for e in sorted(issue.events, key=lambda e: e.created_at)
        ],
        my_roles=my_roles,
    )
