"""Telegram bot for Backup Orchestrator.

A separate process from `app/` (the FastAPI service) -- it talks to the API
exclusively over HTTP via `bot.api_client.OrchestratorApiClient`, using a
per-user bot-scoped JWT obtained via `POST /api/auth/telegram-link`. Never
imports `app.core.config`, `app.core.security`, `app.core.auth`,
`app.core.db`, any `app.models.*` ORM class, or any `app.routers.*` -- see
`bot/crypto.py`'s module docstring for why, and `bot/api_client.py` for the
list of `app.schemas.*` response models it IS safe to import.
"""
