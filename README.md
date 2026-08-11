
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
