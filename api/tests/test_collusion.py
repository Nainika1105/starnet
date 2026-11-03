"""The collusion graph, and the ring it exists to stop.

Every per-issue independence rule passes for a pair taking turns across many
issues: A is not B, neither reported what the other settled, and both keep
their tier because confirming is what raises trust. The pairing is only visible
in aggregate. These tests build the ring and assert the system refuses it,
then build the honest patterns that superficially resemble it and assert the
system does not.
"""
from __future__ import annotations

import pytest

from app import lifecycle
from app.config import settings
from app.engines import collusion, routing
from app.engines.confidence import independence_weight
from app.enums import Category, IssueStatus, Role, Tier, Verdict
from app.models import Contribution, Issue, Report, utcnow

ELIGIBLE = dict(trust_a=12, trust_b=0)


# --- the metric ------------------------------------------------------------


def _contribute(db, user, issue, role) -> None:
    db.add(
        Contribution(
            issue_id=issue.id, user_id=user.id, role=role, created_at=utcnow()
        )
    )
    db.flush()


def _issue(db, discoverer, *, lat=12.9719, lng=77.5937) -> Issue:
    from app.engines.geo import ward_for, wkt_point

    issue = Issue(
        category=Category.POTHOLE,
        status=IssueStatus.CONFIRMED,
        location=wkt_point(lat, lng),
        lat=lat,
        lng=lng,
        ward=ward_for(lat, lng),
        reward_total=0,
        discoverer_id=discoverer.id,
    )
    db.add(issue)
    db.flush()
    return issue


def _ring(db, a, b, *, rounds: int) -> list[Issue]:
    """A reports, B confirms; B reports, A confirms; repeat."""
    issues = []
    for n in range(rounds):
        claimer, settler = (a, b) if n % 2 == 0 else (b, a)
        issue = _issue(db, claimer, lat=12.9719 + n * 0.001)
        _contribute(db, claimer, issue, Role.DISCOVERER)
        _contribute(db, settler, issue, Role.CONFIRMER)
        issues.append(issue)
    return issues


def test_strangers_score_zero(db, make_user):
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    assert collusion.pair_history(db, a.id, b.id).suspicion() == 0.0


def test_a_couple_of_shared_issues_is_a_street_not_a_conspiracy(db, make_user):
    """Below the shared-issue floor the score stays zero however exclusive it
    looks, because a measure that fires on coincidence is worse than none."""
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    _ring(db, a, b, rounds=settings.collusion_min_shared_issues - 1)

    history = collusion.pair_history(db, a.id, b.id)
    assert history.affinity == 1.0, "they have only ever worked together"
    assert history.suspicion() == 0.0, "but it is too little history to act on"


def test_a_take_turns_ring_is_caught(db, make_user):
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    _ring(db, a, b, rounds=6)

    history = collusion.pair_history(db, a.id, b.id)
    assert history.shared_issues == 6
    assert history.reciprocity == 3, "three swaps in each direction"
    assert history.is_reciprocal
    assert history.suspicion() > settings.collusion_routing_veto


def test_an_exclusive_pairing_is_flagged_even_without_role_swapping(db, make_user):
    """A confirmer who only ever signs off one reporter's repairs, and nobody
    else's, is releasing that reporter's 45% on nobody's word but their own.
    Whether they also swap roles is immaterial -- the money still moves on an
    unchecked pairing, so this scores as high as a ring.
    """
    reporter = make_user("reporter", **ELIGIBLE)
    confirmer = make_user("confirmer", **ELIGIBLE)

    for n in range(6):
        issue = _issue(db, reporter, lat=12.9719 + n * 0.001)
        _contribute(db, reporter, issue, Role.DISCOVERER)
        _contribute(db, confirmer, issue, Role.CONFIRMER)

    history = collusion.pair_history(db, reporter.id, confirmer.id)
    assert history.reciprocity == 0, "no swapping"
    assert not history.is_reciprocal
    assert history.affinity == 1.0, "but they work with nobody else"
    assert history.suspicion() > settings.collusion_routing_veto


def test_role_swapping_discriminates_at_moderate_affinity(db, make_user):
    """Where reciprocity earns its keep. Two pairs with identical overlap, one
    swapping claim and settle roles and one not: the swapping pair scores
    higher, because taking turns is the signature of a ring rather than of a
    working relationship.
    """
    # Pair one: four shared issues out of six each, swapping roles.
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    _ring(db, a, b, rounds=4)
    _dilute(db, make_user, a, count=2, lat=12.9300)
    _dilute(db, make_user, b, count=2, lat=12.9350)

    # Pair two: the same shape, but b2 only ever settles for a2.
    a2, b2 = make_user("a2", **ELIGIBLE), make_user("b2", **ELIGIBLE)
    for n in range(4):
        issue = _issue(db, a2, lat=12.9400 + n * 0.001)
        _contribute(db, a2, issue, Role.DISCOVERER)
        _contribute(db, b2, issue, Role.CONFIRMER)
    _dilute(db, make_user, a2, count=2, lat=12.9450)
    _dilute(db, make_user, b2, count=2, lat=12.9500)

    swapping = collusion.pair_history(db, a.id, b.id)
    one_way = collusion.pair_history(db, a2.id, b2.id)

    assert swapping.affinity == pytest.approx(one_way.affinity), "same overlap"
    assert swapping.is_reciprocal and not one_way.is_reciprocal
    assert swapping.suspicion() > one_way.suspicion()
    assert one_way.suspicion() > 0, "still entangled, just less damning"


def _dilute(db, make_user, user, *, count: int, lat: float) -> None:
    """Give a user unrelated history, each issue with a different partner."""
    for n in range(count):
        partner = make_user(f"dil{lat}{n}", **ELIGIBLE)
        issue = _issue(db, user, lat=lat + n * 0.001)
        _contribute(db, user, issue, Role.DISCOVERER)
        _contribute(db, partner, issue, Role.CONFIRMER)


def test_affinity_is_measured_against_the_less_active_account(db, make_user):
    """A new account whose every contribution is alongside one established
    user is the suspicious party; dividing by the established user's large
    total would hide exactly that."""
    prolific = make_user("prolific", **ELIGIBLE)
    newcomer = make_user("newcomer", **ELIGIBLE)
    others = [make_user(f"other{n}", **ELIGIBLE) for n in range(10)]

    # The prolific user works with everybody.
    for n, other in enumerate(others):
        issue = _issue(db, prolific, lat=12.9719 + n * 0.002)
        _contribute(db, prolific, issue, Role.DISCOVERER)
        _contribute(db, other, issue, Role.CONFIRMER)

    # The newcomer only ever works with the prolific one -- enough times to
    # clear the activity floor, so the ratio is about them and not about noise.
    _ring(db, prolific, newcomer, rounds=settings.collusion_min_activity + 1)

    history = collusion.pair_history(db, prolific.id, newcomer.id)
    assert history.activity_a > history.activity_b
    assert history.affinity == 1.0, "all of the newcomer's work is with them"
    assert history.suspicion() > 0


def test_an_account_working_widely_is_not_flagged(db, make_user):
    """The honest prolific volunteer: many partners, no concentration."""
    busy = make_user("busy", **ELIGIBLE)
    partners = [make_user(f"p{n}", **ELIGIBLE) for n in range(8)]

    for n, partner in enumerate(partners):
        issue = _issue(db, partner, lat=12.9719 + n * 0.002)
        _contribute(db, partner, issue, Role.DISCOVERER)
        _contribute(db, busy, issue, Role.CONFIRMER)

    worst, score = collusion.worst_pair(db, busy.id, {p.id for p in partners})
    assert score == 0.0, f"flagged against {worst} with no concentration"


def test_suspicion_against_takes_the_worst_partner(db, make_user):
    """Being clean with nine people does not make an exclusive partnership
    with the tenth acceptable -- and it is the tenth who would be paid."""
    actor = make_user("actor", **ELIGIBLE)
    partner = make_user("partner", **ELIGIBLE)
    innocents = [make_user(f"i{n}", **ELIGIBLE) for n in range(5)]

    _ring(db, actor, partner, rounds=6)
    for n, innocent in enumerate(innocents):
        issue = _issue(db, innocent, lat=12.9900 + n * 0.002)
        _contribute(db, innocent, issue, Role.DISCOVERER)
        _contribute(db, actor, issue, Role.CONFIRMER)

    everyone = {partner.id} | {i.id for i in innocents}
    assert collusion.suspicion_against(db, actor.id, everyone) > 0
    worst, _ = collusion.worst_pair(db, actor.id, everyone)
    assert worst == partner.id


def test_a_user_is_never_entangled_with_themselves(db, make_user):
    a = make_user("a", **ELIGIBLE)
    assert collusion.pair_history(db, a.id, a.id).suspicion() == 0.0
    assert collusion.suspicion_against(db, a.id, {a.id}) == 0.0


# --- the routing veto, which is the actual fix ----------------------------


def test_a_partner_is_refused_the_confirmation(db, make_user, make_issue):
    """The point of the whole module. The confirmation slice is 45% of the
    pool, so the person who releases it must not be the discoverer's partner.
    """
    discoverer = make_user("discoverer", **ELIGIBLE)
    partner = make_user("partner", **ELIGIBLE)
    _ring(db, discoverer, partner, rounds=6)

    issue = make_issue(discoverer)

    # The partner is the only eligible user in the system, and is still refused.
    assert routing.select_confirmer(db, issue) is None

    stranger = make_user("stranger", **ELIGIBLE)
    picked = routing.select_confirmer(db, issue)
    assert picked is not None and picked.user.id == stranger.id


def test_the_veto_is_not_relaxed_by_the_trusted_fallback(db, make_user, make_issue):
    """Dropping the tier to find somebody is reasonable. Handing the release to
    the discoverer's partner is the attack, at any tier."""
    discoverer = make_user("discoverer", **ELIGIBLE)
    partner = make_user("partner", trust_a=2, trust_b=0)  # TRUSTED, not eligible
    _ring(db, discoverer, partner, rounds=6)

    issue = make_issue(discoverer)
    assert routing.select_confirmer(db, issue) is None


def test_a_clean_candidate_is_preferred_over_an_entangled_one(db, make_user, make_issue):
    """Below the veto, entanglement is a ranking penalty rather than a refusal."""
    discoverer = make_user("discoverer", **ELIGIBLE)
    issue = make_issue(discoverer)

    entangled = make_user("entangled", **ELIGIBLE)
    clean = make_user("clean", trust_a=3, trust_b=0)

    # Five issues shared with the discoverer, one-directional so reciprocity
    # does not amplify, plus unrelated history on both sides -- enough to clear
    # the activity floor and land the affinity between the threshold and the
    # veto, which is the band where entanglement ranks rather than refuses.
    for n in range(5):
        other = _issue(db, discoverer, lat=12.9800 + n * 0.001)
        _contribute(db, discoverer, other, Role.DISCOVERER)
        _contribute(db, entangled, other, Role.CONFIRMER)
    _dilute(db, make_user, discoverer, count=5, lat=12.9650)
    _dilute(db, make_user, entangled, count=3, lat=12.9600)
    _dilute(db, make_user, clean, count=6, lat=12.9550)

    pool = {c.user.id: c for c in routing.candidates(
        db, issue, tiers=(Tier.VERIFIER_ELIGIBLE, Tier.TRUSTED)
    )}
    assert entangled.id in pool, "scored, not vetoed -- this is the ranking band"
    assert clean.id in pool

    # Compared directly rather than by position, so unrelated users created by
    # the dilution cannot move the result.
    assert 0 < pool[entangled.id].collusion < settings.collusion_routing_veto
    assert pool[clean.id].collusion == 0.0
    assert pool[clean.id].sort_key < pool[entangled.id].sort_key, (
        "a clean candidate must outrank an entangled one even when the "
        "entangled one is nearer or more trusted"
    )


def test_collusion_checking_can_be_turned_off_for_cost(db, make_user, make_issue):
    """The graph query is per candidate; the flag exists so a caller that does
    not need it does not pay for it."""
    discoverer = make_user("discoverer", **ELIGIBLE)
    partner = make_user("partner", **ELIGIBLE)
    _ring(db, discoverer, partner, rounds=6)
    issue = make_issue(discoverer)

    unchecked = routing.candidates(
        db, issue, tiers=(Tier.VERIFIER_ELIGIBLE,), check_collusion=False
    )
    assert [c.user.id for c in unchecked] == [partner.id]
    assert all(c.collusion == 0.0 for c in unchecked)


# --- the independence weight ----------------------------------------------


def test_independence_weight_is_graded_not_binary():
    """A pair sharing two thirds of their history should count for less than a
    stranger without counting for nothing."""
    stranger = independence_weight()
    partial = independence_weight(flagged_collusion=0.6)
    total = independence_weight(flagged_collusion=1.0)

    assert stranger == 1.0
    assert settings.rho_min < partial < stranger
    assert total == settings.rho_min


def test_independent_suspicions_compound():
    """A shared device AND a reciprocal history is worse than either, with no
    rule needed to say so."""
    device_only = independence_weight(shared_device=0.5)
    pair_only = independence_weight(reciprocal_pair=0.5)
    both = independence_weight(shared_device=0.5, reciprocal_pair=0.5)

    assert both < device_only == pair_only


def test_independence_weight_never_goes_below_the_floor():
    """A discounted corroboration is still evidence: the mechanism withholds
    money on confirmation and need not also pretend the report never happened.
    """
    assert independence_weight(
        shared_device=1.0, flagged_collusion=1.0, reciprocal_pair=1.0, temporal_burst=1.0
    ) == settings.rho_min


def test_booleans_still_work():
    """Callers predating the graded form pass True/False."""
    assert independence_weight(shared_device=True) == settings.rho_min
    assert independence_weight(shared_device=False) == 1.0


@pytest.mark.parametrize("bad", [-5.0, 2.0])
def test_out_of_range_signals_are_clamped(bad):
    assert settings.rho_min <= independence_weight(flagged_collusion=bad) <= 1.0


def test_early_users_are_not_flagged_for_being_early(db, make_user):
    """In a small or new deployment there is hardly anybody else to have worked
    with, so every pair looks exclusive. A measure that fires on that punishes
    the first users of the system for the crime of being first.
    """
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    _ring(db, a, b, rounds=settings.collusion_min_activity - 1)

    history = collusion.pair_history(db, a.id, b.id)
    assert history.shared_issues >= settings.collusion_min_shared_issues
    assert history.affinity == 1.0
    assert history.suspicion() == 0.0, "too little history for the ratio to mean anything"


def test_the_ring_is_caught_once_there_is_enough_history(db, make_user):
    """The other side of that floor: it delays detection, it does not prevent
    it. A ring only pays off at volume, and volume is what trips this."""
    a, b = make_user("a", **ELIGIBLE), make_user("b", **ELIGIBLE)
    _ring(db, a, b, rounds=settings.collusion_min_activity)

    assert collusion.pair_history(db, a.id, b.id).suspicion() > 0
