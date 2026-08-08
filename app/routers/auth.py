from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, get_current_user, hash_password, verify_password
from app.core.config import settings
from app.core.db import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse

router = APIRouter(tags=["auth"])

_INVALID_CREDENTIALS_DETAIL = "Invalid username or password"

# Precomputed hash with no matching password, used to pay the same bcrypt
# cost on a not-found username as on a wrong password -- otherwise a
# not-found user short-circuits before verify_password() runs, making
# "unknown username" measurably faster than "wrong password" and letting an
# attacker enumerate valid usernames via response timing alone.
_DUMMY_HASH = hash_password("not-a-real-password-used-only-for-timing")


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)) -> LoginResponse:
    stmt = select(User).where(User.username == payload.username)
    user = (await session.execute(stmt)).scalar_one_or_none()

    password_ok = verify_password(payload.password, user.hashed_password if user else _DUMMY_HASH)

    # Same generic 401 for not-found, wrong-password, and inactive-user --
    # never leak which case applies.
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS_DETAIL)

    token = create_access_token(user_id=user.id, username=user.username, role=user.role)
    return LoginResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=MeResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
