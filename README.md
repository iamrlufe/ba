
## Deployment

### Требования

- Docker Engine
- Docker Compose v2 (плагин `docker compose`, НЕ legacy standalone `docker-compose` v1) — 
  требуется для long-form синтаксиса `depends_on: condition: service_healthy` в 
  `docker-compose.yml`.

### Первый деплой — чеклист

1. Генерация секретов — у каждой обязательной переменной в `.env.example` уже есть 
   собственный `python -c ...` one-liner в комментарии прямо над ней; отдельно 
   дублировать их здесь не нужно.
2. `cp .env.example .env` и `cp bot/.env.example bot/.env`, заполнить обязательные 
   значения (важно: `FERNET_KEY` должен совпадать между backend `.env` и `bot/.env` — 
   см. `bot/config.py`).
3. `docker compose build`
4. `docker compose up -d`
5. Миграции применяются автоматически при старте контейнера (см. `docker-entrypoint.sh`) — 
   ничего руками запускать не нужно; проверить: `docker compose logs backend | grep alembic` 
   должен показать `Running upgrade ... -> ..., <head revision>` без ошибок.
6. Smoke-check: `curl http://localhost:8000/healthz` → `{"status":"ok"}`; 
   `docker compose ps` должен показывать backend как `healthy`.

> `bot/.env.example` по умолчанию рассчитан на деплой не через compose 
> (`BOT_API_BASE_URL=http://localhost:8000`, относительный `BOT_STATE_DB_PATH=./bot_state.db`). 
> Под compose это не нужно править руками — `docker-compose.yml` уже переопределяет оба 
> значения через `environment:` у сервиса `bot` (`BOT_API_BASE_URL=http://backend:8000`, 
> `BOT_STATE_DB_PATH=/srv/app/data/bot_state.db`, внутрь смонтированного `bot_data`-volume). 
> Это актуально только при запуске `bot/main.py` НЕ через `docker-compose.yml` (например, 
> напрямую на хосте) — тогда те же два значения нужно выставить в `bot/.env` вручную.

### Build context — обязательно корень репозитория

И `Dockerfile` (backend), и `bot/Dockerfile` собираются ТОЛЬКО с контекстом = корень 
репозитория (уже так настроено в `docker-compose.yml` через `build.context: .`); при 
ручной сборке вне compose — `docker build -f Dockerfile .` из корня, не изнутри 
поддиректории.

### Хранение данных

`backend_data` (`/srv/app/data`, `orchestrator.db`) и `bot_data` (`/srv/app/data`, 
`bot_state.db`) — именованные volumes; удаление volume — деструктивная операция 
(теряется история backup-job'ов/алертов или связки Telegram-пользователей 
соответственно), `docker compose down` НЕ удаляет volumes по умолчанию, только 
`docker compose down -v`.

### Как применить изменения .env — шпаргалка

```
# docker compose restart НЕ перечитывает .env -- переменные окружения
# фиксируются при создании контейнера. После правки .env или bot/.env:
docker compose up -d --force-recreate backend
docker compose up -d --force-recreate bot
# (или сразу оба: docker compose up -d --force-recreate)
```

### Откат миграций

Entrypoint выполняет только `alembic upgrade head` автоматически; откат — ручной: 
`docker compose exec backend uv run alembic downgrade <rev>`.

### Frontend — сборка и порт

- Frontend теперь тоже собирается и поднимается через тот же `docker compose build` / 
  `docker compose up -d`, отдельных шагов не требуется.
- `frontend` слушает `8090` на хосте (nginx внутри контейнера — на `80`), проксирует 
  `/api/` и `/ws/` на `backend:8000` внутри compose-сети.
- Build context для `frontend` — `./frontend`, НЕ корень репозитория (в отличие от 
  backend/bot) — фронтенд не зависит от кода вне своей директории.

### VITE_* переменные — другая модель, чем backend .env

- `VITE_API_BASE_URL` (и любые другие `VITE_*`) вшиваются в JS-бандл **во время 
  `vite build`** (`import.meta.env.VITE_*` статически заменяется esbuild/rollup) — это 
  НЕ читается заново при старте контейнера, в отличие от backend/bot, где 
  pydantic-settings/python-dotenv читает `.env` живьём при старте процесса.
- `docker compose up -d --force-recreate backend` пересоздаёт контейнер и подхватывает 
  новый `.env` — этого **достаточно** для backend/bot. Для frontend этого 
  **недостаточно**: смена `VITE_API_BASE_URL` требует пересборки образа:
  ```
  docker compose build --no-cache frontend
  docker compose up -d frontend
  ```
- В текущей топологии деплоя (frontend и backend за одним reverse-proxy origin) 
  `VITE_API_BASE_URL=/api` всегда корректен и обычно не требует изменения — этот раздел 
  нужен в основном чтобы будущий оператор не тратил время на `--force-recreate`, 
  недоумевая почему ничего не изменилось.

### Frontend + Nginx Proxy Manager

NPM в этом деплое — отдельный/внешний инстанс (НЕ в той же Docker-сети, что и этот 
compose-стек) — подтверждено пользователем:

- В NPM Proxy Host должен указывать на **`<ip-сервера>:8090`** (host-опубликованный 
  порт), НЕ на внутреннее compose service DNS-имя (`frontend`) — оно недоступно извне 
  этой compose-сети при внешнем NPM.
- NPM должен проксировать на **этот frontend-контейнер** (который сам проксирует `/api` 
  и `/ws` на `backend`), а не напрямую на `backend` — та же логика, что уже была в 
  dev-прокси (`vite.config.ts`'s `/api`+`/ws` proxy), применённая теперь к 
  продакшн-nginx-контейнеру внутри compose.
- **Websocket Support обязательно включить** в NPM UI для этого Proxy Host — иначе NPM 
  сам не прокинет `Upgrade`/`Connection`-заголовки дальше, и апгрейд до WebSocket 
  оборвётся ещё на первом хопе (браузер → NPM), даже если `frontend/nginx.conf` 
  настроен правильно. Два хопа апгрейда (NPM → frontend-nginx → backend) должны оба 
  сохранять эти заголовки — внутри compose это уже обеспечено, но NPM-сторону нужно 
  включить вручную в UI, это не настраивается через файлы этого репозитория.
- TLS terminates at NPM — `frontend/nginx.conf` intentionally has no cert/key handling.

## C#/.NET агент — эксплуатационные заметки

### Деплой на Windows-сервер

Self-contained single-file publish (не требует установки .NET на целевом сервере):
```bash
dotnet publish agent/Worker -r win-x64 --self-contained -p:PublishSingleFile=true -c Release
```

Конфигурация — `appsettings.Production.json` рядом с exe (в .gitignore) либо 
через переменные окружения: `Agent__ServerId`, `Agent__AgentKey`, 
`Agent__ConnectionConfigKey` и т.д.

Регистрация как Windows Service — через `sc.exe create` (стандартный путь для 
`UseWindowsService()`-приложений, NSSM не нужен).

Проверять сначала на ОДНОМ тестовом сервере, только после успешного пилота — 
раскатывать на остальные.

### КРИТИЧНО — connection-config endpoint не готов для боевых credentials

`GET /api/agents/{server_id}/connection-config` — первый в проекте эндпоинт, 
отдающий расшифрованные (plaintext) FTP/SFTP-credentials. Защищён отдельным 
`CONNECTION_CONFIG_API_KEY`, аудируется в таблице `agent_credential_access_logs` 
(читается через admin-only `GET /api/agents/credential-access-log`), 403 для 
DISABLED-сервера.

**BLOCKER: не использовать с реальными продакшн-паролями, пока не реализованы 
per-server ключи** (сейчас один общий ключ открывает credentials всех серверов 
разом). До реализации per-server ключей — доступ к этому эндпоинту должен быть 
ограничен только внутренним VPN-периметром (WireGuard/Netbird), не открыт 
наружу ни при каких обстоятельствах.

### Windows Server 2008 R2

.NET 8 не поддерживается. Для пары серверов на 2008 R2 — отдельный облегчённый 
агент (PowerShell или .NET Framework 4.8) — отдельная будущая задача, ещё не 
начата.

### Overlap-policy планировщика

Skip-and-log: если предыдущий прогон задачи ещё выполняется к моменту 
следующего cron-тика — тик пропускается с Warning-логом, не ставится в очередь. 
Backend независимо детектит просрочку через `JOB_MISSED`/`missed_run_grace_minutes`.

### Offline-очередь

SQLite (не LiteDB) — `complete`/`backup-records` события гарантированно 
переживают падение процесса и недоступность backend, доставляются при 
восстановлении связи через транзакционный `DELETE WHERE id=...` после 
подтверждённой отправки. `heartbeat`/progress-patch можно терять без очереди.

## Открытые TODO (порядок приоритета)

1. Per-server agent keys — заменить единый `AGENT_API_KEY`/`CONNECTION_CONFIG_API_KEY` 
   на уникальные ключи по серверам (blocker для боевого использования connection-config)
2. Python-чекер FTP copy-integrity — сама реализация (backend-контракт уже готов)
3. Legacy-агент для Windows Server 2008 R2
4. Короткоживущий WS-тикет вместо `?token=` в query string для WebSocket-подключений
5. `bot/.env.example`: `BOT_STATE_DB_PATH`/`BOT_API_BASE_URL` по умолчанию рассчитаны на 
   деплой не через compose (тот же класс бага, что был у backend `DATABASE_URL` — 
   относительный путь теряется при пересоздании контейнера). Под `docker-compose.yml` 
   это уже закрыто через `environment:`-оверрайд у сервиса `bot` (см. секцию Deployment), 
   но сам `bot/.env.example` не исправлен (вне рамок задачи) — прямой (не через compose) 
   запуск бота на хосте по-прежнему требует ручной правки `bot/.env`.
