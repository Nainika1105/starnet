from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, hash_password, issue_token, verify_password
from app.db import get_db
from app.engines.trust import tier_of, trust_of
from app.models import User
from app.schemas import LoginIn, MeOut, SignupIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupIn, db: Session = Depends(get_db)) -> TokenOut:
    clash = db.execute(
        select(User).where((User.email == payload.email) | (User.handle == payload.handle))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or handle already registered")

    user = User(
        email=payload.email,
        handle=payload.handle,
        password_hash=hash_password(payload.password),
        home_ward=payload.home_ward,
    )
    db.add(user)
    db.commit()
    return TokenOut(access_token=issue_token(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials")
    return TokenOut(access_token=issue_token(user))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        handle=user.handle,
        is_admin=user.is_admin,
        tier=tier_of(user),
        home_ward=user.home_ward,
        # Exposed only to admins; citizens see the tier.
        trust_score=round(trust_of(user), 4) if user.is_admin else None,
    )
