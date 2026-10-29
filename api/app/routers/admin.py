from __future__ import annotations

import statistics
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import lifecycle
from app.auth import current_admin
from app.db import get_db
from app.engines import priority as priority_engine
from app.enums import Category, IssueStatus
from app.models import Issue, LedgerEntry, User
from app.routers.reports import OPEN_STATUSES
from app.schemas import AssignIn, IssueDetail, IssueSummary, RejectIn, WardMetrics
from app.serializers import issue_detail, issue_summary

router = APIRouter(prefix="/admin", tags=["admin"])

# Statuses that still need municipal action.
QUEUE_STATUSES = [IssueStatus.VERIFIED, IssueStatus.ASSIGNED, IssueStatus.REOPENED]


@router.get("/queue", response_model=list[IssueSummary])
def queue(
    ward: str | None = Query(None),
    category: Category | None = Query(None),
    status_filter: IssueStatus | None = Query(None, alias="status"),
    limit: int = Query(200, le=1000),
    _: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> list[IssueSummary]:
    """Ranked by priority, then by the equity multiplier, then by age.

    Equity sits above age on purpose: a report from an under-reported ward
    should not lose to a newer one from a loud ward.
    """
    stmt = select(Issue).where(
        Issue.status.in_([status_filter] if status_filter else QUEUE_STATUSES)
    )
    if ward:
        stmt = stmt.where(Issue.ward == ward)
    if category:
        stmt = stmt.where(Issue.category == category)

    stmt = stmt.order_by(
        Issue.priority.desc(), Issue.equity_multiplier.desc(), Issue.created_at.asc()
    ).limit(limit)

    return [issue_summary(i) for i in db.execute(stmt).unique().scalars().all()]


@router.get("/issues/{issue_id}", response_model=IssueDetail)
def admin_issue_detail(
    issue_id: uuid.UUID, admin: User = Depends(current_admin), db: Session = Depends(get_db)
) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    return issue_detail(db, issue, viewer_id=admin.id)


@router.post("/issues/{issue_id}/assign", response_model=IssueDetail)
def assign_issue(
    issue_id: uuid.UUID,
    payload: AssignIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    if issue.status not in (IssueStatus.VERIFIED, IssueStatus.REOPENED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot assign an issue that is {issue.status}")

    lifecycle.assign(db, issue, assignee_label=payload.assignee_label, actor_id=admin.id)
    db.commit()
    db.refresh(issue)
    return issue_detail(db, issue, viewer_id=admin.id)


@router.post("/issues/{issue_id}/resolve", response_model=IssueDetail)
def resolve_issue(
    issue_id: uuid.UUID, admin: User = Depends(current_admin), db: Session = Depends(get_db)
) -> IssueDetail:
    """Marks work done and releases g_resolution -- but NOT the final slice.
    The last 45% waits for an independent user to photograph the repair."""
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    if issue.status not in (IssueStatus.VERIFIED, IssueStatus.ASSIGNED, IssueStatus.REOPENED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot resolve an issue that is {issue.status}")

    lifecycle.mark_resolved(db, issue, actor_id=admin.id)
    db.commit()
    db.refresh(issue)
    return issue_detail(db, issue, viewer_id=admin.id)


@router.post("/issues/{issue_id}/reject", response_model=IssueDetail)
def reject_issue(
    issue_id: uuid.UUID,
    payload: RejectIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    lifecycle.reject(db, issue, reason=payload.reason, actor_id=admin.id)
    db.commit()
    db.refresh(issue)
    return issue_detail(db, issue, viewer_id=admin.id)


@router.post("/issues/{issue_id}/reopen", response_model=IssueDetail)
def reopen_issue(
    issue_id: uuid.UUID,
    payload: RejectIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    lifecycle.reopen(db, issue, reason=payload.reason, actor_id=admin.id)
    db.commit()
    db.refresh(issue)
    return issue_detail(db, issue, viewer_id=admin.id)


@router.post("/issues/{issue_id}/close-offapp", response_model=IssueDetail)
def close_off_app(
    issue_id: uuid.UUID,
    payload: RejectIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> IssueDetail:
    """Work-order reconciliation: the fault was fixed outside the app.

    Note this still does NOT pay the confirmation slice -- nobody has verified
    the repair, so the mechanism withholds it exactly as designed.
    """
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such issue")
    lifecycle.close(db, issue, actor_id=admin.id, reason=f"Closed off-app: {payload.reason}")
    db.commit()
    db.refresh(issue)
    return issue_detail(db, issue, viewer_id=admin.id)


@router.get("/metrics", response_model=list[WardMetrics])
def metrics(_: User = Depends(current_admin), db: Session = Depends(get_db)) -> list[WardMetrics]:
    """Public-facing accountability. The city is scored too: median resolution
    time and reopen rate per ward."""
    out: list[WardMetrics] = []

    for ward in priority_engine.all_wards():
        issues = db.execute(select(Issue).where(Issue.ward == ward)).unique().scalars().all()
        if not issues:
            continue

        durations = [
            (i.resolved_at - i.created_at).total_seconds() / 3600
            for i in issues
            if i.resolved_at is not None
        ]
        reopened = sum(1 for i in issues if i.reopen_count > 0)
        observed = lifecycle.observed_report_rate(db, ward)

        out.append(
            WardMetrics(
                ward=ward,
                open_issues=sum(1 for i in issues if i.status in OPEN_STATUSES),
                awaiting_verification=sum(
                    1 for i in issues if i.status == IssueStatus.REPORTED
                ),
                total_issues=len(issues),
                median_resolution_hours=round(statistics.median(durations), 2) if durations else None,
                reopen_rate=round(reopened / len(issues), 4),
                expected_report_rate=priority_engine.expected_rate(ward),
                observed_report_rate=observed,
                equity_multiplier=round(priority_engine.equity_multiplier(ward, observed), 3),
            )
        )

    return sorted(out, key=lambda m: m.ward)


@router.get("/ledger")
def full_ledger(
    limit: int = Query(500, le=5000),
    _: User = Depends(current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The whole append-only ledger, for audit. Nothing here is ever updated
    or deleted, so this is a complete history of every point ever moved."""
    rows = db.execute(
        select(LedgerEntry, User.handle)
        .join(User, User.id == LedgerEntry.user_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(limit)
    ).all()

    return [
        {
            "id": str(e.id),
            "created_at": e.created_at.isoformat(),
            "user": handle,
            "issue_id": str(e.issue_id) if e.issue_id else None,
            "role": e.role.value if e.role else None,
            "stage": e.stage.value,
            "entry_type": e.entry_type.value,
            "amount": str(e.amount),
            "note": e.note,
            "idempotency_key": e.idempotency_key,
        }
        for e, handle in rows
    ]
