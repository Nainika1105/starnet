from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.enums import Category, EntryType, IssueStatus, Role, Stage, TaskType, Tier, Verdict, VestingState


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    handle: str = Field(min_length=3, max_length=64)
    home_ward: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    handle: str
    is_admin: bool
    tier: Tier
    home_ward: str | None
    # Raw trust is deliberately not exposed to citizens; the tier is.
    trust_score: float | None = None


class ReportOut(BaseModel):
    id: uuid.UUID
    reporter_handle: str
    role: Role
    photo_url: str | None
    lat: float
    lng: float
    description: str | None
    submitted_at: datetime
    s_img: float
    s_geo: float
    independence_weight: float
    # How far the photo's own EXIF GPS sat from the claimed location, or None
    # when the photo carried no GPS. This is the geo check made inspectable.
    photo_gps_distance_m: float | None = None


class TimelineEntry(BaseModel):
    event_type: str
    created_at: datetime
    payload: dict


class IssueSummary(BaseModel):
    id: uuid.UUID
    category: Category
    status: IssueStatus
    lat: float
    lng: float
    ward: str
    confidence: float
    # Live ranking priority: the escalation term grows while the issue is open.
    priority: float
    # What priority was at filing. R_I was computed from this and is frozen, so
    # the two together let an auditor recompute the reward from its real inputs.
    base_priority: float
    equity_multiplier: float
    reward_total: Decimal
    corroboration_count: int
    created_at: datetime
    photo_url: str | None = None


class IssueDetail(IssueSummary):
    discoverer_handle: str
    assignee_label: str | None
    reopen_count: int
    resolved_at: datetime | None
    confirmed_at: datetime | None
    reports: list[ReportOut]
    timeline: list[TimelineEntry]
    my_roles: list[Role] = []


class ReportAccepted(BaseModel):
    issue: IssueSummary
    decision: str
    joined_existing_issue: bool
    your_role: Role
    message: str


class CorroborateIn(BaseModel):
    lat: float
    lng: float
    description: str | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    type: TaskType
    status: str
    issue: IssueSummary
    created_at: datetime
    expires_at: datetime | None


class TaskCompleteOut(BaseModel):
    task_id: uuid.UUID
    verdict: Verdict
    issue_status: IssueStatus
    message: str


class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    issue_id: uuid.UUID | None
    role: Role | None
    stage: Stage
    entry_type: EntryType
    amount: Decimal
    note: str | None
    created_at: datetime


class ContributionOut(BaseModel):
    issue_id: uuid.UUID
    category: Category
    issue_status: IssueStatus
    role: Role
    entitlement: Decimal
    vesting_state: VestingState
    vested_so_far: Decimal


class WalletOut(BaseModel):
    balance: Decimal
    pending_by_state: dict[str, Decimal]
    contributions: list[ContributionOut]
    ledger: list[LedgerEntryOut]
    tier: Tier


class CatalogItem(BaseModel):
    sku: str
    name: str
    cost: Decimal
    description: str


class RedeemIn(BaseModel):
    sku: str
    # Client-supplied nonce makes a double-tapped redeem button idempotent.
    nonce: str = Field(min_length=6, max_length=64)


class RedeemOut(BaseModel):
    ok: bool
    balance: Decimal
    message: str


class AssignIn(BaseModel):
    assignee_label: str


class RejectIn(BaseModel):
    reason: str


class WardMetrics(BaseModel):
    ward: str
    open_issues: int
    # Reports waiting on a citizen verifier rather than on the city. A ward
    # where this stays high has thin verifier coverage, which is an operational
    # problem the city can act on and which the equity multiplier cannot fix.
    awaiting_verification: int
    total_issues: int
    median_resolution_hours: float | None
    reopen_rate: float
    expected_report_rate: float
    observed_report_rate: float
    equity_multiplier: float
