from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password, require_role
from app.core.db import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.user import UserCreate, UserRead

router = APIRouter(tags=["users"])


@router.post(
    "",
    response_model=UserRead,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_db)) -> User:
    # No pre-check SELECT for username uniqueness -- relies on the global
    # IntegrityError -> 409 handler, matching the established pattern
    # elsewhere in this codebase (see uq_users_username).
    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get(
    "",
    response_model=PaginatedResponse[UserRead],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[UserRead]:
    total_stmt = select(func.count()).select_from(User)
    items_stmt = select(User).order_by(User.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[UserRead](
        items=[UserRead.model_validate(u) for u in items], total=total, limit=limit, offset=offset
    )
