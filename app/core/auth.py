"""Password hashing, JWT issuance/verification, and the FastAPI auth
dependencies (get_current_user, require_role, require_admin_or_agent_key)
used by every router.

Single-access-token scheme: no refresh tokens, no server-side revocation
list. A leaked token remains valid until JWT_EXPIRE_MINUTES elapses, even
if the user is deactivated in the interim -- deactivation IS checked live
on every request via get_current_user re-fetching the User row, so
deactivation takes effect immediately; only "this specific still-valid
token" cannot be individually revoked short of that.
"""
from datetime import UTC, datetime, timedelta
import secrets as _secrets

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.enums import UserRole
from app.models.user import User

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(*, user_id: int, username: str, role: UserRole) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_bot_access_token(*, user_id: int, username: str, role: UserRole) -> str:
    """Mint a long-lived JWT for the Telegram bot process (see
    POST /api/auth/telegram-link). Deliberately a separate function from
    create_access_token (not a shared function with an optional
    expires_delta param) so the normal login path stays structurally
    incapable of accidentally issuing a long-lived token.

    decode_access_token/get_current_user need ZERO changes -- a
    bot-scoped JWT authenticates exactly like a normal one. The "scope"
    claim is included for forward-compatible metadata only; it is not
    enforced or read anywhere in this pass.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role.value,
        "scope": "telegram_bot",
        "iat": now,
        "exp": now + timedelta(minutes=settings.BOT_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError (or subclasses) on any invalid/expired token."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: UserRole):
    async def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(r.value for r in roles)}",
            )
        return current_user
    return _dependency


async def require_admin_or_agent_key(
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User | None:
    """Allow either a valid X-Agent-Key header (agent/scheduler traffic)
    or an authenticated admin JWT (manual/admin override, e.g. testing).
    Returns the User if authenticated via JWT, else None (agent-key path
    has no User principal). Endpoints using this dependency generally
    don't need the return value -- wire it via `dependencies=[Depends(...)]`
    unless a handler specifically needs to branch on human-vs-agent.
    """
    if x_agent_key is not None:
        if not _secrets.compare_digest(x_agent_key, settings.AGENT_API_KEY):
            raise HTTPException(status_code=401, detail="Invalid agent API key")
        return None
    user = await get_current_user(credentials=credentials, session=session)
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Requires the X-Agent-Key header or an admin JWT",
        )
    return user
