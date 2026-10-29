from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.catalog import BY_SKU, CATALOG
from app.db import get_db
from app.engines import reward
from app.engines.trust import tier_of
from app.models import Contribution, Issue, LedgerEntry, Redemption, User
from app.schemas import (
    CatalogItem,
    ContributionOut,
    LedgerEntryOut,
    RedeemIn,
    RedeemOut,
    WalletOut,
)

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/me", response_model=WalletOut)
def my_wallet(user: User = Depends(current_user), db: Session = Depends(get_db)) -> WalletOut:
    """The whole point of the wallet is that pending and paid are visibly
    different: the user can watch a reward move through the vesting stages."""
    entries = db.execute(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.created_at.desc())
    ).scalars().all()

    rows = db.execute(
        select(Contribution, Issue)
        .join(Issue, Issue.id == Contribution.issue_id)
        .where(Contribution.user_id == user.id)
        .order_by(Contribution.created_at.desc())
    ).all()

    vested_by_contribution: dict = {}
    for entry in entries:
        if entry.contribution_id is None:
            continue
        vested_by_contribution[entry.contribution_id] = (
            vested_by_contribution.get(entry.contribution_id, 0) + entry.amount
        )

    return WalletOut(
        balance=reward.balance(db, user.id),
        pending_by_state=reward.pending_by_state(db, user.id),
        contributions=[
            ContributionOut(
                issue_id=c.issue_id,
                category=i.category,
                issue_status=i.status,
                role=c.role,
                entitlement=c.entitlement,
                vesting_state=c.vesting_state,
                vested_so_far=vested_by_contribution.get(c.id, 0),
            )
            for c, i in rows
        ],
        ledger=[
            LedgerEntryOut(
                id=e.id,
                issue_id=e.issue_id,
                role=e.role,
                stage=e.stage,
                entry_type=e.entry_type,
                amount=e.amount,
                note=e.note,
                created_at=e.created_at,
            )
            for e in entries
        ],
        tier=tier_of(user),
    )


@router.get("/catalog", response_model=list[CatalogItem])
def catalog() -> list[CatalogItem]:
    return CATALOG


@router.post("/redeem", response_model=RedeemOut)
def redeem(
    payload: RedeemIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> RedeemOut:
    item = BY_SKU.get(payload.sku)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such catalogue item")

    available = reward.balance(db, user.id)
    if available < item.cost:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Need {item.cost} points, you have {available}",
        )

    entry = reward.record_redemption(
        db, user_id=user.id, sku=item.sku, name=item.name, cost=item.cost, nonce=payload.nonce
    )
    if entry is None:
        # Same nonce replayed: report the existing state rather than charging twice.
        return RedeemOut(
            ok=True,
            balance=reward.balance(db, user.id),
            message="Already redeemed (duplicate request ignored).",
        )

    db.add(
        Redemption(
            user_id=user.id, item_sku=item.sku, item_name=item.name, points_spent=item.cost
        )
    )
    db.commit()
    return RedeemOut(
        ok=True, balance=reward.balance(db, user.id), message=f"Redeemed {item.name}."
    )
