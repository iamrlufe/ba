"""Tests for bot/api_client.py against the real FastAPI app via
httpx.ASGITransport (see tests/bot/conftest.py::api_client_factory)."""
from __future__ import annotations

from app.core.auth import hash_password
from app.models.enums import AlertStatus
from bot.api_client import (
    ApiConflict,
    ApiForbidden,
    ApiNotFound,
    ApiUnauthorized,
    ApiValidationError,
)
from tests.conftest import (
    build_alert,
    build_backup_job,
    build_backup_record,
    build_disk,
    build_server,
    build_sql_instance,
    build_user,
    mint_token,
)


async def _job_and_record(session, *, with_sql_instance=True):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    sql_instance = None
    if with_sql_instance:
        sql_instance = build_sql_instance()
        session.add(sql_instance)
        await session.commit()
    job = build_backup_job(
        server.id, disk.id,
        sql_instance_id=sql_instance.id if sql_instance else None,
        verification_method="CHECKSUM" if sql_instance else None,
    )
    session.add(job)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()
    return job, record, sql_instance


async def test_telegram_link_happy_path(api_client_factory, session):
    user = build_user(username="tglink", hashed_password=hash_password("pw-secret-1"))
    session.add(user)
    await session.commit()

    client = api_client_factory(None)
    try:
        resp = await client.telegram_link(username="tglink", password="pw-secret-1", telegram_user_id=42)
    finally:
        await client.aclose()

    assert resp.username == "tglink"
    assert resp.bot_access_token


async def test_telegram_link_wrong_password_raises_unauthorized(api_client_factory, session):
    user = build_user(username="tglink2", hashed_password=hash_password("realpw"))
    session.add(user)
    await session.commit()

    client = api_client_factory(None)
    try:
        try:
            await client.telegram_link(username="tglink2", password="wrongpw", telegram_user_id=43)
            assert False, "expected ApiUnauthorized"
        except ApiUnauthorized:
            pass
    finally:
        await client.aclose()


async def test_telegram_link_conflict_raises_api_conflict(api_client_factory, session):
    user1 = build_user(username="tglink3", hashed_password=hash_password("pw1"))
    user2 = build_user(username="tglink4", hashed_password=hash_password("pw2"))
    session.add_all([user1, user2])
    await session.commit()

    client = api_client_factory(None)
    try:
        await client.telegram_link(username="tglink3", password="pw1", telegram_user_id=777)
        try:
            await client.telegram_link(username="tglink4", password="pw2", telegram_user_id=777)
            assert False, "expected ApiConflict"
        except ApiConflict as exc:
            assert exc.detail
    finally:
        await client.aclose()


async def test_get_daily_summary_happy_path(api_client_factory, admin_user):
    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        summary = await client.get_daily_summary()
    finally:
        await client.aclose()
    assert summary.counts is not None


async def test_list_alerts_happy_path_and_filters(api_client_factory, admin_user, session):
    active = build_alert(status=AlertStatus.ACTIVE)
    resolved = build_alert(status=AlertStatus.RESOLVED)
    session.add_all([active, resolved])
    await session.commit()

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        page = await client.list_alerts(status=AlertStatus.ACTIVE)
    finally:
        await client.aclose()

    ids = {a.id for a in page.items}
    assert active.id in ids
    assert resolved.id not in ids


async def test_list_alerts_without_token_raises_unauthorized(api_client_factory):
    client = api_client_factory(None)
    try:
        try:
            await client.list_alerts()
            assert False, "expected ApiUnauthorized"
        except ApiUnauthorized:
            pass
    finally:
        await client.aclose()


async def test_acknowledge_alert_happy_path_as_admin(api_client_factory, admin_user, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        result = await client.acknowledge_alert(alert.id)
    finally:
        await client.aclose()
    assert result.status.value == "ACKNOWLEDGED"


async def test_acknowledge_alert_forbidden_for_operator(api_client_factory, operator_user, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    token = mint_token(operator_user.id, operator_user.username, operator_user.role)
    client = api_client_factory(token)
    try:
        try:
            await client.acknowledge_alert(alert.id)
            assert False, "expected ApiForbidden"
        except ApiForbidden as exc:
            assert exc.detail
    finally:
        await client.aclose()


async def test_resolve_alert_happy_path(api_client_factory, admin_user, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        result = await client.resolve_alert(alert.id, "fixed via test")
    finally:
        await client.aclose()
    assert result.status.value == "RESOLVED"
    assert result.resolved_note == "fixed via test"


async def test_get_backup_record_happy_path_and_404(api_client_factory, admin_user, session):
    job, record, _sql = await _job_and_record(session)

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        result = await client.get_backup_record(record.id)
        assert result.id == record.id

        try:
            await client.get_backup_record(999999)
            assert False, "expected ApiNotFound"
        except ApiNotFound:
            pass
    finally:
        await client.aclose()


async def test_get_backup_job_happy_path_and_404(api_client_factory, admin_user, session):
    job, _record, _sql = await _job_and_record(session)

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        result = await client.get_backup_job(job.id)
        assert result.id == job.id

        try:
            await client.get_backup_job(999999)
            assert False, "expected ApiNotFound"
        except ApiNotFound:
            pass
    finally:
        await client.aclose()


async def test_create_restore_operation_happy_path_hardcodes_mode_missing(
    api_client_factory, operator_user, session
):
    job, record, sql_instance = await _job_and_record(session)

    token = mint_token(operator_user.id, operator_user.username, operator_user.role)
    client = api_client_factory(token)
    try:
        op = await client.create_restore_operation(
            backup_record_id=record.id,
            sql_instance_id=sql_instance.id,
            database_name="mydb",
            confirmation_database_name="mydb",
        )
    finally:
        await client.aclose()
    assert op.mode.value == "MISSING"
    assert op.database_name == "mydb"


async def test_create_restore_operation_conflict_on_duplicate(api_client_factory, admin_user, session):
    job, record, sql_instance = await _job_and_record(session)

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        await client.create_restore_operation(
            backup_record_id=record.id,
            sql_instance_id=sql_instance.id,
            database_name="dupdb",
            confirmation_database_name="dupdb",
        )
        try:
            await client.create_restore_operation(
                backup_record_id=record.id,
                sql_instance_id=sql_instance.id,
                database_name="dupdb",
                confirmation_database_name="dupdb",
            )
            assert False, "expected ApiConflict"
        except ApiConflict as exc:
            assert exc.detail
    finally:
        await client.aclose()


async def test_create_restore_operation_mismatched_confirmation_is_validation_error(
    api_client_factory, admin_user, session
):
    job, record, sql_instance = await _job_and_record(session)

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        try:
            await client.create_restore_operation(
                backup_record_id=record.id,
                sql_instance_id=sql_instance.id,
                database_name="realdb",
                confirmation_database_name="typo'd-db-name",
            )
            assert False, "expected ApiValidationError"
        except ApiValidationError as exc:
            assert exc.detail
    finally:
        await client.aclose()


async def test_mark_alert_telegram_delivered_happy_path(api_client_factory, admin_user, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    token = mint_token(admin_user.id, admin_user.username, admin_user.role)
    client = api_client_factory(token)
    try:
        result = await client.mark_alert_telegram_delivered(alert.id)
    finally:
        await client.aclose()
    assert result.delivered_telegram_at is not None
