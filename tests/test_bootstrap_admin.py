"""Coverage for `app.main._bootstrap_admin()` -- the startup step that
optionally creates a bootstrap ADMIN `User` from `BOOTSTRAP_ADMIN_USERNAME`/
`BOOTSTRAP_ADMIN_PASSWORD`.

This function is never called via the ASGI app/lifespan in these tests (the
`client` fixture's `ASGITransport` doesn't trigger `lifespan`, and even if
it did, `app.main` reads `async_session_maker`/`engine` from
`app.core.db` at module level, which points at the real file-backed
`DATABASE_URL` from `.env`). Instead we call `app.main._bootstrap_admin()`
directly and monkeypatch `app.main.async_session_maker` (bound to the
in-memory `session_maker` fixture's engine) and the relevant
`app.main.settings.BOOTSTRAP_ADMIN_*` attributes, so nothing here ever
touches the real dev DB file.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as RealAsyncSession

import app.main as main_module
from app.core.auth import hash_password, verify_password
from app.models.enums import UserRole
from app.models.user import User


async def test_bootstrap_admin_both_unset_is_noop(monkeypatch, session_maker):
    monkeypatch.setattr(main_module, "async_session_maker", session_maker)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_USERNAME", None)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_PASSWORD", None)

    await main_module._bootstrap_admin()

    async with session_maker() as s:
        result = await s.execute(select(User))
        assert result.scalars().all() == []


@pytest.mark.parametrize(
    "username,password",
    [("only-username", None), (None, "only-password")],
)
async def test_bootstrap_admin_exactly_one_set_raises(monkeypatch, session_maker, username, password):
    monkeypatch.setattr(main_module, "async_session_maker", session_maker)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_USERNAME", username)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_PASSWORD", password)

    with pytest.raises(RuntimeError):
        await main_module._bootstrap_admin()

    async with session_maker() as s:
        result = await s.execute(select(User))
        assert result.scalars().all() == []


async def test_bootstrap_admin_creates_user_when_both_set_and_none_exists(monkeypatch, session_maker):
    monkeypatch.setattr(main_module, "async_session_maker", session_maker)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_USERNAME", "bootstrap-admin")
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-password-1")

    await main_module._bootstrap_admin()

    async with session_maker() as s:
        result = await s.execute(select(User).where(User.username == "bootstrap-admin"))
        user = result.scalar_one()

    assert user.role == UserRole.ADMIN
    assert user.is_active is True
    assert user.hashed_password != "bootstrap-password-1"
    assert verify_password("bootstrap-password-1", user.hashed_password)


async def test_bootstrap_admin_existing_user_is_untouched(monkeypatch, session_maker):
    monkeypatch.setattr(main_module, "async_session_maker", session_maker)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_USERNAME", "existing-admin")
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_PASSWORD", "brand-new-password")

    original_hash = hash_password("original-password")
    async with session_maker() as s:
        s.add(
            User(
                username="existing-admin",
                hashed_password=original_hash,
                role=UserRole.OPERATOR,  # deliberately not ADMIN, to prove role is untouched too
                is_active=True,
            )
        )
        await s.commit()

    async with session_maker() as s:
        result = await s.execute(select(User).where(User.username == "existing-admin"))
        before = result.scalar_one()
        before_id, before_hash, before_role, before_active, before_created_at = (
            before.id,
            before.hashed_password,
            before.role,
            before.is_active,
            before.created_at,
        )

    await main_module._bootstrap_admin()

    async with session_maker() as s:
        result = await s.execute(select(User).where(User.username == "existing-admin"))
        rows = result.scalars().all()

    # No-op: exactly the same single row, byte-identical on every column that
    # matters (bootstrap must never overwrite an existing user's password or role).
    assert len(rows) == 1
    after = rows[0]
    assert after.id == before_id
    assert after.hashed_password == before_hash
    assert after.role == before_role
    assert after.is_active == before_active
    assert after.created_at == before_created_at
    # The new password must NOT have taken effect.
    assert not verify_password("brand-new-password", after.hashed_password)
    assert verify_password("original-password", after.hashed_password)


async def test_bootstrap_admin_concurrent_creation_race_is_caught(monkeypatch, session_maker):
    """Reproduces the exact race called out in the spec/reviewer fix: two
    processes both pass the check-then-insert's SELECT (neither sees an
    existing row), then both attempt to commit an INSERT for the same
    username. The loser's commit must raise IntegrityError (real UNIQUE
    constraint violation, not a mocked exception), and `_bootstrap_admin`
    must catch it and roll back instead of propagating / crashing startup.

    Implemented by monkeypatching `AsyncSession.commit` so that, on the
    *first* call (this call belongs to `_bootstrap_admin`'s own session,
    right after its SELECT already ran and found nothing), a concurrent
    "worker" session is used to insert-and-commit the same username first --
    genuinely winning the race -- before control returns to let the real
    commit() proceed and hit the UNIQUE constraint.
    """
    monkeypatch.setattr(main_module, "async_session_maker", session_maker)
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_USERNAME", "race-admin")
    monkeypatch.setattr(main_module.settings, "BOOTSTRAP_ADMIN_PASSWORD", "race-password-1")

    real_commit = RealAsyncSession.commit
    state = {"triggered": False}

    async def _commit_with_injected_race(self):
        if not state["triggered"]:
            state["triggered"] = True
            async with session_maker() as other:
                other.add(
                    User(
                        username="race-admin",
                        hashed_password=hash_password("concurrent-winner-password"),
                        role=UserRole.ADMIN,
                        is_active=True,
                    )
                )
                await other.commit()
        await real_commit(self)

    monkeypatch.setattr(RealAsyncSession, "commit", _commit_with_injected_race)

    # Must not raise -- the IntegrityError from the loser's commit is caught
    # and rolled back inside _bootstrap_admin.
    await main_module._bootstrap_admin()

    async with session_maker() as s:
        result = await s.execute(select(User).where(User.username == "race-admin"))
        rows = result.scalars().all()

    # Exactly one row survives: the concurrent winner's. The loser's insert
    # was rolled back, not silently retried/duplicated.
    assert len(rows) == 1
    assert verify_password("concurrent-winner-password", rows[0].hashed_password)
    assert not verify_password("race-password-1", rows[0].hashed_password)
