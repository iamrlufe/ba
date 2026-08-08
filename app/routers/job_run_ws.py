import jwt
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_access_token
from app.core.db import get_db
from app.core.ws_manager import manager
from app.models.enums import JOB_RUN_TERMINAL_STATUSES
from app.models.job_run import JobRun
from app.models.user import User
from app.schemas.job_run import JobRunRead

router = APIRouter(tags=["job-run-ws"])


@router.websocket("/ws/job-runs/{job_run_id}")
async def job_run_ws(
    job_run_id: int,
    websocket: WebSocket,
    token: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> None:
    await websocket.accept()

    if token is None:
        await websocket.close(code=4401, reason="missing token")
        return
    try:
        payload = decode_access_token(token)
        user = await session.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        user = None
    if user is None or not user.is_active:
        await websocket.close(code=4401, reason="invalid or expired token")
        return

    # Register with the manager *before* reading status: if a concurrent
    # POST .../complete lands between accept() and here, its broadcast +
    # close_all would otherwise be sent to a socket that isn't subscribed
    # yet, and this connection -- now stuck reading an already-terminal
    # status -- would never receive a future update (there won't be one).
    # Connecting first guarantees we either see the pre-completion status
    # below and enter the live loop normally (in which case the completion
    # reaches us via the loop), or we see the post-completion status below
    # and take the terminal branch -- there's no window where we see
    # neither.
    await manager.connect(job_run_id, websocket)

    run = await session.get(JobRun, job_run_id)

    if run is None:
        await manager.disconnect(job_run_id, websocket)
        await websocket.send_json({"error": "job_run_not_found"})
        await websocket.close(code=4404, reason="job_run_not_found")
        return

    current_state = JobRunRead.model_validate(run).model_dump(mode="json")

    if run.status in JOB_RUN_TERMINAL_STATUSES:
        await manager.disconnect(job_run_id, websocket)
        try:
            # If a concurrent completion's close_all() already fired for
            # this job_run_id, the socket is already closed -- these calls
            # then raise, which is expected and harmless.
            await websocket.send_json(current_state)
            await websocket.close(
                code=1000, reason="job run already finished; no further updates will be sent"
            )
        except Exception:
            pass
        return

    try:
        await websocket.send_json(current_state)
        while True:
            # Channel is send-only from the server's perspective; incoming
            # messages are ignored, this call just detects disconnects.
            await websocket.receive()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(job_run_id, websocket)
