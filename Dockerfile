# Build context MUST be the repo root (docker build -f Dockerfile . / compose
# build.context: .), NOT a subdirectory — this Dockerfile does
# `COPY app ./app` and `COPY alembic ./alembic`, both repo-root-relative
# paths, exactly like bot/Dockerfile's COPY app ./app + COPY bot ./bot.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

# Fixed UID/GID (10001, not 1000 -- 1000 frequently collides with a real
# interactive host user) so the data dir's ownership is reproducible across
# rebuilds and so a Docker named volume's first-run population (which
# copies the mount point's existing in-image content/ownership) inherits
# correct permissions with no runtime chown needed.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /srv/app/data \
    && chown -R appuser:appuser /srv/app /srv/app/data

WORKDIR /srv/app

# appuser is created with --no-create-home, so $HOME has no writable
# default (uv's cache dir defaults to $HOME/.cache/uv, which would fail
# with "Permission denied" at runtime otherwise). Point HOME at WORKDIR,
# which the final chown below makes appuser-owned.
ENV HOME=/srv/app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Everything copied/built above (.venv, app, alembic, uv cache under
# $HOME/.cache) was written as root -- reassign it to appuser now that all
# root-only steps are done, so the entrypoint (running as appuser) can
# actually read the venv and write uv's cache at runtime.
RUN chown -R appuser:appuser /srv/app

ENV PYTHONUNBUFFERED=1

# /healthz is unauthenticated and does not touch the DB (see
# app/routers/health.py) -- migration failure at entrypoint already fails
# the container hard before uvicorn binds, so this only needs to prove the
# ASGI event loop is alive and serving.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
