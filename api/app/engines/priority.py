"""Prioritisation and equity (design doc section 7).

P(I) = sum(lambda_i * x_i), each x_i in [0,1], lambdas sum to 1.

Deliberately NOT learned from resolution time: training on how fast the city
actually fixed things would teach the model to deprioritise neglected wards,
which is the opposite of what the equity multiplier is for.
"""
from __future__ import annotations

import math

from app.config import settings
from app.enums import Category
from app.engines.geo import WARDS

# --- x_i inputs ------------------------------------------------------------
# The four base rates are STUBs: per-category and per-ward constants standing in
# for real signals (severity from a trained detector, exposure from traffic
# counts, vulnerability from a POI lookup for schools and hospitals, escalation
# from category physics and weather). The escalation component is *not* a
# constant -- it grows with the age of the open issue, which is computed.

_SEVERITY: dict[Category, float] = {
    Category.POTHOLE: 0.70,
    Category.STREETLIGHT: 0.55,
    Category.WATER_LEAK: 0.85,
    Category.GARBAGE: 0.45,
    Category.TREE: 0.75,
    Category.FOOTPATH: 0.50,
}

# How fast the fault gets worse if ignored.
_ESCALATION: dict[Category, float] = {
    Category.POTHOLE: 0.75,     # monsoon turns a pothole into a crater
    Category.STREETLIGHT: 0.30,
    Category.WATER_LEAK: 0.90,  # undermines the road and wastes water
    Category.GARBAGE: 0.60,     # public-health compounding
    Category.TREE: 0.70,
    Category.FOOTPATH: 0.35,
}

_EXPOSURE: dict[str, float] = {
    "WARD-NORTH": 0.65,
    "WARD-CENTRAL": 0.90,
    "WARD-SOUTH": 0.55,
    "WARD-EAST": 0.60,
    "WARD-WEST": 0.50,
    "WARD-UNASSIGNED": 0.40,
}

_VULNERABILITY: dict[str, float] = {
    "WARD-NORTH": 0.50,
    "WARD-CENTRAL": 0.70,
    "WARD-SOUTH": 0.60,
    "WARD-EAST": 0.45,
    "WARD-WEST": 0.40,
    "WARD-UNASSIGNED": 0.30,
}

# Expected report rate per ward, rho_hat(l). STUB: a population proxy. Wards
# that *should* generate reports but do not are under-reported, not fault-free.
_EXPECTED_RATE: dict[str, float] = {
    "WARD-NORTH": 12.0,
    "WARD-CENTRAL": 20.0,
    "WARD-SOUTH": 14.0,
    "WARD-EAST": 10.0,
    "WARD-WEST": 16.0,
    "WARD-UNASSIGNED": 8.0,
}

EPSILON_RATE = 1.0


def escalation_component(category: Category, age_hours: float = 0.0) -> float:
    """x_escalation, which is the one input that moves on its own.

    A fault that has been open for a month is worse than the same fault
    yesterday, and worse by more if it is the kind that compounds. So the
    category's escalation rate is scaled from `escalation_floor` of itself at
    filing up toward the full rate, saturating exponentially:

        x = e_cat * (floor + (1 - floor) * (1 - exp(-age / tau)))

    This is what stops an issue from sitting at the same rank forever while
    newer, flashier reports overtake it -- the queue ages items up by itself,
    with no human having to notice.

    It moves the RANKING only. R_I was computed from the creation-time priority
    and is frozen, so an issue climbing the queue never changes what anyone was
    promised for it.
    """
    rate = _ESCALATION.get(category, 0.5)
    floor = settings.escalation_floor
    tau = max(1e-9, settings.escalation_tau_hours)
    growth = 1.0 - math.exp(-max(0.0, age_hours) / tau)
    return rate * (floor + (1.0 - floor) * growth)


def components(category: Category, ward: str, age_hours: float = 0.0) -> dict[str, float]:
    return {
        "severity": _SEVERITY.get(category, 0.5),
        "exposure": _EXPOSURE.get(ward, 0.4),
        "vulnerability": _VULNERABILITY.get(ward, 0.3),
        "escalation": escalation_component(category, age_hours),
    }


def priority(category: Category, ward: str, age_hours: float = 0.0) -> float:
    x = components(category, ward, age_hours)
    p = (
        settings.lambda_severity * x["severity"]
        + settings.lambda_exposure * x["exposure"]
        + settings.lambda_vulnerability * x["vulnerability"]
        + settings.lambda_escalation * x["escalation"]
    )
    return max(0.0, min(1.0, p))


def equity_multiplier(ward: str, observed_rate: float) -> float:
    """E(l) = clip( rho_hat(l) / max(r_obs(l), eps), 1, E_max ).

    Under-reported wards get their rewards multiplied up, so a neighbourhood
    that is quiet because nobody reports does not stay starved of attention.
    Bounded above by E_max and floored at 1 so it can only ever help.
    """
    expected = _EXPECTED_RATE.get(ward, 8.0)
    ratio = expected / max(observed_rate, EPSILON_RATE)
    return max(1.0, min(settings.e_max, ratio))


def expected_rate(ward: str) -> float:
    return _EXPECTED_RATE.get(ward, 8.0)


def all_wards() -> list[str]:
    return list(WARDS) + ["WARD-UNASSIGNED"]
