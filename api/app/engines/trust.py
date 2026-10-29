"""Trust engine (design doc section 8).

One score per user drives everything: how much a report is believed, who gets
asked to verify, and how much a reward is worth.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.config import settings
from app.enums import Tier
from app.models import User


def beta_trust(trust_a: float, trust_b: float) -> float:
    """tau(u) = (a + 1) / (a + b + 2).

    The +1/+2 is a Beta(1,1) prior: a brand-new user sits at exactly 0.5 and
    carries little weight, so neither a fresh honest account nor a fresh sybil
    can move an issue on its own.
    """
    return (trust_a + 1.0) / (trust_a + trust_b + 2.0)


def decayed_counts(user: User, *, now: datetime | None = None) -> tuple[float, float]:
    """Geometric decay of evidence, ~0.95 per week by default.

    Trust is never locked in: a user who was reliable two years ago and has
    gone quiet drifts back toward the prior, and so does a user who gamed their
    way up. Pure function -- `apply_decay` is what persists it.
    """
    now = now or datetime.now(timezone.utc)
    anchor = user.trust_decayed_at or user.created_at or now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    weeks = max(0.0, (now - anchor).total_seconds() / (7 * 24 * 3600))
    factor = settings.trust_decay_per_week ** weeks
    return user.trust_a * factor, user.trust_b * factor


def apply_decay(user: User, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    user.trust_a, user.trust_b = decayed_counts(user, now=now)
    user.trust_decayed_at = now


def trust_of(user: User) -> float:
    a, b = decayed_counts(user)
    return beta_trust(a, b)


def tier_of(user: User) -> Tier:
    """Tiers are what the user sees -- the raw score stays internal, so there
    is no number to reverse-engineer and farm."""
    tau = trust_of(user)
    if tau >= settings.tier_verifier_min:
        return Tier.VERIFIER_ELIGIBLE
    if tau >= settings.tier_trusted_min:
        return Tier.TRUSTED
    return Tier.NEW


def can_verify(user: User) -> bool:
    return tier_of(user) == Tier.VERIFIER_ELIGIBLE


def reward_correct(user: User, weight: float = 1.0) -> None:
    """A contribution was physically confirmed correct."""
    apply_decay(user)
    user.trust_a += weight


def penalise_incorrect(user: User, weight: float = 1.0) -> None:
    """A contribution was confirmed incorrect -- including a fix-confirmer who
    is later contradicted by a reopen."""
    apply_decay(user)
    user.trust_b += weight
