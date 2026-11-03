"""Image checks that do not need a model (design doc section 6, s_img).

The semantic question -- "is there actually a pothole in this photograph?" --
needs a trained model and is still a stub (see `confidence.score_image`). But
the question this module answers is different, cheaper, and arguably protects
the mechanism better: "have I seen this photograph before?"

That matters because photo re-use is the cheapest fabrication available. Find
one picture of a pothole and submit it as a new fault in twenty streets. The
live-camera requirement in the app is UI enforcement only, EXIF can be stripped
or forged, and a screenshot defeats both -- but none of that changes the
pixels, which is what this looks at.

The hash is a difference hash: the image is reduced to a 9x8 greyscale grid and
each pixel compared with its right-hand neighbour, giving 64 bits describing
the image's gradient structure.

Measured on this implementation, as Hamming distance out of 64 bits:

    re-encoded at JPEG q35       0 bits
    resized to 50%               2 bits
    resized to 25%               1 bit
    slight blur                  1 bit
    saved again                  0 bits
    ---
    cropped 5% at the border    14 bits
    unrelated photographs       10-19 bits

The two groups cluster cleanly, so the threshold goes in the gap between them
rather than at either edge: six bits, with four of headroom above every true
positive and four below the closest false positive.

What it does NOT catch is cropping, and that is a real limitation rather than a
tuning choice -- a 5% crop lands further from the original than some unrelated
photographs do, so no threshold separates those two cases. The upgrade is a
DCT-based pHash, which is much more crop-robust; it needs a DCT, hence numpy or
scipy, which is why it is not here yet.

It is NOT a cryptographic hash and must never be used as one: collisions are
the entire point of the design, and near-collisions are what it reports.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Report, utcnow

HASH_BITS = 64
_GRID_W, _GRID_H = 9, 8  # 9 columns gives 8 comparisons per row


def perceptual_hash(raw: bytes) -> str | None:
    """64-bit difference hash as 16 hex characters, or None if undecodable.

    Hex rather than an integer because the top bit is meaningful and Postgres
    BIGINT is signed: storing a 64-bit hash as a signed integer invites a
    silent overflow at exactly the values most likely to be hit.
    """
    try:
        with Image.open(io.BytesIO(raw)) as img:
            small = img.convert("L").resize((_GRID_W, _GRID_H), Image.Resampling.LANCZOS)
            pixels = list(small.getdata())
    except Exception:
        return None

    bits = 0
    position = 0
    for row in range(_GRID_H):
        offset = row * _GRID_W
        for col in range(_GRID_W - 1):
            left = pixels[offset + col]
            right = pixels[offset + col + 1]
            if left > right:
                bits |= 1 << position
            position += 1

    return f"{bits:016x}"


def hamming(a: str | None, b: str | None) -> int | None:
    """Number of differing bits, or None if either hash is missing."""
    if not a or not b:
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


def looks_like_the_same_photo(a: str | None, b: str | None) -> bool:
    distance = hamming(a, b)
    return distance is not None and distance <= settings.photo_hash_max_distance


@dataclass(frozen=True)
class ReuseFinding:
    """A previously stored photo that matches the one being submitted."""

    report_id: object
    issue_id: object
    reporter_id: object
    distance: int


def _penalty(distance: int) -> float:
    """How much of s_img a match costs, scaled by how close it is.

    An exact match is the strongest signal available that a photo is not
    evidence of anything new. A near match could be two genuine photographs of
    the same fault from the same spot, which is exactly what an honest
    corroboration looks like -- so it is discounted, not condemned.
    """
    ceiling = max(1, settings.photo_hash_max_distance)
    closeness = 1.0 - (distance / (ceiling + 1))
    return max(0.0, min(1.0, closeness))


def reuse_signal(matches: list[ReuseFinding], *, by_same_reporter: bool) -> float:
    """[0, 1]: how much the image evidence should be discounted.

    Re-use by the SAME account is weighted hardest. Somebody submitting one
    photograph as several different faults is the fabrication this exists to
    catch, and there is no innocent reading of it. The same image arriving from
    two accounts is suspicious too, but it has readings that are not fraud --
    a widely shared photo of a famous pothole, or a screenshot passed around a
    neighbourhood group -- so it costs less.
    """
    if not matches:
        return 0.0

    closest = min(match.distance for match in matches)
    base = _penalty(closest)
    weight = (
        settings.photo_reuse_same_reporter_weight
        if by_same_reporter
        else settings.photo_reuse_other_reporter_weight
    )
    return max(0.0, min(1.0, base * weight))


def find_reuse(
    db: Session, photo_hash: str | None, *, reporter_id=None, exclude_issue_id=None
) -> list[ReuseFinding]:
    """Previously stored photos that match this one.

    A linear scan over the recent window. That is honest at demo scale and
    wrong at city scale: the seam for real volume is a BK-tree over the hashes,
    or Postgres storing them as bit(64) and indexing popcount of the XOR. The
    window bounds the cost either way, and recency is the right axis anyway --
    a photo re-used two years apart is a far weaker signal than one re-used
    the same week.
    """
    if not photo_hash:
        return []

    from datetime import timedelta

    since = utcnow() - timedelta(days=settings.photo_reuse_window_days)
    stmt = select(Report).where(
        Report.photo_hash.is_not(None), Report.submitted_at >= since
    )
    if reporter_id is not None:
        stmt = stmt.where(Report.reporter_id == reporter_id)
    if exclude_issue_id is not None:
        stmt = stmt.where(Report.issue_id != exclude_issue_id)

    findings: list[ReuseFinding] = []
    for report in db.execute(stmt).unique().scalars():
        distance = hamming(photo_hash, report.photo_hash)
        if distance is not None and distance <= settings.photo_hash_max_distance:
            findings.append(
                ReuseFinding(
                    report_id=report.id,
                    issue_id=report.issue_id,
                    reporter_id=report.reporter_id,
                    distance=distance,
                )
            )

    return sorted(findings, key=lambda f: f.distance)
