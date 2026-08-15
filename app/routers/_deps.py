"""Small shared helpers used across routers.

Not a router itself -- no endpoints are defined here.

Pagination convention (no dedicated dependency, just applied consistently
in each list endpoint): `limit: int = Query(50, ge=1, le=200)`,
`offset: int = Query(0, ge=0)`, results ordered by `id` descending.
"""
from typing import Any, Sequence, TypeVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


async def get_or_404(
    session: AsyncSession,
    model: type[ModelT],
    obj_id: int,
    options: Sequence[Any] | None = None,
) -> ModelT:
    obj = await session.get(model, obj_id, options=options or ())
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found")
    return obj
