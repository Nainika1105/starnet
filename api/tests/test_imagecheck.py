"""Photo re-use detection.

Re-using one photograph as several faults is the cheapest fabrication there is:
the live-camera rule is UI enforcement only, EXIF can be stripped or forged,
and a screenshot defeats both. None of that changes the pixels.

These tests pin both halves of the contract -- what the hash must match, and
what it must NOT match -- because a perceptual hash with a badly chosen
threshold either misses every re-use or flags every photograph of the same
street.
"""
from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.config import settings
from app.engines import imagecheck
from app.engines.imagecheck import hamming, perceptual_hash


def photo(seed: int, *, size=(800, 600)) -> Image.Image:
    """A textured pseudo-photograph, so the hash has real gradient structure.

    Flat synthetic shapes are a trap here: they make every image resemble every
    other, and a threshold tuned against them would be meaningless.
    """
    rng = random.Random(seed)
    img = Image.new("RGB", size)
    pixels = img.load()
    for y in range(0, size[1], 4):
        for x in range(0, size[0], 4):
            value = (rng.randint(40, 200), rng.randint(40, 200), rng.randint(40, 200))
            for dy in range(4):
                for dx in range(4):
                    pixels[x + dx, y + dy] = value
    img = img.filter(ImageFilter.GaussianBlur(6))
    draw = ImageDraw.Draw(img)
    draw.ellipse([size[0] // 4, size[1] // 3, size[0] * 2 // 3, size[1] * 7 // 10], fill=(25, 25, 28))
    return img


def encode(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# --- what it must match ----------------------------------------------------


@pytest.mark.parametrize(
    "label,transform",
    [
        ("re-encoded hard", lambda im: encode(im, quality=35)),
        ("resized to half", lambda im: encode(im.resize((400, 300)))),
        ("resized to a quarter", lambda im: encode(im.resize((200, 150)))),
        ("blurred", lambda im: encode(im.filter(ImageFilter.GaussianBlur(1.5)))),
        ("saved again", lambda im: encode(im)),
    ],
)
def test_the_same_photo_survives_transit(label, transform):
    """What happens to an image on its way through a phone and an upload."""
    original = photo(1)
    assert imagecheck.looks_like_the_same_photo(
        perceptual_hash(encode(original)), perceptual_hash(transform(original))
    ), label


# --- what it must not match ------------------------------------------------


def test_different_photos_do_not_match():
    base = perceptual_hash(encode(photo(1)))
    distances = [hamming(base, perceptual_hash(encode(photo(s)))) for s in range(2, 12)]

    assert min(distances) > settings.photo_hash_max_distance, (
        f"closest unrelated photo was {min(distances)} bits away, threshold is "
        f"{settings.photo_hash_max_distance}"
    )


def test_cropping_defeats_this_hash():
    """Documented, not wished away.

    A 5% crop lands further from the original than some genuinely different
    photographs do, so no threshold separates the two cases. Pinned as a test
    so the limitation is visible rather than discovered later, and so that
    swapping in a DCT-based pHash shows up here as a change in behaviour.
    """
    original = photo(1)
    cropped = original.crop((40, 30, 760, 570))
    distance = hamming(perceptual_hash(encode(original)), perceptual_hash(encode(cropped)))

    assert distance > settings.photo_hash_max_distance, (
        "if this now passes, the hash got better -- update the docstring and "
        "the README rather than deleting the test"
    )


# --- mechanics -------------------------------------------------------------


def test_the_hash_is_sixteen_hex_characters():
    value = perceptual_hash(encode(photo(1)))
    assert len(value) == 16
    int(value, 16)


def test_the_top_bit_is_preserved():
    """Stored as hex because Postgres integers are signed: a 64-bit hash with
    the top bit set would overflow a BIGINT."""
    high = "ffffffffffffffff"
    assert hamming(high, "0000000000000000") == 64


def test_an_undecodable_upload_has_no_hash():
    assert perceptual_hash(b"not an image") is None
    assert hamming(None, "0" * 16) is None
    assert not imagecheck.looks_like_the_same_photo(None, "0" * 16)


def test_a_malformed_stored_hash_does_not_crash_comparison():
    assert hamming("zzzz", "0" * 16) is None


# --- the signal ------------------------------------------------------------


def _finding(distance: int, reporter_id="r"):
    return imagecheck.ReuseFinding(
        report_id="x", issue_id="i", reporter_id=reporter_id, distance=distance
    )


def test_no_matches_means_no_penalty():
    assert imagecheck.reuse_signal([], by_same_reporter=False) == 0.0


def test_an_exact_match_costs_more_than_a_near_one():
    exact = imagecheck.reuse_signal([_finding(0)], by_same_reporter=True)
    near = imagecheck.reuse_signal(
        [_finding(settings.photo_hash_max_distance - 1)], by_same_reporter=True
    )
    assert exact > near > 0


def test_a_match_beyond_the_threshold_costs_nothing():
    assert imagecheck.reuse_signal(
        [_finding(settings.photo_hash_max_distance + 1)], by_same_reporter=True
    ) == 0.0


def test_the_same_reporter_is_weighted_hardest():
    """One photograph submitted as several faults has no innocent reading. The
    same image from two accounts does -- a widely shared photo, a screenshot
    passed round a neighbourhood group."""
    own = imagecheck.reuse_signal([_finding(0)], by_same_reporter=True)
    other = imagecheck.reuse_signal([_finding(0)], by_same_reporter=False)
    assert own > other > 0


def test_the_closest_match_drives_the_penalty():
    assert imagecheck.reuse_signal(
        [_finding(9), _finding(0), _finding(7)], by_same_reporter=True
    ) == imagecheck.reuse_signal([_finding(0)], by_same_reporter=True)


# --- end to end through the endpoint --------------------------------------


def _file(client, headers, raw, *, lat=12.9719, lng=77.5937, category="POTHOLE"):
    return client.post(
        "/reports",
        headers=headers,
        data={"category": category, "lat": str(lat), "lng": str(lng)},
        files={"photo": ("live.jpg", raw, "image/jpeg")},
    )


def test_resubmitting_your_own_photo_weakens_the_report(db, client, register):
    """The fabrication this exists to catch: one picture, many faults.

    Both reports are filed at the SAME coordinates with different categories.
    That isolates the re-use signal: a different location would trip the
    impossible-travel check instead, and the same category would cluster the
    second report into the first as a corroboration. An earlier version of this
    test moved the second report across town and passed for the wrong reason.
    """
    _, headers = register("recycler")
    raw = encode(photo(42))

    first = _file(client, headers, raw, category="POTHOLE").json()
    second = _file(client, headers, raw, category="WATER_LEAK").json()

    assert second["joined_existing_issue"] is False, "different category, so a new issue"
    assert second["issue"]["confidence"] < first["issue"]["confidence"]


def test_the_reuse_is_recorded_for_audit(db, client, register):
    _, headers = register("recycler")
    raw = encode(photo(43))

    _file(client, headers, raw, category="POTHOLE")
    second = _file(client, headers, raw, category="GARBAGE").json()

    from sqlalchemy import select

    from app.models import IssueEvent

    db.flush()
    events = list(
        db.execute(
            select(IssueEvent.event_type).where(
                IssueEvent.issue_id == second["issue"]["id"]
            )
        ).scalars()
    )
    assert "PHOTO_REUSE_DETECTED" in events


def test_a_genuinely_new_photo_is_not_penalised(db, client, register):
    """The control for the test above: same shape, different picture."""
    _, headers = register("honest")

    first = _file(client, headers, encode(photo(1)), category="POTHOLE").json()
    second = _file(client, headers, encode(photo(2)), category="WATER_LEAK").json()

    from sqlalchemy import select

    from app.models import IssueEvent

    db.flush()
    events = list(
        db.execute(
            select(IssueEvent.event_type).where(
                IssueEvent.issue_id == second["issue"]["id"]
            )
        ).scalars()
    )
    assert "PHOTO_REUSE_DETECTED" not in events


def test_the_same_photo_from_another_account_costs_less(db, client, register):
    """It is still suspicious, but it has readings that are not fraud -- a
    widely shared photo, a screenshot passed round a neighbourhood group."""
    _, first_headers = register("first")
    _, second_headers = register("second")
    raw = encode(photo(44))

    _file(client, first_headers, raw, category="POTHOLE")
    other_account = _file(client, second_headers, raw, category="WATER_LEAK").json()

    _, third_headers = register("third")
    _file(client, third_headers, encode(photo(45)), category="TREE")
    same_account = _file(client, third_headers, encode(photo(45)), category="FOOTPATH").json()

    from sqlalchemy import select

    from app.models import IssueEvent

    db.flush()

    def penalty(issue_id):
        payload = db.execute(
            select(IssueEvent.payload).where(
                IssueEvent.issue_id == issue_id,
                IssueEvent.event_type == "PHOTO_REUSE_DETECTED",
            )
        ).scalars().one()
        return payload["s_img_penalty"]

    assert penalty(same_account["issue"]["id"]) > penalty(other_account["issue"]["id"]) > 0


def test_the_hash_is_stored_with_the_report(db, client, register):
    _, headers = register("reporter")
    raw = encode(photo(7))
    created = _file(client, headers, raw).json()

    from sqlalchemy import select

    from app.models import Report

    stored = db.execute(
        select(Report).where(Report.issue_id == created["issue"]["id"])
    ).unique().scalars().one()

    assert stored.photo_hash == perceptual_hash(raw)
