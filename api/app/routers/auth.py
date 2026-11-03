from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import (
    DUMMY_PASSWORD_HASH,
    current_user,
    hash_password,
    issue_token,
    verify_password,
)
from app.db import get_db
from app.engines.trust import tier_of, trust_of
from app.models import User
from app.schemas import LoginIn, MeOut, SignupIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupIn, request: Request, db: Session = Depends(get_db)
) -> TokenOut:
    ip_key = ratelimit.guard_signup(request)
    clash = db.execute(
        select(User).where((User.email == payload.email) | (User.handle == payload.handle))
    ).scalar_one_or_none()
    if clash is not None:
        # This does tell a caller that an address is registered, which is a
        # mild enumeration oracle -- kept because the alternative is a sign-up
        # form that cannot explain why it failed. The rate limit above is what
        # stops it being enumerated at scale.
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or handle already registered")

    user = User(
        email=payload.email,
        handle=payload.handle,
        password_hash=hash_password(payload.password),
        home_ward=payload.home_ward,
    )
    db.add(user)
    db.commit()
    ratelimit.signup_by_ip.record(ip_key)
    return TokenOut(access_token=issue_token(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    ip_key, account_key = ratelimit.guard_login(request, payload.email)

    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()

    # The hash comparison runs whether or not the account exists. Skipping it
    # for an unknown address would return in microseconds instead of the ~100ms
    # bcrypt takes, and that difference is a reliable way to ask the API which
    # addresses are registered.
    hashed = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, hashed)

    if user is None or not password_ok:
        ratelimit.record_failed_login(ip_key, account_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad credentials")

    ratelimit.clear_login(ip_key, account_key)
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
