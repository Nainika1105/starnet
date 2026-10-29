import uuid
from datetime import datetime, timezone
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (
    Category,
    EntryType,
    IssueStatus,
    Role,
    Stage,
    TaskStatus,
    TaskType,
    Tier,
    Verdict,
    VestingState,
)

# Points are exact decimals, never floats: this is a value ledger.
POINTS = Numeric(18, 4)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(py_enum, name):
    return Enum(py_enum, name=name, native_enum=True, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str] = mapped_column(String(64), unique=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Beta reputation counts. a = physically-confirmed-correct contributions,
    # b = confirmed-incorrect. tau = (a + 1) / (a + b + 2).
    trust_a: Mapped[float] = mapped_column(Float, default=0.0)
    trust_b: Mapped[float] = mapped_column(Float, default=0.0)
    trust_decayed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    device_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    home_ward: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    category: Mapped[Category] = mapped_column(_enum(Category, "category"))
    status: Mapped[IssueStatus] = mapped_column(
        _enum(IssueStatus, "issue_status"), default=IssueStatus.REPORTED, index=True
    )

    # Canonical location: the centroid of the reports clustered into this issue.
    location = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    # Denormalised alongside the geography column: PostGIS answers the spatial
    # queries, these answer "what do I put in the JSON" without an ST_ call.
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    ward: Mapped[str] = mapped_column(String(64), index=True)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # Live ranking priority. The escalation component grows while the issue is
    # open, so this moves; `base_priority` is what it was at filing.
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    # Frozen with reward_total, so an audit can recompute R_I from the inputs
    # that were actually used instead of from today's aged-up ranking.
    base_priority: Mapped[float] = mapped_column(Float, default=0.0)
    equity_multiplier: Mapped[float] = mapped_column(Float, default=1.0)

    # R_I, frozen at the moment the issue is created so later trust/priority
    # drift cannot retroactively change what contributors were promised.
    reward_total: Mapped[Decimal] = mapped_column(POINTS, default=Decimal("0"))

    discoverer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    assignee_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)

    discoverer = relationship("User", lazy="joined")
    reports = relationship("Report", back_populates="issue", lazy="selectin")
    events = relationship("IssueEvent", back_populates="issue", lazy="selectin")


class Report(Base):
    """A single submission. Duplicates are never deleted -- they attach here as
    corroborations of an existing issue."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    issue_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("issues.id"), index=True)
    reporter_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))

    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # The only EXIF kept from the photo: capture time and GPS, both needed by
    # the geo-coherence check and both worth keeping for an audit. Everything
    # else is stripped before the image is stored.
    exif_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    photo_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    photo_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    s_img: Mapped[float] = mapped_column(Float, default=0.0)
    s_geo: Mapped[float] = mapped_column(Float, default=0.0)
    independence_weight: Mapped[float] = mapped_column(Float, default=1.0)

    issue = relationship("Issue", back_populates="reports")
    reporter = relationship("User", lazy="joined")

    __table_args__ = (
        # One submission per user per issue: a user cannot farm an issue by
        # corroborating their own report repeatedly.
        UniqueConstraint("issue_id", "reporter_id", name="uq_report_issue_reporter"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    type: Mapped[TaskType] = mapped_column(_enum(TaskType, "task_type"))
    issue_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("issues.id"), index=True)
    assignee_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus, "task_status"), default=TaskStatus.OPEN)

    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verdict: Mapped[Verdict | None] = mapped_column(_enum(Verdict, "verdict"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many times this task has already been handed on because the previous
    # assignee let it expire. Bounded by max_verify_reroutes.
    reroute_count: Mapped[int] = mapped_column(Integer, default=0)

    issue = relationship("Issue", lazy="joined")


class Contribution(Base):
    """One row per (issue, user, role). This row carries the vesting state
    machine; the money itself only ever appears as ledger entries."""

    __tablename__ = "contributions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    issue_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("issues.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "contribution_role"))

    entitlement: Mapped[Decimal] = mapped_column(POINTS, default=Decimal("0"))
    vesting_state: Mapped[VestingState] = mapped_column(
        _enum(VestingState, "vesting_state"), default=VestingState.PENDING_VERIFICATION
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", lazy="joined")
    issue = relationship("Issue", lazy="joined")

    __table_args__ = (
        UniqueConstraint("issue_id", "user_id", "role", name="uq_contribution_issue_user_role"),
    )


class LedgerEntry(Base):
    """Append-only. Never updated, never deleted. A user's balance is the signed
    sum of their entries -- balances are not stored anywhere.

    `idempotency_key` is the double-pay guard: every transition derives a
    deterministic key, so a retried or duplicated lifecycle event collides on
    the unique index instead of paying twice.
    """

    __tablename__ = "reward_ledger"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("issues.id"), nullable=True, index=True
    )
    contribution_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("contributions.id"), nullable=True
    )

    role: Mapped[Role | None] = mapped_column(_enum(Role, "ledger_role"), nullable=True)
    stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"), default=Stage.NONE)
    entry_type: Mapped[EntryType] = mapped_column(_enum(EntryType, "entry_type"))

    amount: Mapped[Decimal] = mapped_column(POINTS)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Redemption(Base):
    __tablename__ = "redemptions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), index=True)
    item_sku: Mapped[str] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(255))
    points_spent: Mapped[Decimal] = mapped_column(POINTS)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IssueEvent(Base):
    """Audit trail. Every vesting transition is driven by one of these."""

    __tablename__ = "issue_events"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=_uuid)
    issue_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("issues.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    issue = relationship("Issue", back_populates="events")
