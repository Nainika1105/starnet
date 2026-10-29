"""Demo seed data.

Everything here goes through the same engines and lifecycle calls the API uses,
so the seeded ledger is real -- not fixtures pasted into the tables.
"""
from __future__ import annotations

import io
import logging
import random
from fractions import Fraction

from PIL import Image, ImageDraw
from sqlalchemy import func, select

from app import lifecycle, storage
from app.auth import hash_password
from app.db import SessionLocal
from app.engines import confidence as conf
from app.engines import priority as priority_engine
from app.engines import reward, trust
from app.engines.geo import ward_for, wkt_point
from app.enums import Category, IssueStatus, Role, TaskStatus, TaskType, Verdict
from app.models import Issue, Report, Task, User, utcnow

log = logging.getLogger("starnet.seed")

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_GPS_IFD = 34853

PASSWORD = "starnet123"

# (handle, email, trust_a, trust_b, ward, is_admin)
PEOPLE = [
    ("city_admin", "admin@starnet.demo", 0, 0, None, True),
    ("asha_r", "asha@starnet.demo", 4, 1, "WARD-CENTRAL", False),
    ("meera_k", "meera@starnet.demo", 8, 0, "WARD-CENTRAL", False),
    ("vikram_s", "vikram@starnet.demo", 9, 1, "WARD-NORTH", False),
    ("dev_p", "dev@starnet.demo", 3, 0, "WARD-SOUTH", False),
    ("newcomer", "new@starnet.demo", 0, 0, "WARD-CENTRAL", False),
]

# (category, lat, lng, description)
FAULTS = [
    (Category.POTHOLE, 12.9719, 77.5937, "Deep pothole in the right lane near the junction."),
    (Category.WATER_LEAK, 12.9655, 77.5855, "Mains leaking across the footpath, road is flooding."),
    (Category.STREETLIGHT, 12.9810, 77.5990, "Three lights out on the stretch, very dark."),
    (Category.GARBAGE, 12.9600, 77.5700, "Illegal dumping piling up behind the market."),
    (Category.TREE, 13.0450, 77.6200, "Large branch down, blocking half the road."),
    (Category.FOOTPATH, 12.9200, 77.6100, "Footpath slabs broken, tripping hazard outside school."),
    (Category.POTHOLE, 13.0500, 77.6300, "Cluster of potholes after the rain."),
    (Category.WATER_LEAK, 12.8900, 77.6000, "Slow leak at the valve, has been weeks."),
]


def _dms(value: float) -> tuple[Fraction, Fraction, Fraction]:
    degrees = int(abs(value))
    minutes = int((abs(value) - degrees) * 60)
    seconds = (abs(value) - degrees - minutes / 60) * 3600
    return (Fraction(degrees, 1), Fraction(minutes, 1), Fraction(round(seconds * 100), 100))


def _placeholder_photo(
    label: str, seed: int, *, lat: float | None = None, lng: float | None = None
) -> tuple[str, storage.PhotoMeta]:
    """A generated stand-in image, written with the EXIF a live capture carries.

    The capture time and GPS matter: the geo-coherence check reads them, and a
    seed whose photos had no EXIF would score every demo report as a re-encoded
    image and leave the dashboard full of rejections. Returns the storage key
    and the metadata read back out of the image, so the seed scores a photo
    exactly as the report endpoint would.
    """
    rng = random.Random(seed)
    img = Image.new("RGB", (640, 480), (rng.randint(60, 140), rng.randint(60, 140), rng.randint(60, 140)))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 600, 440], outline=(240, 240, 240), width=4)
    draw.text((60, 70), f"DEMO PHOTO\n{label}", fill=(245, 245, 245))

    exif = Image.Exif()
    exif[_EXIF_DATETIME_ORIGINAL] = utcnow().strftime("%Y:%m:%d %H:%M:%S")
    if lat is not None and lng is not None:
        exif[_EXIF_GPS_IFD] = {
            1: "N" if lat >= 0 else "S",
            2: _dms(lat),
            3: "E" if lng >= 0 else "W",
            4: _dms(lng),
        }

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70, exif=exif)
    raw = buf.getvalue()
    return storage.save(raw), storage.read_photo_meta(raw)


def seed_if_empty() -> None:
    with SessionLocal() as db:
        if db.execute(select(func.count(User.id))).scalar_one() > 0:
            log.info("Seed skipped: users already exist")
            return
        _seed(db)
        db.commit()
        log.info("Seeded %d demo users and %d issues", len(PEOPLE), len(FAULTS))


def _seed(db) -> None:
    users: dict[str, User] = {}
    for handle, email, a, b, ward, is_admin in PEOPLE:
        user = User(
            email=email,
            handle=handle,
            password_hash=hash_password(PASSWORD),
            trust_a=float(a),
            trust_b=float(b),
            trust_decayed_at=utcnow(),
            home_ward=ward,
            is_admin=is_admin,
        )
        db.add(user)
        users[handle] = user
    db.flush()

    reporters = [users["asha_r"], users["vikram_s"], users["dev_p"], users["newcomer"]]

    created: list[Issue] = []
    for index, (category, lat, lng, description) in enumerate(FAULTS):
        reporter = reporters[index % len(reporters)]
        issue = _file_report(db, reporter, category, lat, lng, description, index)
        created.append(issue)

    # Drive a few of them further along the lifecycle so the demo opens on a
    # dashboard with work in every state.
    admin = users["city_admin"]
    confirmer = users["meera_k"]

    # 1. One issue corroborated by a second citizen -- which is what lifts it
    #    over theta_hi under fusion, with no verifier needed.
    _corroborate(db, created[0], users["dev_p"])

    # Issues 1-3 are driven on through the municipal flow, so each needs to
    # have cleared verification first. Issue 4 (index 4) is left with its
    # verify task open on purpose: the demo opens with one report waiting on a
    # routed verifier.
    for issue in (created[1], created[2], created[3]):
        _run_verification(db, issue)

    # 2. One assigned to a crew.
    lifecycle.assign(db, created[1], assignee_label="Water Works Crew 3", actor_id=admin.id)

    # 3. One resolved and awaiting confirmation -- the interesting state.
    lifecycle.assign(db, created[2], assignee_label="Street Lighting Crew 1", actor_id=admin.id)
    lifecycle.mark_resolved(db, created[2], actor_id=admin.id)

    # 4. One fully through the mechanism: resolved, confirmed, paid.
    lifecycle.assign(db, created[3], assignee_label="Sanitation Crew 2", actor_id=admin.id)
    task = lifecycle.mark_resolved(db, created[3], actor_id=admin.id)
    if task is not None:
        photo, _ = _placeholder_photo(
            "repair confirmed", 900, lat=created[3].lat, lng=created[3].lng
        )
        actual_confirmer = db.get(User, task.assignee_id) or confirmer
        lifecycle.confirm_fix(
            db, created[3], task, confirmer=actual_confirmer, verdict=Verdict.CONFIRMED, photo_key=photo
        )

    # 5. One rejected by audit, to show forfeiture in the ledger.
    if created[7].status != IssueStatus.REJECTED:
        lifecycle.reject(
            db, created[7], reason="Audit found no fault at this location", actor_id=admin.id
        )


def _file_report(db, reporter: User, category: Category, lat: float, lng: float, description: str, index: int) -> Issue:
    photo_key, meta = _placeholder_photo(category.value, index, lat=lat, lng=lng)
    submitted_at = utcnow()

    s_img = conf.score_image(category, photo_key)
    s_geo = conf.score_geo(
        lat=lat,
        lng=lng,
        exif_time=meta.captured_at,
        submitted_at=submitted_at,
        photo_lat=meta.lat,
        photo_lng=meta.lng,
    )
    tau = trust.trust_of(reporter)

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
        discoverer_id=reporter.id,
    )
    db.add(issue)
    db.flush()

    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=reporter.id,
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
    lifecycle.log_event(db, issue, "REPORTED", actor_id=reporter.id, confidence=round(confidence_value, 4))
    reward.create_contribution(db, issue=issue, user_id=reporter.id, role=Role.DISCOVERER)

    decision = conf.decide(confidence_value)
    if decision == conf.Decision.ACCEPT:
        lifecycle.mark_verified(db, issue, actor_id=None, reason="Auto-accepted on confidence")
    elif decision == conf.Decision.REJECT:
        lifecycle.reject(db, issue, reason=f"Confidence {confidence_value:.2f} below theta_lo")
    else:
        lifecycle.open_verification_task(db, issue)

    db.flush()
    return issue


def _run_verification(db, issue: Issue) -> bool:
    """Have the routed verifier attend the issue, as a real user would.

    Under the default `fusion` confidence mode a single uncorroborated report
    sits in the VERIFY band, so most seeded issues start with an open verify
    task. Completing them here is what gives the demo a queue with work in it;
    one issue is deliberately left unverified so the verifier path is visible.
    """
    task = db.execute(
        select(Task).where(
            Task.issue_id == issue.id,
            Task.type == TaskType.VERIFY,
            Task.status == TaskStatus.OPEN,
        )
    ).scalars().first()
    if task is None:
        return issue.verified_at is not None

    verifier = db.get(User, task.assignee_id)
    lifecycle.complete_verification(
        db,
        issue,
        task,
        verifier=verifier,
        verdict=Verdict.CONFIRMED,
        photo_key=_placeholder_photo(
            "verification", 700 + hash(str(issue.id)) % 100, lat=issue.lat, lng=issue.lng
        )[0],
    )
    db.flush()
    return True


def _corroborate(db, issue: Issue, user: User) -> None:
    submitted_at = utcnow()
    photo_key, meta = _placeholder_photo(
        f"{issue.category.value} corroboration", 800, lat=issue.lat, lng=issue.lng
    )
    s_img = conf.score_image(issue.category, photo_key)
    s_geo = conf.score_geo(
        lat=issue.lat,
        lng=issue.lng,
        exif_time=meta.captured_at,
        submitted_at=submitted_at,
        photo_lat=meta.lat,
        photo_lng=meta.lng,
    )
    db.add(
        Report(
            issue_id=issue.id,
            reporter_id=user.id,
            role=Role.CORROBORATOR,
            photo_path=photo_key,
            lat=issue.lat,
            lng=issue.lng,
            description="I see this too.",
            exif_time=meta.captured_at,
            photo_lat=meta.lat,
            photo_lng=meta.lng,
            submitted_at=submitted_at,
            s_img=s_img,
            s_geo=s_geo,
            independence_weight=1.0,
        )
    )
    db.flush()
    reward.create_contribution(
        db,
        issue=issue,
        user_id=user.id,
        role=Role.CORROBORATOR,
        stages_already_passed=lifecycle.stages_passed(issue),
    )
    confidence_value = lifecycle.recompute_confidence(db, issue)
    lifecycle.log_event(
        db, issue, "CORROBORATED", actor_id=user.id, confidence=round(confidence_value, 4)
    )

    # Independent corroboration is what clears theta_hi under fusion, so the
    # issue enters the queue with no verifier in the loop.
    if issue.status == IssueStatus.REPORTED and conf.decide(confidence_value) == conf.Decision.ACCEPT:
        lifecycle.mark_verified(
            db, issue, actor_id=None, reason="Corroboration pushed confidence over theta_hi"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_if_empty()
