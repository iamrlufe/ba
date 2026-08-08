"""Thin async httpx wrapper around the Backup Orchestrator HTTP API.

Every typed method parses the response into the same `app.schemas.*` model
the API itself returns, or raises one of the `ApiClientError` subtypes
below. `app.models.enums` and Pydantic response schemas under
`app.schemas.*` are safe to import here -- none of them transitively
import `app.core.config` (see bot/__init__.py's module docstring). NEVER
import `app.core.config`, `app.core.security`, `app.core.auth`,
`app.core.db`, any `app.models.*` ORM class, or any `app.routers.*`.

NEVER log the `Authorization` header or its value anywhere in this module.

One `OrchestratorApiClient` instance per invocation/per-chat's token --
never a shared client with a mutated bearer token. See
`bot/handlers/_common.py::get_api_client` for how callers build one.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.models.enums import AlertStatus
from app.schemas.alert import AlertRead
from app.schemas.auth import TelegramLinkResponse
from app.schemas.backup_job import BackupJobRead
from app.schemas.backup_record import BackupRecordRead
from app.schemas.common import PaginatedResponse
from app.schemas.restore_operation import RestoreOperationRead
from app.schemas.summary import DailySummary


class ApiClientError(Exception):
    """Base class for all OrchestratorApiClient errors."""


class ApiUnauthorized(ApiClientError):
    """401 -- missing/expired/invalid bearer token."""


class ApiForbidden(ApiClientError):
    """403 -- authenticated but not permitted (e.g. role check)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ApiNotFound(ApiClientError):
    """404."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ApiConflict(ApiClientError):
    """409."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ApiValidationError(ApiClientError):
    """422."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ApiServerError(ApiClientError):
    """5xx."""


class ApiUnavailable(ApiClientError):
    """Network-level failure (connection refused, timeout, DNS, ...) --
    raised from `httpx.RequestError`, never reached the server at all."""


def _extract_detail(response: httpx.Response) -> str:
    try:
        data: Any = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        return detail if isinstance(detail, str) else str(detail)
    return response.text


class OrchestratorApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        # transport=None means real network (build a normal httpx.AsyncClient);
        # a non-None transport is the test-injection point, e.g.
        # httpx.ASGITransport(app=fastapi_app) in tests.
        if transport is not None:
            self._client = httpx.AsyncClient(
                base_url=base_url, timeout=timeout, headers=headers, transport=transport
            )
        else:
            self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OrchestratorApiClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise ApiUnavailable(str(exc)) from exc

        status_code = response.status_code
        if status_code == 401:
            raise ApiUnauthorized(_extract_detail(response))
        if status_code == 403:
            raise ApiForbidden(_extract_detail(response))
        if status_code == 404:
            raise ApiNotFound(_extract_detail(response))
        if status_code == 409:
            raise ApiConflict(_extract_detail(response))
        if status_code == 422:
            raise ApiValidationError(_extract_detail(response))
        if status_code >= 500:
            raise ApiServerError(_extract_detail(response))
        if not (200 <= status_code < 300):
            raise ApiClientError(_extract_detail(response))
        return response

    async def telegram_link(
        self, *, username: str, password: str, telegram_user_id: int
    ) -> TelegramLinkResponse:
        response = await self._request(
            "POST",
            "/api/auth/telegram-link",
            json={
                "username": username,
                "password": password,
                "telegram_user_id": telegram_user_id,
            },
        )
        return TelegramLinkResponse.model_validate(response.json())

    async def get_daily_summary(self) -> DailySummary:
        response = await self._request("GET", "/api/summary/daily")
        return DailySummary.model_validate(response.json())

    async def list_alerts(
        self, *, status: AlertStatus | None = None, limit: int = 50, offset: int = 0
    ) -> PaginatedResponse[AlertRead]:
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value
        response = await self._request("GET", "/api/alerts", params=params)
        return PaginatedResponse[AlertRead].model_validate(response.json())

    async def acknowledge_alert(self, alert_id: int) -> AlertRead:
        response = await self._request("POST", f"/api/alerts/{alert_id}/acknowledge", json={})
        return AlertRead.model_validate(response.json())

    async def resolve_alert(self, alert_id: int, note: str | None = None) -> AlertRead:
        response = await self._request(
            "POST", f"/api/alerts/{alert_id}/resolve", json={"resolved_note": note}
        )
        return AlertRead.model_validate(response.json())

    async def get_backup_record(self, backup_record_id: int) -> BackupRecordRead:
        response = await self._request("GET", f"/api/backup-records/{backup_record_id}")
        return BackupRecordRead.model_validate(response.json())

    async def get_backup_job(self, backup_job_id: int) -> BackupJobRead:
        response = await self._request("GET", f"/api/backup-jobs/{backup_job_id}")
        return BackupJobRead.model_validate(response.json())

    async def create_restore_operation(
        self,
        *,
        backup_record_id: int,
        sql_instance_id: int,
        database_name: str,
        confirmation_database_name: str,
    ) -> RestoreOperationRead:
        # mode is HARDCODED to "MISSING" here -- never a caller-supplied
        # parameter (see app/schemas/restore_operation.py::RestoreOperationCreate
        # and app/routers/restore_operations.py's OPERATOR-role restriction,
        # which only permits mode=MISSING for non-admin requesters -- the bot
        # never authenticates as anything more privileged than the linked
        # user's own role).
        response = await self._request(
            "POST",
            "/api/restore-operations",
            json={
                "backup_record_id": backup_record_id,
                "sql_instance_id": sql_instance_id,
                "database_name": database_name,
                "mode": "MISSING",
                "confirmation_database_name": confirmation_database_name,
            },
        )
        return RestoreOperationRead.model_validate(response.json())

    async def mark_alert_telegram_delivered(self, alert_id: int) -> AlertRead:
        response = await self._request(
            "POST", f"/api/alerts/{alert_id}/mark-telegram-delivered", json={}
        )
        return AlertRead.model_validate(response.json())
