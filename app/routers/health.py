"""GET /healthz -- trivial liveness endpoint.

Deliberately has zero DB/auth coupling (no Depends(get_db), no auth
dependency of any kind) so it stays usable as a Docker HEALTHCHECK target
even if the database is unreachable -- migration failures already fail the
container hard at the docker-entrypoint.sh step, before uvicorn ever binds.
"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
