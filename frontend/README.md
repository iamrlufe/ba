# Backup Orchestrator — Web UI

React + TypeScript + Vite frontend for the Backup Orchestrator FastAPI backend.

## Stack

- React 19 + TypeScript (strict), Vite
- shadcn/ui (new-york style, Tailwind CSS v3, Radix primitives) + Tailwind
- TanStack Query for all server state
- React Router v7 for navigation
- react-hook-form + zod for forms
- openapi-typescript for generated wire types (`src/api/schema.gen.ts`)

## Getting started

```bash
npm install
cp .env.example .env   # VITE_API_BASE_URL defaults to /api
npm run dev
```

The dev server proxies `/api` and `/ws` to `http://localhost:8000` / `ws://localhost:8000`
(see `vite.config.ts`), so the backend must be running separately on port 8000.

## Regenerating the API types

`src/api/schema.gen.ts` and `openapi.snapshot.json` are generated from the backend's live
`/openapi.json` and committed so the frontend can build without a running backend. To
regenerate after a backend schema change:

```bash
# from the repo root, with a throwaway .env (FERNET_KEY / JWT_SECRET_KEY / AGENT_API_KEY)
uv run uvicorn app.main:app --port 8000
# in another shell, from frontend/
npm run gen:api
curl -s http://localhost:8000/openapi.json | python3 -m json.tool > openapi.snapshot.json
```

Then stop the backend and remove any scratch DB file it created.

## Production topology

This repo does not build or configure the reverse proxy itself. In production, a reverse
proxy (nginx, Caddy, Traefik, etc.) must front **both** the built static assets (`dist/`)
and the backend's `/api` and `/ws` routes on the **same origin**. This is required because:

- The backend has no CORS middleware, so the API must be same-origin.
- The JWT (stored in `sessionStorage`, see `src/auth/AuthContext.tsx`) is sent as an
  `Authorization: Bearer` header on `fetch` calls and as a `?token=` query param on the
  WebSocket connection — both assume `location.origin` is the API's origin.

Example nginx sketch (not included/managed by this repo):

```
server {
    listen 443 ssl;
    server_name backup-orchestrator.example.com;

    location / {
        root /srv/backup-orchestrator/frontend/dist;
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Known limitation: JWT in the WebSocket URL

The browser `WebSocket` API cannot set custom headers, so `src/hooks/useJobRunSocket.ts`
sends the full bearer JWT as a `?token=` query-string parameter on the
`/ws/job-runs/{id}` connect request. Query strings commonly end up in reverse-proxy
access logs (and browser history, though this URL is never navigated to directly, only
opened via the `WebSocket` constructor). Recommended mitigations, both **out of scope for
this frontend-only change** since they require backend work:

1. Configure the reverse proxy to omit query strings from its access-log format for the
   `/ws/` location specifically (e.g. nginx: a dedicated `log_format` without `$request`'s
   query part, or `access_log off;` on that location if request logging isn't needed there).
2. Longer-term, better fix: have the backend mint a short-lived, single-use WS ticket
   (e.g. `POST /api/auth/ws-ticket` returning a token valid for ~30s and one connection)
   instead of accepting the full-lifetime JWT over the WS query string at all.

## Scripts

- `npm run dev` — start the Vite dev server
- `npm run build` — type-check (`tsc -b`) and build for production
- `npm run lint` — run oxlint
- `npm run gen:api` — regenerate `src/api/schema.gen.ts` from a locally running backend

## Прод: nginx и WebSocket-токен в query string

Auth-токен для WebSocket (`/ws/job-runs/{id}?token=...`) передаётся через query 
string — ограничение Browser WebSocket API (нет способа передать кастомные 
заголовки при upgrade-запросе). Это означает, что токен **гарантированно 
попадёт в access-лог** любого reverse-proxy на пути между клиентом и backend, 
если явно не настроить иначе.

### Обязательно перед продакшен-деплоем

В конфиге nginx (или Caddy) для location `/ws/` нужно либо замаскировать query 
string в логах, либо полностью отключить логирование для этого пути.

**Вариант A — кастомный log_format без query string (рекомендуется, сохраняет видимость подключений):**

В `http {}` блоке `nginx.conf`:
```nginx
log_format combined_no_query '$remote_addr - $remote_user [$time_local] '
                              '"$request_method $uri" $status $body_bytes_sent '
                              '"$http_referer" "$http_user_agent"';
```

В `location /ws/`:
```nginx
location /ws/ {
    proxy_pass http://backend_upstream;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;

    access_log /var/log/nginx/ws_access.log combined_no_query;
}
```

**Вариант B — полностью отключить логирование для /ws/ (проще, но теряется видимость реконнектов/обрывов):**
```nginx
location /ws/ {
    ...
    access_log off;
}
```

### Долгосрочное решение (не в этой итерации)

Правильное архитектурное решение — короткоживущий WS-тикет: клиент запрашивает 
одноразовый токен через отдельный REST-эндпоинт (например, 
`POST /api/ws-ticket`), обменивает его на подключение, тикет истекает за 
секунды и не пригоден для повторного использования. Это устраняет саму 
причину утечки, а не просто маскирует её в логах. Требует backend-изменения — 
отдельная будущая итерация.
