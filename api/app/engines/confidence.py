"""Multi-signal confidence (design doc section 6).

C(r) = sigma( w0 + w1*s_img + w2*s_geo + w3*s_cor + w4*tau )

`fusion` is the default and the real mechanism: the four signals are weighed
together and the result decides ACCEPT / VERIFY / REJECT. s_geo and s_cor are
computed from the submission and from the corroborating reports; s_img is still
a per-category constant (see below) and is the one remaining stub in the chain.

Under fusion a lone report from an ordinary account lands in the VERIFY band,
which is the point: one stranger's photo should not put work on the city's
queue. Corroboration or a trusted reporter clears theta_hi; a photo with no
EXIF from a new account falls under theta_lo.

STARNET_CONFIDENCE_MODE=stub restores the Phase-1 shortcut that auto-accepts a
single plausible report. The signals are computed and persisted either way, so
the mode changes the decision, not the data.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.config import settings
from app.enums import Category
from app.engines.geo import haversine_m, in_service_area

# --- s_img -----------------------------------------------------------------
# STUB. The real version fine-tunes YOLO on RDD2022 for road damage and needs
# per-category datasets for the rest. These constants stand in for "the photo
# plausibly contains the claimed fault type" and are deliberately uniform-ish:
# they encode no real visual evidence, so nothing downstream should treat a
# difference between them as meaningful.
_STUB_S_IMG: dict[Category, float] = {
    Category.POTHOLE: 0.82,
    Category.STREETLIGHT: 0.74,
    Category.WATER_LEAK: 0.80,
    Category.GARBAGE: 0.86,
    Category.TREE: 0.78,
    Category.FOOTPATH: 0.76,
}


def score_image(category: Category, photo_path: str | None) -> float:
    """STUB: returns a plausible [0,1] without looking at the image.

    Seam for the real implementation: swap the body for a CLIP zero-shot
    similarity between the image and the category label, then for a fine-tuned
    detector. Signature stays the same.
    """
    base = _STUB_S_IMG.get(category, 0.75)
    return base if photo_path else base * 0.5  # no photo is weak evidence


# --- s_geo -----------------------------------------------------------------
# Computed for real, from four independent checks on the submission itself.
# Each one subtracts from a clean baseline, so the signal degrades gradually
# rather than flipping a report from believed to rejected on one weak reading.

_GEO_BASE = 0.9           # inside the serviced area, nothing suspicious
_PENALTY_NO_EXIF = 0.2    # re-encoded, screenshotted, or stripped by a share sheet
_PENALTY_STALE_DAY = 0.3  # photographed hours ago, submitted now
_PENALTY_NO_GPS = 0.1     # the camera had no fix, or the app withheld it
_PENALTY_GPS_FAR = 0.5    # the photo was taken somewhere else entirely
_BONUS_LIVE = 0.1         # captured within ten minutes
_BONUS_GPS_MATCH = 0.1    # the photo's own GPS agrees with the claim


def gps_agreement(
    *, claimed_lat: float, claimed_lng: float, photo_lat: float | None, photo_lng: float | None
) -> tuple[float, float | None]:
    """How much the photo's own EXIF GPS supports the claimed location.

    Returns (delta to apply to s_geo, metres apart or None). Consumer GPS is
    routinely tens of metres out and urban canyons are worse, so the match
    radius is loose: this is here to catch a photo taken streets away, not to
    punish a bad fix.
    """
    if photo_lat is None or photo_lng is None:
        return -_PENALTY_NO_GPS, None

    distance = haversine_m(claimed_lat, claimed_lng, photo_lat, photo_lng)
    if distance <= settings.exif_gps_match_m:
        return _BONUS_GPS_MATCH, distance
    if distance >= settings.exif_gps_far_m:
        return -_PENALTY_GPS_FAR, distance

    # Between the two radii, scale the penalty linearly rather than stepping:
    # a report 300 m out is probably a sloppy pin, one 900 m out probably is not.
    span = settings.exif_gps_far_m - settings.exif_gps_match_m
    fraction = (distance - settings.exif_gps_match_m) / span
    return -_PENALTY_GPS_FAR * fraction, distance


def staleness_delta(
    *, exif_time: datetime | None, submitted_at: datetime
) -> tuple[float, float | None]:
    """How much the capture-to-submission gap supports a live sighting.

    A stale photo is the cheapest fabrication there is -- find an old picture of
    a pothole, submit it as new -- so this is one of the signals that matters.
    """
    if exif_time is None:
        return -_PENALTY_NO_EXIF, None

    if exif_time.tzinfo is None:
        exif_time = exif_time.replace(tzinfo=timezone.utc)

    age = abs((submitted_at - exif_time).total_seconds())
    if age <= 600:
        return _BONUS_LIVE, age
    if age <= 3600:
        return 0.0, age
    if age <= 86_400:
        return -_PENALTY_STALE_DAY, age
    return -(_GEO_BASE - 0.2), age  # over a day old: floor the score at 0.2


def travel_implausibility(
    *,
    lat: float,
    lng: float,
    submitted_at: datetime,
    previous_lat: float | None,
    previous_lng: float | None,
    previous_at: datetime | None,
) -> tuple[float, float | None]:
    """Implied ground speed since this user's previous report.

    One account cannot be in two places at once. Someone filing from across the
    city seconds later is either sharing the account or scripting it, and either
    way the submission should not be weighed as an eyewitness account.

    Returns (implied km/h, metres travelled), or (0.0, None) with no prior.
    """
    if previous_lat is None or previous_lng is None or previous_at is None:
        return 0.0, None

    if previous_at.tzinfo is None:
        previous_at = previous_at.replace(tzinfo=timezone.utc)

    metres = haversine_m(previous_lat, previous_lng, lat, lng)
    seconds = (submitted_at - previous_at).total_seconds()
    if seconds <= 0:
        # Same instant, different place: infinitely fast by definition.
        return float("inf") if metres > settings.cluster_distance_m else 0.0, metres

    return (metres / seconds) * 3.6, metres


def score_geo(
    *,
    lat: float,
    lng: float,
    exif_time: datetime | None,
    submitted_at: datetime,
    photo_lat: float | None = None,
    photo_lng: float | None = None,
    previous_lat: float | None = None,
    previous_lng: float | None = None,
    previous_at: datetime | None = None,
) -> float:
    """GPS/timestamp coherence and location plausibility, in [0, 1].

    Outside the serviced area is the one hard zero: we cannot act on it and
    have no business paying for it. Everything else is a deduction.
    """
    if not in_service_area(lat, lng):
        return 0.0

    score = _GEO_BASE
    score += staleness_delta(exif_time=exif_time, submitted_at=submitted_at)[0]
    score += gps_agreement(
        claimed_lat=lat, claimed_lng=lng, photo_lat=photo_lat, photo_lng=photo_lng
    )[0]

    speed_kmh, _ = travel_implausibility(
        lat=lat,
        lng=lng,
        submitted_at=submitted_at,
        previous_lat=previous_lat,
        previous_lng=previous_lng,
        previous_at=previous_at,
    )
    if speed_kmh > settings.max_travel_speed_kmh:
        # Not a deduction: the account demonstrably was not there, so its
        # geographic evidence is worth nothing. The report still stands or
        # falls on the other signals and on-site verification.
        return 0.0

    return max(0.0, min(1.0, score))


# --- s_cor -----------------------------------------------------------------


def score_corroboration(weighted_sum: float) -> float:
    """s_cor = 1 - exp(-S / s0), S = sum of rho_i * tau_i.

    Saturating on purpose: the first independent corroboration moves the needle
    a lot, the tenth barely at all, so flooding an issue with accounts buys
    almost nothing.
    """
    return 1.0 - math.exp(-max(0.0, weighted_sum) / settings.corroboration_s0)


def independence_weight(
    *,
    shared_device: bool = False,
    flagged_collusion: bool = False,
    reciprocal_pair: bool = False,
    temporal_burst: bool = False,
) -> float:
    """rho_i = max(rho_min, (1-d)(1-c)(1-p)(1-b)).

    STUB-level inputs in Phase 1 (see anti-abuse); the formula is the real one.
    """
    rho = 1.0
    for flag in (shared_device, flagged_collusion, reciprocal_pair, temporal_burst):
        if flag:
            rho *= 0.0
    return max(settings.rho_min, rho)


# --- fusion ----------------------------------------------------------------


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def fuse(*, s_img: float, s_geo: float, s_cor: float, tau: float) -> float:
    z = (
        settings.w0
        + settings.w_img * s_img
        + settings.w_geo * s_geo
        + settings.w_cor * s_cor
        + settings.w_trust * tau
    )
    return _sigmoid(z)


def confidence(*, s_img: float, s_geo: float, s_cor: float, tau: float, mode: str | None = None) -> float:
    """The logistic fusion by default; `stub` is the Phase-1 shortcut."""
    mode = mode or getattr(settings, "confidence_mode", "stub")
    if mode == "fusion":
        return fuse(s_img=s_img, s_geo=s_geo, s_cor=s_cor, tau=tau)
    # The Phase-1 shortcut: a blend of the two signals we compute, floored so a
    # well-formed report auto-accepts and the end-to-end slice runs with no
    # verifier in the loop. Kept so the earlier behaviour is still reachable.
    return max(0.0, min(1.0, 0.5 * s_img + 0.5 * s_geo + 0.15))


class Decision:
    ACCEPT = "ACCEPT"
    VERIFY = "VERIFY"
    REJECT = "REJECT"


def decide(confidence_value: float) -> str:
    if confidence_value >= settings.theta_hi:
        return Decision.ACCEPT
    if confidence_value < settings.theta_lo:
        return Decision.REJECT
    return Decision.VERIFY
