"""Phase-2 signal tests: EXIF, geo coherence, confidence fusion, priority ageing.

These cover the inputs to the mechanism. The money itself is covered by
test_vesting.py, which stays the suite that must never go red.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.engines import confidence as conf
from app.engines import priority as priority_engine
from app.engines.geo import haversine_m
from app.enums import Category
from app.storage import read_photo_meta

JUNCTION = (12.9719, 77.5937)  # the demo pothole, WARD-CENTRAL


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- EXIF ------------------------------------------------------------------


def test_exif_capture_time_and_gps_round_trip(photo_bytes):
    captured = _now().replace(microsecond=0)
    meta = read_photo_meta(photo_bytes(captured_at=captured, lat=JUNCTION[0], lng=JUNCTION[1]))

    assert meta.captured_at == captured.astimezone(timezone.utc)
    assert meta.has_gps
    # DMS encoding is lossy at the third decimal; metres is the unit that matters.
    assert haversine_m(JUNCTION[0], JUNCTION[1], meta.lat, meta.lng) < 2.0


def test_exif_southern_western_hemispheres_are_signed(photo_bytes):
    meta = read_photo_meta(photo_bytes(captured_at=_now(), lat=-33.9249, lng=-18.4241))
    assert meta.lat < 0 and meta.lng < 0


def test_photo_with_no_exif_is_not_an_error(photo_bytes):
    meta = read_photo_meta(photo_bytes())
    assert meta.captured_at is None and not meta.has_gps


def test_undecodable_bytes_are_not_an_error():
    """A corrupt upload is a weak report, not a 500."""
    meta = read_photo_meta(b"this is not an image")
    assert meta.captured_at is None and not meta.has_gps


def test_zero_zero_gps_is_treated_as_no_fix(photo_bytes):
    meta = read_photo_meta(photo_bytes(captured_at=_now(), lat=0.0, lng=0.0))
    assert not meta.has_gps


# --- s_geo -----------------------------------------------------------------


def test_outside_service_area_scores_zero():
    assert conf.score_geo(lat=48.85, lng=2.35, exif_time=None, submitted_at=_now()) == 0.0


def test_fresher_photo_scores_higher_than_a_stale_one():
    now = _now()
    live = conf.score_geo(lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now)
    hours_old = conf.score_geo(
        lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now - timedelta(hours=6), submitted_at=now
    )
    days_old = conf.score_geo(
        lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now - timedelta(days=3), submitted_at=now
    )

    assert live > hours_old > days_old
    assert days_old == pytest.approx(0.2, abs=0.11)


def test_missing_exif_scores_below_a_live_capture():
    now = _now()
    assert conf.score_geo(
        lat=JUNCTION[0], lng=JUNCTION[1], exif_time=None, submitted_at=now
    ) < conf.score_geo(lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now)


def test_photo_gps_agreeing_with_the_claim_helps_and_disagreeing_hurts():
    now = _now()
    agree = conf.score_geo(
        lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now,
        photo_lat=JUNCTION[0] + 0.0003, photo_lng=JUNCTION[1],  # ~33 m, ordinary GPS error
    )
    far = conf.score_geo(
        lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now,
        photo_lat=JUNCTION[0] + 0.05, photo_lng=JUNCTION[1],  # ~5.5 km away
    )
    none = conf.score_geo(lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now)

    assert agree > none > far


def test_gps_penalty_scales_rather_than_stepping():
    """A sloppy pin should not be scored like a photo from another suburb."""
    now = _now()
    kwargs = dict(lat=JUNCTION[0], lng=JUNCTION[1], exif_time=now, submitted_at=now)

    def at(metres: float) -> float:
        # ~111 km per degree of latitude.
        return conf.score_geo(
            **kwargs, photo_lat=JUNCTION[0] + metres / 111_000.0, photo_lng=JUNCTION[1]
        )

    assert at(200) > at(500) > at(900)


def test_impossible_travel_voids_the_geo_signal():
    """One account cannot be in two places at once."""
    now = _now()
    score = conf.score_geo(
        lat=JUNCTION[0],
        lng=JUNCTION[1],
        exif_time=now,
        submitted_at=now,
        previous_lat=13.05,       # ~9 km north
        previous_lng=77.63,
        previous_at=now - timedelta(seconds=30),
    )
    assert score == 0.0


def test_plausible_travel_does_not_penalise():
    now = _now()
    score = conf.score_geo(
        lat=JUNCTION[0],
        lng=JUNCTION[1],
        exif_time=now,
        submitted_at=now,
        previous_lat=12.9750,     # ~400 m away, twenty minutes ago
        previous_lng=77.5937,
        previous_at=now - timedelta(minutes=20),
    )
    assert score > 0.8


def test_travel_speed_is_computed_in_kmh():
    now = _now()
    speed, metres = conf.travel_implausibility(
        lat=JUNCTION[0],
        lng=JUNCTION[1],
        submitted_at=now,
        previous_lat=JUNCTION[0] + 0.009,  # ~1 km
        previous_lng=JUNCTION[1],
        previous_at=now - timedelta(minutes=30),
    )
    assert metres == pytest.approx(1000, rel=0.05)
    assert speed == pytest.approx(2.0, rel=0.05)  # 1 km in half an hour


def test_first_ever_report_has_no_travel_history():
    assert conf.travel_implausibility(
        lat=JUNCTION[0], lng=JUNCTION[1], submitted_at=_now(),
        previous_lat=None, previous_lng=None, previous_at=None,
    ) == (0.0, None)


# --- fusion ----------------------------------------------------------------


def _fuse(**kwargs) -> float:
    return conf.confidence(mode="fusion", **kwargs)


def test_lone_report_from_a_new_account_needs_a_verifier():
    """The central design claim: one stranger's photo does not queue city work."""
    value = _fuse(s_img=0.82, s_geo=0.9, s_cor=0.0, tau=0.5)
    assert conf.decide(value) == conf.Decision.VERIFY


def test_corroboration_clears_the_accept_threshold():
    s_cor = conf.score_corroboration(0.9)  # one trusted independent corroborator
    value = _fuse(s_img=0.82, s_geo=1.0, s_cor=s_cor, tau=0.9)
    assert conf.decide(value) == conf.Decision.ACCEPT


def test_weak_submission_from_a_new_account_is_rejected():
    """No photo, no EXIF, no history: under theta_lo, and never paid at all."""
    value = _fuse(s_img=conf.score_image(Category.POTHOLE, None), s_geo=0.7, s_cor=0.0, tau=0.5)
    assert conf.decide(value) == conf.Decision.REJECT


def test_confidence_rises_with_every_signal():
    base = dict(s_img=0.6, s_geo=0.6, s_cor=0.3, tau=0.5)
    for signal in base:
        better = dict(base, **{signal: base[signal] + 0.2})
        assert _fuse(**better) > _fuse(**base), signal


def test_corroboration_saturates():
    """The tenth corroborator must be worth far less than the first."""
    first = conf.score_corroboration(1.0) - conf.score_corroboration(0.0)
    tenth = conf.score_corroboration(10.0) - conf.score_corroboration(9.0)
    assert first > 10 * tenth

    # Bounded above, so no amount of flooding can carry s_cor past its weight.
    assert conf.score_corroboration(1e6) <= 1.0
    # And by ten corroborators there is almost nothing left to buy: the whole
    # remaining headroom is a fifth of a percent of the signal's range.
    assert 1.0 - conf.score_corroboration(10.0) < 0.002


def test_shared_device_collapses_independence_to_the_floor():
    assert conf.independence_weight(shared_device=True) == settings.rho_min
    assert conf.independence_weight() == 1.0


# --- priority and equity ---------------------------------------------------


def test_escalation_grows_with_age_and_stays_bounded():
    rates = [priority_engine.escalation_component(Category.WATER_LEAK, h) for h in (0, 24, 168, 720, 10_000)]
    assert rates == sorted(rates)
    assert rates[0] > 0  # a brand-new leak is already escalating
    assert rates[-1] <= 1.0


def test_an_ageing_issue_outranks_a_fresh_identical_one():
    fresh = priority_engine.priority(Category.POTHOLE, "WARD-CENTRAL", 0)
    stale = priority_engine.priority(Category.POTHOLE, "WARD-CENTRAL", 24 * 30)
    assert stale > fresh


def test_priority_stays_in_range_for_every_category_and_ward():
    for category in Category:
        for ward in priority_engine.all_wards():
            for age in (0, 1000, 100_000):
                assert 0.0 <= priority_engine.priority(category, ward, age) <= 1.0


def test_equity_multiplier_only_ever_helps_and_is_capped():
    silent = priority_engine.equity_multiplier("WARD-CENTRAL", 0.0)
    loud = priority_engine.equity_multiplier("WARD-CENTRAL", 500.0)
    assert silent == settings.e_max       # an under-reported ward gets the cap
    assert loud == 1.0                    # a loud one is never penalised below 1
    assert 1.0 <= priority_engine.equity_multiplier("WARD-EAST", 7.0) <= settings.e_max
