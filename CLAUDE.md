# Backup Orchestrator — контекст проекта

## Архитектура
- Backend: Python/FastAPI, SQLAlchemy 2.0 async, SQLite, Docker на Ubuntu
- Агент: C#/.NET 8 (self-contained single-file, win-x64), WinSCP-интеграция, `agent/` (Core/Worker/Tests)
- Веб-UI: React/TypeScript/Vite, `frontend/` — существенно продвинут (страницы, тесты, Dockerfile/nginx,
  деплой через тот же `docker compose`), см. "Статус модулей"
- Telegram-бот: отдельный процесс, python-telegram-bot, mandatory chat_id allowlist

## Ключевые архитектурные решения (не пересматривать без явной причины)
- Auth: JWT для людей (admin/operator роли), X-Agent-Key shared-secret для агентов/scheduler
- operator может создавать restore-операции только с mode=MISSING
- ALL/EXISTING restore — только admin
- .bak-файлы хранятся ЛОКАЛЬНО на сервере с SQL Server; FTP — только страховочная копия
- RESTORE VERIFYONLY выполняется через msdb.dbo.backupmediafamily.physical_device_name, 
  НЕ через сканирование директорий (xp_dirtree избегаем — требует sysadmin)
- Alert — polymorphic через nullable FK-колонки (не entity_type+entity_id), 
  создание только через create_alert(), никогда напрямую
- Atomic conditional UPDATE (WHERE status == expected) вместо read-then-write 
  везде, где меняется терминальный статус — защита от race conditions
- Partial unique indexes — не более одного PENDING/RUNNING run на job, не более 
  одного ACTIVE алерта того же типа на сущность

## ⚠️ ВНИМАНИЕ: GET /api/agents/{server_id}/connection-config — НЕ для продакшн-секретов
Этот эндпоинт отдаёт РАСШИФРОВАННЫЕ FTP/SFTP-credentials агенту и защищён
ОДНИМ глобальным shared-secret'ом (`CONNECTION_CONFIG_API_KEY`, заголовок
`X-Connection-Config-Key`, см. `app/core/config.py` / `app/core/auth.py::require_connection_config_key`)
— без per-server scoping/ротации. НЕЛЬЗЯ использовать с реальными продакшн-
credentials, пока не реализованы per-server agent keys (запланировано как
самая первая задача сразу после C#-агента). До этого момента:
- Эндпоинт ДОЛЖЕН быть доступен только внутри существующего VPN-периметра
  (WireGuard/Netbird) и НИКОГДА не должен быть выставлен в публичный
  интернет — это настраивается на уровне сети (nginx/firewall) при деплое,
  вне этого кодабазы.
- Каждый вызов аудируется в таблице `agent_credential_access_logs`
  (`app/models/agent_credential_access_log.py`), включая неуспешные попытки
  — см. `GET /api/agents/credential-access-log` (admin-only).
- `CONNECTION_CONFIG_API_KEY` — отдельный секрет от `AGENT_API_KEY`,
  никогда не переиспользуется.

## Пайплайн разработки
Используем субагентов: architect (только спецификация, Read/Grep/Glob) → 
coder (реализация) → reviewer (только чтение, security-чеклист) → test-runner 
(пишет и реально гоняет pytest). Файлы в .claude/agents/.

Перед коммитом — обязательная проверка на чистом git worktree (не полагаться 
на uncommitted файлы в рабочей копии).

## Статус модулей
- ✅ Models/migrations/schemas
- ✅ Routers + WebSocket
- ✅ Auth (JWT + agent key)
- ✅ Background worker (missed runs, offline, timeout, daily summary)
- ✅ Telegram bot
- ✅ SQL verification (local path via msdb)
- ⏳ FTP copy-integrity checker — backend-контракт готов (`Alert.backup_record_id` 6th FK),
  сам Python-чекер ещё не реализован (см. README "Открытые TODO" #2)
- ✅ Веб-UI (`frontend/`) — React/TypeScript/Vite, страницы: Dashboard, Servers (list+detail),
  SQL Instances, Jobs (list/form/detail), Run detail, Restore, Alerts, Login/NotAuthorized/NotFound;
  auth-обвязка, API-клиент (`src/api/endpoints`), тесты (Vitest) на большинство страниц; собирается
  и деплоится через тот же `docker compose build`/`up` (Dockerfile + nginx reverse-proxy на `/api`,
  `/ws`, порт `8090`) — см. README "Deployment" → "Frontend — сборка и порт". Не превентивно
  считать законченным — не проверялось на предмет полноты покрытия функциональности backend'а
  (например, отображение STUCK/WATCH-специфичных полей в UI не аудировалось).
- ✅ C#-агент (`agent/`, Core/Worker/Tests) — WinSCP-интеграция, offline-очередь на SQLite
  (переживает падение процесса, `complete`/`backup-records` доставляются гарантированно,
  heartbeat/progress можно терять), `dotnet build` чист; WinSCP/live-hosting путь не проверен
  вне Windows. Ключевые фичи агента:
  - Trigger modes: CRON (schedule_cron) и WATCH (watch_directory, msdb-priority file-ready
    детекция для SQL-инстансов, взаимоисключим с schedule_cron/source_path — см.
    `app/schemas/backup_job.py`); copy windows и single-candidate-slot overlap-policy
    (skip-and-log при ещё выполняющемся предыдущем прогоне — backend независимо детектит
    просрочку через `JOB_MISSED`).
  - WinSCP resume/skip: `TransferPlanCalculator` (`agent/src/.../Core/Transfer/`) решает
    Resume/Skip/(полный transfer) на основании существующего частичного файла на destination.
  - Extended monitoring: CPU/memory/службы (per-server override списка отслеживаемых Windows-
    служб — `Server.monitored_service_names`)/top-processes — `app/models/server_metrics.py`,
    `app/schemas/server_metrics.py`, приём через `app/routers/agents.py`.
  - JobRun cancel (`POST /{job_run_id}/cancel`, admin-only, PENDING/RUNNING) с
    `cancel_acknowledged_at` — агент подтверждает получение отмены отдельным вызовом.
  - `JobRunStatus.STUCK` — для PENDING-прогонов, зависших дольше
    `BackupJob.pending_to_running_grace_minutes`; соответствующий `AlertType.JOB_STUCK_PENDING`
    детектится alert worker'ом (`app/workers/alert_worker.py`), не самим агентом.
  - Offline-replay throttling + backend concurrency limit (2788b4c) — троттлинг backlog при
    восстановлении связи агента с backend после разрыва, чтобы не забить бэкенд всплеском
    очереди сразу при reconnect.
  - Socket-churn диагностика (issue #1, закрыт, 44ca50d) — причиной client-side
    `SocketException 10054` + server-side teardown-race `RuntimeError` был дефолтный uvicorn
    `--timeout-keep-alive 5s`, короче polling-cadence агента (scheduler tick 15s / job-poll 30s /
    heartbeat 60s); исправлено выставлением `--timeout-keep-alive 90` в `docker-entrypoint.sh` +
    добавлена диагностика socket-состояния и тюнинг пулинга на стороне агента. Покрыто только
    статическим тестом на наличие флага (`tests/test_docker_entrypoint.py`, не гоняет реальный
    uvicorn-процесс) — ручная проверка на живом трафике агента с реальными idle-паузами >5s
    всё ещё не проводилась; считать закрытие issue основанным на анализе причины, а не на
    воспроизведённой-и-подтверждённой фиксации на живом трафике.
- ✅ Deployment-инфраструктура (`Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `.dockerignore`,
  `GET /healthz`, теперь и `frontend/Dockerfile`+`nginx.conf`) — см. README "Deployment". Реально
  проверено только продакшн-деплоем на сервере grafana и локальным `docker build`/`docker run`
  при разработке этих файлов (миграции + `/healthz` + non-root-запуск подтверждены вручную),
  НЕ покрыто CI/тестами — считать это фактом инфраструктуры, а не автоматически проверяемым
  инвариантом в будущих итерациях.

## Открытые TODO (см. README "Открытые TODO" за полным списком с порядком приоритета)
1. Per-server agent keys (blocker для боевого использования connection-config endpoint)
2. Python-чекер FTP copy-integrity — backend-контракт готов, сама реализация — нет
3. Legacy-агент для Windows Server 2008 R2 (.NET 8 не поддерживается)
4. Короткоживущий WS-тикет вместо `?token=` в query string для WebSocket
5. `bot/.env.example` дефолты не рассчитаны на прямой (не через compose) запуск
6. `CopyVerificationReportRequest.checked_at` без `normalize_to_utc`-валидатора
7. `Alert.delivered_web_at` — колонка есть, не выставляется в `AlertRead`
8. **Критичный, в работе**: невалидный cron в одной BackupJob крашит весь процесс агента
   (`BackgroundServiceExceptionBehavior=StopHost`, необработанный `Cronos.CronFormatException`
   в `JobScheduler.Tick`/`GetOrComputeNextFire`) — изоляция ошибки per-job + видимость в UI +
   (возможно) backend-валидация cron при создании/редактировании job.
