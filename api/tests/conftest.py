import os
import uuid

import pytest

# Point the app at a throwaway database before anything imports settings.
os.environ.setdefault(
    "STARNET_DATABASE_URL",
    "postgresql+psycopg://starnet:starnet@localhost:55432/starnet_test",
)
os.environ.setdefault("STARNET_MEDIA_ROOT", "/tmp/starnet-test-media")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401


def _ensure_database() -> None:
    admin_url = settings.database_url.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    target = settings.database_url.rsplit("/", 1)[1]
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    _ensure_database()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    # Dropped and rebuilt rather than created-if-absent: this is a throwaway
    # database, and a stale schema from an earlier revision fails in ways that
    # look like engine bugs.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db(database):
    """A clean session per test. Truncating is faster than recreating and keeps
    the PostGIS extension in place."""
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def make_user(db):
    from app.auth import hash_password
    from app.models import User, utcnow

    def _make(handle: str, *, trust_a: float = 0.0, trust_b: float = 0.0, admin: bool = False, ward: str | None = "WARD-CENTRAL"):
        user = User(
            email=f"{handle}-{uuid.uuid4().hex[:6]}@test.local",
            handle=f"{handle}-{uuid.uuid4().hex[:6]}",
            password_hash=hash_password("testpassword"),
            trust_a=trust_a,
            trust_b=trust_b,
            trust_decayed_at=utcnow(),
            home_ward=ward,
            is_admin=admin,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def make_issue(db):
    """An issue in REPORTED state with its discoverer contribution in place --
    the state the report endpoint leaves behind."""
    from app.engines import priority as priority_engine
    from app.engines import reward, trust
    from app.engines.geo import ward_for, wkt_point
    from app.enums import Category, IssueStatus, Role
    from app.models import Issue, Report, utcnow

    def _make(discoverer, *, category=Category.POTHOLE, lat=12.9719, lng=77.5937):
        ward = ward_for(lat, lng)
        tau = trust.trust_of(discoverer)
        priority = priority_engine.priority(category, ward)
        equity = 1.0

        issue = Issue(
            category=category,
            status=IssueStatus.REPORTED,
            location=wkt_point(lat, lng),
            lat=lat,
            lng=lng,
            ward=ward,
            confidence=0.9,
            priority=priority,
            base_priority=priority,
            equity_multiplier=equity,
            reward_total=reward.compute_reward_total(priority, equity, tau),
            discoverer_id=discoverer.id,
        )
        db.add(issue)
        db.flush()
        db.add(
            Report(
                issue_id=issue.id,
                reporter_id=discoverer.id,
                role=Role.DISCOVERER,
                photo_path="seed.jpg",
                lat=lat,
                lng=lng,
                submitted_at=utcnow(),
                s_img=0.82,
                s_geo=0.9,
                independence_weight=1.0,
            )
        )
        reward.create_contribution(db, issue=issue, user_id=discoverer.id, role=Role.DISCOVERER)
        db.flush()
        return issue

    return _make


@pytest.fixture
def photo_bytes():
    """A small JPEG with real EXIF, so the geo checks have something to read.

    Written through Pillow rather than hand-rolled: the point is to exercise
    the same parsing path a phone's photo takes, including the degrees/minutes/
    seconds encoding and the hemisphere refs.
    """
    import io
    from fractions import Fraction

    from PIL import Image

    def _dms(value: float):
        degrees = int(abs(value))
        minutes = int((abs(value) - degrees) * 60)
        seconds = (abs(value) - degrees - minutes / 60) * 3600
        return (Fraction(degrees, 1), Fraction(minutes, 1), Fraction(round(seconds * 100), 100))

    def _make(*, captured_at=None, lat: float | None = None, lng: float | None = None) -> bytes:
        img = Image.new("RGB", (48, 48), (90, 90, 90))
        exif = Image.Exif()
        if captured_at is not None:
            exif[36867] = captured_at.strftime("%Y:%m:%d %H:%M:%S")
        if lat is not None and lng is not None:
            exif[34853] = {
                1: "N" if lat >= 0 else "S",
                2: _dms(lat),
                3: "E" if lng >= 0 else "W",
                4: _dms(lng),
            }
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        return buf.getvalue()

    return _make


@pytest.fixture
def client(db):
    """The real app, talking to the test session.

    No lifespan: the scheduler and the seed have nothing to do with these
    tests, and starting a background scheduler per test is a good way to get
    flakes that have nothing to do with the mechanism.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def register(client, db):
    """Sign a user up through the API; returns (user row, auth headers)."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models import User

    def _register(handle: str, *, ward: str = "WARD-CENTRAL"):
        suffix = _uuid.uuid4().hex[:6]
        # Not a .test/.local domain: these go through EmailStr, which rejects
        # reserved special-use names.
        email = f"{handle}-{suffix}@starnet-demo.org"
        response = client.post(
            "/auth/signup",
            json={
                "email": email,
                "password": "testpassword",
                "handle": f"{handle}-{suffix}",
                "home_ward": ward,
            },
        )
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        return user, {"Authorization": f"Bearer {token}"}

    return _register
