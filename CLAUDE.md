# Backup Orchestrator — контекст проекта

## Архитектура
- Backend: Python/FastAPI, SQLAlchemy 2.0 async, SQLite, Docker на Ubuntu
- Агент: C#/.NET (self-contained single-file), WinSCP-интеграция, ещё не начат
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
- ⏳ C#-агент
