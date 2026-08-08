"""In-memory WebSocket connection manager for live JobRun progress.

Single-process only (no cross-process pub/sub) -- acceptable for this
deployment, but note that horizontally scaling the API would require
replacing this with a shared broker.
"""
import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class JobRunConnectionManager:
    """Tracks WebSocket connections subscribed to a given job_run_id."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, job_run_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(job_run_id, set()).add(websocket)

    async def disconnect(self, job_run_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(job_run_id)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(job_run_id, None)

    async def broadcast(self, job_run_id: int, payload: dict) -> None:
        async with self._lock:
            sockets = set(self._connections.get(job_run_id, ()))

        dead: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                logger.debug("Dropping dead websocket for job_run_id=%s", job_run_id, exc_info=True)
                dead.append(websocket)

        if dead:
            async with self._lock:
                live = self._connections.get(job_run_id)
                if live is not None:
                    for websocket in dead:
                        live.discard(websocket)
                    if not live:
                        self._connections.pop(job_run_id, None)

    async def close_all(self, job_run_id: int, code: int, reason: str) -> None:
        async with self._lock:
            sockets = self._connections.pop(job_run_id, set())

        for websocket in sockets:
            try:
                await websocket.close(code=code, reason=reason)
            except Exception:
                logger.debug("Error closing websocket for job_run_id=%s", job_run_id, exc_info=True)


# Module-level singleton -- routers import this directly rather than going
# through `request.app.state` (see app/main.py for the app.state alias,
# kept only for testability).
manager = JobRunConnectionManager()
