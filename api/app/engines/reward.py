"""Reward and vesting engine (design doc section 5).

This module is the heart of STAR-Net and the part that must be correct.

Three invariants hold everywhere below:

1. **Append-only.** Balances are never stored or mutated. A user's balance is
   the signed sum of their ledger entries. Vesting appends positive entries;
   reversals and redemptions append negative ones.
2. **Idempotent.** Every write derives a deterministic `idempotency_key`. A
   duplicated or retried lifecycle event collides on the unique index and is
   silently dropped, so no event can ever pay twice.
3. **Reward vests on confirmed resolution.** A contribution only reaches PAID
   once an independent user has physically confirmed the repair. A fabricated
   fault can never be resolved, so it collects at most
   `g_verification * f_discoverer * R_I` -- roughly 0.5% of base points with
   the default knobs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.enums import EntryType, Role, Stage, VestingState
from app.models import Contribution, Issue, LedgerEntry, Report, User, utcnow

QUANTUM = Decimal("0.0001")


def _d(value: float | Decimal | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    """Quantise downward. Rounding down never over-allocates a pool.

    Every amount in this module goes through here, so the whole ledger shares
    one rounding convention. Anything comparing against engine output must use
    `quantize_points` rather than Decimal's default ROUND_HALF_EVEN.
    """
    return value.quantize(QUANTUM, rounding=ROUND_DOWN)


# Public alias: the engine's rounding convention, for callers and tests.
quantize_points = _q


# --- role and stage plans --------------------------------------------------

ROLE_FRACTION: dict[Role, Decimal] = {
    Role.DISCOVERER: _d(settings.f_discoverer),
    Role.CORROBORATOR: _d(settings.f_corroborator),
    Role.VERIFIER: _d(settings.f_verifier),
    Role.CONFIRMER: _d(settings.f_confirmer),
}

# Which stages each role vests across, and what fraction of its entitlement
# each stage releases. Discoverers and corroborators are the ones held across
# the full lifecycle; the verifier and fix-confirmer fees pay at the single
# stage where their work happens.
STAGE_PLAN: dict[Role, dict[Stage, Decimal]] = {
    Role.DISCOVERER: {
        Stage.VERIFICATION: _d(settings.g_verification),
        Stage.RESOLUTION: _d(settings.g_resolution),
        Stage.CONFIRMATION: _d(settings.g_confirmation),
    },
    Role.CORROBORATOR: {
        Stage.VERIFICATION: _d(settings.g_verification),
        Stage.RESOLUTION: _d(settings.g_resolution),
        Stage.CONFIRMATION: _d(settings.g_confirmation),
    },
    Role.VERIFIER: {Stage.VERIFICATION: Decimal("1")},
    Role.CONFIRMER: {Stage.CONFIRMATION: Decimal("1")},
}

STAGE_ORDER: list[Stage] = [Stage.VERIFICATION, Stage.RESOLUTION, Stage.CONFIRMATION]

_AWAITING_STATE: dict[Stage, VestingState] = {
    Stage.VERIFICATION: VestingState.PENDING_VERIFICATION,
    Stage.RESOLUTION: VestingState.PENDING_RESOLUTION,
    Stage.CONFIRMATION: VestingState.PENDING_CONFIRMATION,
}


def initial_vesting_state(role: Role, stages_already_passed: Iterable[Stage]) -> VestingState:
    """A contribution created late (a corroborator joining an already-verified
    issue) starts at the next stage it can actually participate in."""
    passed = set(stages_already_passed)
    for stage in STAGE_ORDER:
        if stage in STAGE_PLAN[role] and stage not in passed:
            return _AWAITING_STATE[stage]
    return VestingState.PAID


def _cumulative_fractions(role: Role, stage: Stage) -> tuple[Decimal, Decimal]:
    """Cumulative plan fraction before `stage` and through `stage`.

    Stage amounts are paid as the *difference* between these two quantised
    cumulative values rather than as independently quantised slices. Slicing
    each stage on its own loses a rounding unit per stage, so three stages
    would pay out marginally less than the entitlement and the pool would leak.
    Telescoping the differences makes the stages sum to exactly the
    entitlement, with the last stage absorbing the residual.
    """
    plan = STAGE_PLAN[role]
    before = Decimal("0")
    for candidate in STAGE_ORDER:
        if candidate not in plan:
            continue
        if candidate == stage:
            return before, before + plan[candidate]
        before += plan[candidate]
    raise KeyError(f"{role} does not vest at {stage}")


def _advance_state(contribution: Contribution, fired: Stage) -> VestingState:
    """State after `fired` vests: the next unfired stage in this role's plan."""
    plan = STAGE_PLAN[contribution.role]
    seen_fired = False
    for stage in STAGE_ORDER:
        if stage == fired:
            seen_fired = True
            continue
        if seen_fired and stage in plan:
            return _AWAITING_STATE[stage]
    return VestingState.PAID


# --- R_I -------------------------------------------------------------------


def compute_reward_total(priority: float, equity_multiplier: float, discoverer_trust: float) -> Decimal:
    """R_I = R0 * P(I) * E(l) * tau(discoverer), bounded to [0, R0 * E_max]."""
    r_i = _d(settings.r0_base_points) * _d(priority) * _d(equity_multiplier) * _d(discoverer_trust)
    ceiling = _d(settings.r0_base_points) * _d(settings.e_max)
    return _q(max(Decimal("0"), min(r_i, ceiling)))


# --- pool splitting --------------------------------------------------------


def split_pool(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` across `weights` pro-rata, exactly.

    Each share is quantised down, then the residual left by rounding is handed
    to the largest weight. The returned shares always sum to exactly `total`,
    so a pool can never leak or overpay by a rounding unit.
    """
    if not weights:
        return []
    weight_sum = sum(weights, Decimal("0"))
    if weight_sum <= 0:
        return [Decimal("0")] * len(weights)

    shares = [_q(total * w / weight_sum) for w in weights]
    residual = total - sum(shares, Decimal("0"))
    if residual != 0:
        largest = max(range(len(weights)), key=lambda i: weights[i])
        shares[largest] += residual
    return shares


def _corroborator_weights(db: Session, issue_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    """rho_i * tau_i per corroborator, capped at k_max by descending weight.

    Recomputed at each vesting stage against whoever has corroborated by then,
    so a corroborator who joins late shares the remaining stages rather than
    retroactively diluting what has already vested.
    """
    rows = db.execute(
        select(Contribution, Report.independence_weight, User.trust_a, User.trust_b)
        .join(User, User.id == Contribution.user_id)
        .outerjoin(
            Report,
            (Report.issue_id == Contribution.issue_id) & (Report.reporter_id == Contribution.user_id),
        )
        .where(
            Contribution.issue_id == issue_id,
            Contribution.role == Role.CORROBORATOR,
            Contribution.vesting_state != VestingState.FORFEITED,
        )
    ).all()

    weighted: list[tuple[uuid.UUID, Decimal]] = []
    for contribution, rho, trust_a, trust_b in rows:
        rho_value = _d(rho if rho is not None else 1.0)
        tau = _d(trust_score(trust_a or 0.0, trust_b or 0.0))
        weighted.append((contribution.id, rho_value * tau))

    weighted.sort(key=lambda pair: pair[1], reverse=True)
    return dict(weighted[: settings.max_corroborators])


def trust_score(trust_a: float, trust_b: float) -> float:
    """Beta reputation: tau(u) = (a + 1) / (a + b + 2). Duplicated from the
    trust engine to keep this module importable on its own."""
    return (trust_a + 1.0) / (trust_a + trust_b + 2.0)


# --- ledger primitives -----------------------------------------------------


def _append(
    db: Session,
    *,
    user_id: uuid.UUID,
    issue_id: uuid.UUID | None,
    contribution_id: uuid.UUID | None,
    role: Role | None,
    stage: Stage,
    entry_type: EntryType,
    amount: Decimal,
    idempotency_key: str,
    note: str | None = None,
) -> LedgerEntry | None:
    """Append one ledger entry, or return None if this exact transition has
    already been recorded. The unique index on `idempotency_key` is what makes
    retries safe; we rely on the database, not on a prior read."""
    entry = LedgerEntry(
        user_id=user_id,
        issue_id=issue_id,
        contribution_id=contribution_id,
        role=role,
        stage=stage,
        entry_type=entry_type,
        amount=amount,
        idempotency_key=idempotency_key,
        note=note,
    )
    savepoint = db.begin_nested()
    try:
        db.add(entry)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return None
    return entry


def create_contribution(
    db: Session,
    *,
    issue: Issue,
    user_id: uuid.UUID,
    role: Role,
    stages_already_passed: Iterable[Stage] = (),
) -> Contribution:
    """Create (or fetch) the contribution row for a (issue, user, role).

    `entitlement` is a display estimate only. The authoritative amount for any
    stage is computed when that stage vests -- see `vest_stage`.
    """
    existing = db.execute(
        select(Contribution).where(
            Contribution.issue_id == issue.id,
            Contribution.user_id == user_id,
            Contribution.role == role,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    contribution = Contribution(
        issue_id=issue.id,
        user_id=user_id,
        role=role,
        entitlement=_q(_d(issue.reward_total) * ROLE_FRACTION[role]),
        vesting_state=initial_vesting_state(role, stages_already_passed),
    )
    db.add(contribution)
    db.flush()
    _refresh_corroborator_estimates(db, issue)
    return contribution


def _refresh_corroborator_estimates(db: Session, issue: Issue) -> None:
    """Keep the displayed corroborator entitlements pro-rata. Cosmetic only:
    it touches no ledger entry and no vesting state."""
    weights = _corroborator_weights(db, issue.id)
    if not weights:
        return
    pool = _q(_d(issue.reward_total) * ROLE_FRACTION[Role.CORROBORATOR])
    ids = list(weights)
    shares = split_pool(pool, [weights[i] for i in ids])
    by_id = dict(zip(ids, shares))
    for contribution in db.execute(
        select(Contribution).where(
            Contribution.issue_id == issue.id, Contribution.role == Role.CORROBORATOR
        )
    ).scalars():
        contribution.entitlement = by_id.get(contribution.id, Decimal("0"))


# --- the state machine -----------------------------------------------------


def vest_stage(db: Session, issue: Issue, stage: Stage, *, note: str | None = None) -> list[LedgerEntry]:
    """Fire one vesting stage for every contribution on `issue` awaiting it.

    Idempotent by construction: the key embeds the issue's reopen cycle, so
    replaying the same lifecycle event is a no-op while a genuine
    reopen -> reconfirm cycle vests again under a fresh key.
    """
    cycle = issue.reopen_count
    contributions = list(
        db.execute(select(Contribution).where(Contribution.issue_id == issue.id)).scalars()
    )

    # The corroborator pool is split per stage across exactly those
    # corroborators eligible for it, so the stage's pool is always fully
    # distributed. The per-stage pool itself telescopes, so the stage pools sum
    # to the whole pool with nothing left over.
    #
    # Splitting per stage rather than once up front is deliberate: a
    # corroborator who joins after verification shares the remaining stages
    # without retroactively diluting what has already vested.
    stage_shares: dict[uuid.UUID, Decimal] = {}
    if stage in STAGE_PLAN[Role.CORROBORATOR]:
        eligible = [
            c
            for c in contributions
            if c.role == Role.CORROBORATOR and c.vesting_state == _AWAITING_STATE[stage]
        ]
        if eligible:
            weights = _corroborator_weights(db, issue.id)
            eligible = [c for c in eligible if c.id in weights][: settings.max_corroborators]
        if eligible:
            pool = _q(_d(issue.reward_total) * ROLE_FRACTION[Role.CORROBORATOR])
            cum_before, cum_through = _cumulative_fractions(Role.CORROBORATOR, stage)
            stage_pool = _q(pool * cum_through) - _q(pool * cum_before)
            ids = [c.id for c in eligible]
            stage_shares = dict(zip(ids, split_pool(stage_pool, [weights[i] for i in ids])))

    written: list[LedgerEntry] = []
    for contribution in contributions:
        plan = STAGE_PLAN[contribution.role]
        if stage not in plan:
            continue
        if contribution.vesting_state != _AWAITING_STATE[stage]:
            # Already vested this stage, forfeited, or not yet eligible.
            continue

        if contribution.role == Role.CORROBORATOR:
            amount = stage_shares.get(contribution.id, Decimal("0"))
        else:
            full = _q(_d(issue.reward_total) * ROLE_FRACTION[contribution.role])
            cum_before, cum_through = _cumulative_fractions(contribution.role, stage)
            amount = _q(full * cum_through) - _q(full * cum_before)

        entry = _append(
            db,
            user_id=contribution.user_id,
            issue_id=issue.id,
            contribution_id=contribution.id,
            role=contribution.role,
            stage=stage,
            entry_type=EntryType.VEST,
            amount=amount,
            idempotency_key=f"vest:{contribution.id}:{stage.value}:{cycle}",
            note=note,
        )
        if entry is None:
            continue
        contribution.vesting_state = _advance_state(contribution, stage)
        written.append(entry)

    db.flush()
    return written


def forfeit_unvested(db: Session, issue: Issue, *, reason: str) -> list[LedgerEntry]:
    """Cancel the unvested remainder of every contribution on a rejected or
    abandoned issue.

    Already-vested entries are left untouched: the ledger is append-only, and
    a correct report that reached verification legitimately earned its
    verification slice. The forfeit entry is a zero-amount marker so the audit
    trail records what was cancelled without rewriting history.
    """
    written: list[LedgerEntry] = []
    for contribution in db.execute(
        select(Contribution).where(Contribution.issue_id == issue.id)
    ).scalars():
        if contribution.vesting_state in (VestingState.PAID, VestingState.FORFEITED):
            continue

        remaining = _remaining_fraction(contribution)
        full = _q(_d(issue.reward_total) * ROLE_FRACTION[contribution.role])
        entry = _append(
            db,
            user_id=contribution.user_id,
            issue_id=issue.id,
            contribution_id=contribution.id,
            role=contribution.role,
            stage=Stage.NONE,
            entry_type=EntryType.FORFEIT,
            amount=Decimal("0"),
            idempotency_key=f"forfeit:{contribution.id}:{issue.reopen_count}",
            note=f"{reason} (forfeited {_q(full * remaining)} points)",
        )
        if entry is None:
            continue
        contribution.vesting_state = VestingState.FORFEITED
        written.append(entry)

    db.flush()
    return written


def _remaining_fraction(contribution: Contribution) -> Decimal:
    """Share of this contribution's entitlement that has not yet vested."""
    plan = STAGE_PLAN[contribution.role]
    awaiting = contribution.vesting_state
    remaining = Decimal("0")
    reached = False
    for stage in STAGE_ORDER:
        if stage not in plan:
            continue
        if _AWAITING_STATE[stage] == awaiting:
            reached = True
        if reached:
            remaining += plan[stage]
    return remaining


def reverse_stage(db: Session, issue: Issue, stage: Stage, *, reason: str) -> list[LedgerEntry]:
    """Claw back a vesting stage invalidated by a reopen.

    Used when an issue is reopened after confirmation: the repair did not hold,
    so the confirmation slice was paid against a false signal. We append
    negative entries rather than deleting the originals, and roll the
    contributions back to awaiting that stage so a genuine later confirmation
    can vest them again.
    """
    cycle = issue.reopen_count
    written: list[LedgerEntry] = []

    originals = list(
        db.execute(
            select(LedgerEntry).where(
                LedgerEntry.issue_id == issue.id,
                LedgerEntry.stage == stage,
                LedgerEntry.entry_type == EntryType.VEST,
                LedgerEntry.idempotency_key.like(f"vest:%:{stage.value}:{cycle}"),
            )
        ).scalars()
    )

    for original in originals:
        entry = _append(
            db,
            user_id=original.user_id,
            issue_id=issue.id,
            contribution_id=original.contribution_id,
            role=original.role,
            stage=stage,
            entry_type=EntryType.REVERSAL,
            amount=-original.amount,
            idempotency_key=f"reversal:{original.contribution_id}:{stage.value}:{cycle}",
            note=reason,
        )
        if entry is None:
            continue
        written.append(entry)

        contribution = db.get(Contribution, original.contribution_id)
        if contribution is not None and contribution.vesting_state != VestingState.FORFEITED:
            contribution.vesting_state = _AWAITING_STATE[stage]

    db.flush()
    return written


# --- balances --------------------------------------------------------------


def balance(db: Session, user_id: uuid.UUID) -> Decimal:
    """Signed sum of every ledger entry. The only definition of a balance."""
    total = db.execute(
        select(LedgerEntry.amount).where(LedgerEntry.user_id == user_id)
    ).scalars().all()
    return _q(sum(total, Decimal("0")))


def pending_by_state(db: Session, user_id: uuid.UUID) -> dict[str, Decimal]:
    """What the user is still owed, grouped by vesting state -- the 'pending'
    side of the wallet that makes the vesting mechanism visible."""
    rows = db.execute(
        select(Contribution, Issue.reward_total)
        .join(Issue, Issue.id == Contribution.issue_id)
        .where(Contribution.user_id == user_id)
    ).all()

    out: dict[str, Decimal] = {}
    for contribution, reward_total in rows:
        if contribution.vesting_state in (VestingState.PAID, VestingState.FORFEITED):
            continue
        full = _q(_d(reward_total) * ROLE_FRACTION[contribution.role])
        amount = _q(full * _remaining_fraction(contribution))
        key = contribution.vesting_state.value
        out[key] = out.get(key, Decimal("0")) + amount
    return out


def record_redemption(
    db: Session, *, user_id: uuid.UUID, sku: str, name: str, cost: Decimal, nonce: str
) -> LedgerEntry | None:
    """Spend points. Negative ledger entry; `nonce` keeps a double-tapped
    redeem button from charging twice."""
    return _append(
        db,
        user_id=user_id,
        issue_id=None,
        contribution_id=None,
        role=None,
        stage=Stage.NONE,
        entry_type=EntryType.REDEMPTION,
        amount=-_q(cost),
        idempotency_key=f"redeem:{user_id}:{sku}:{nonce}",
        note=f"Redeemed {name}",
    )
