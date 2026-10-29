from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import lifecycle, storage
from app.auth import current_user
from app.db import get_db
from app.engines.trust import tier_of
from app.enums import TaskStatus, TaskType, Tier, Verdict
from app.models import Task, User
from app.schemas import TaskCompleteOut, TaskOut
from app.serializers import issue_summary

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/me", response_model=list[TaskOut])
def my_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[TaskOut]:
    if tier_of(user) == Tier.NEW:
        # Tasks are a trust privilege; new accounts cannot verify anything.
        return []

    tasks = db.execute(
        select(Task)
        .where(Task.assignee_id == user.id, Task.status == TaskStatus.OPEN)
        .order_by(Task.created_at.desc())
    ).unique().scalars().all()

    return [
        TaskOut(
            id=t.id,
            type=t.type,
            status=t.status.value,
            issue=issue_summary(t.issue),
            created_at=t.created_at,
            expires_at=t.expires_at,
        )
        for t in tasks
    ]


@router.post("/{task_id}/complete", response_model=TaskCompleteOut)
async def complete_task(
    task_id: uuid.UUID,
    verdict: Verdict = Form(...),
    photo: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TaskCompleteOut:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such task")
    if task.assignee_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This task is not yours")
    if task.status != TaskStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Task is already {task.status}")

    # The protocol requires a timestamped geotagged photo of the repair; a
    # CONFIRMED verdict without one is not evidence.
    photo_key = None
    if photo is not None:
        raw = await photo.read()
        photo_key = storage.save(raw)
    if verdict == Verdict.CONFIRMED and photo_key is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A photo is required to confirm a repair"
        )

    issue = task.issue
    if task.type == TaskType.CONFIRM:
        lifecycle.confirm_fix(
            db, issue, task, confirmer=user, verdict=verdict, photo_key=photo_key
        )
        message = (
            "Repair confirmed. The final vesting stage has been released to everyone involved."
            if verdict == Verdict.CONFIRMED
            else "Reported still broken. The issue has been reopened."
        )
    else:
        lifecycle.complete_verification(
            db, issue, task, verifier=user, verdict=verdict, photo_key=photo_key
        )
        message = (
            "Verified. The issue has entered the municipal queue."
            if verdict == Verdict.CONFIRMED
            else "No fault found. The report was rejected."
        )

    db.commit()
    db.refresh(issue)
    return TaskCompleteOut(
        task_id=task.id, verdict=verdict, issue_status=issue.status, message=message
    )
