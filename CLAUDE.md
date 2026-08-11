# Backup Orchestrator — контекст проекта

## Архитектура
- Backend: Python/FastAPI, SQLAlchemy 2.0 async, SQLite, Docker на Ubuntu
- Агент: C#/.NET 8 (self-contained single-file, win-x64), WinSCP-интеграция, `agent/` (Core/Worker/Tests)
- Веб-UI: React/TypeScript, ещё не начат
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
- ⏳ FTP copy-integrity checker (Alert 6th FK — rebuild migration, риск потери данных)
- ⏳ Веб-UI
- ✅ C#-агент (implemented, `dotnet build` clean; WinSCP/live-hosting path unverified outside Windows — см. `agent/`)
