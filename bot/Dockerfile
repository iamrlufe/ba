FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group bot
COPY app ./app
COPY bot ./bot
ENV PYTHONUNBUFFERED=1
# No secrets baked in -- TELEGRAM_BOT_TOKEN / FERNET_KEY / etc are supplied
# at runtime via bot/.env (mounted) or environment variables.
# No HEALTHCHECK in v1 -- TODO: add one once bot/main.py exposes some
# liveness signal (e.g. a small internal socket/file heartbeat); a bot
# process using long-polling has no HTTP port to probe by default.
# alembic/ is intentionally NOT copied -- the bot never runs migrations.
ENTRYPOINT ["uv", "run", "python", "-m", "bot.main"]
