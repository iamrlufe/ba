# Build context = ./frontend (this Dockerfile's own directory), NOT the repo
# root -- unlike the root Dockerfile (backend) and bot/Dockerfile, which both
# require repo-root context because they `COPY app ./app` from outside their
# own subdirectory, this image never needs anything outside frontend/ -- it's
# a fully self-contained npm package. docker-compose.yml's
# `build.context: ./frontend` for this service is therefore correct and
# deliberately different from the other two services' `context: .`. See the
# top-of-file comment in docker-compose.yml.

# ---- Stage 1: build ----
# Multi-stage IS correct here, unlike the single-stage backend image: the
# backend's runtime process (uvicorn) is the same Python interpreter that ran
# at build time, so there was nothing heavyweight to discard afterwards. Here,
# the entire Node/npm/tsc/vite/rollup toolchain (and node_modules, often
# hundreds of MB) is needed ONLY to produce dist/, and is never touched again
# once nginx is serving static files -- this is the canonical multi-stage use
# case.
FROM node:22-alpine AS build

WORKDIR /srv/app

# Dependency manifests copied first (dependency-before-source-code layering,
# mirroring the root Dockerfile's `COPY pyproject.toml uv.lock ./` + `RUN uv
# sync` before `COPY app ./app`), so `npm ci` is cache-hit on rebuilds that
# only touch application source. .npmrc (legacy-peer-deps=true) MUST be
# present before `npm ci` runs -- npm reads .npmrc from the working directory
# automatically, no CLI flag required, but only if the file has already been
# copied in.
COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY . .

# VITE_API_BASE_URL is compiled directly into the JS bundle by esbuild/rollup
# during `vite build` (import.meta.env.VITE_API_BASE_URL is statically
# replaced at build time) -- it is NOT read from the environment at container
# runtime, unlike the backend's pydantic-settings .env, which IS read live at
# process start. ARG->ENV (not ARG alone) is required because `npm run build`
# shells out to `vite build` as a child process, which reads
# process.env.VITE_API_BASE_URL -- Dockerfile ARGs are only visible to RUN
# instructions directly, not to arbitrary env lookups inside a spawned
# process, unless promoted to ENV first.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ---- Stage 2: serve ----
FROM nginx:alpine AS serve

# nginx:alpine's own entrypoint / master process runs as root (needed to bind
# port 80 and run /docker-entrypoint.d/*.sh init scripts), but its worker
# processes -- the ones that actually handle every request -- already drop to
# the unprivileged `nginx` user (uid 101) via the stock `user nginx;`
# directive in the base image's nginx.conf. This is the image's standard,
# unmodified behavior; verified directly against this image, not assumed. No
# extra `USER` directive is added here: doing so would break the
# root-requiring /docker-entrypoint.d scripts and the port-80 bind without
# also switching to a non-privileged port + the nginxinc/docker-nginx-
# unprivileged image, which is a separate hardening decision out of scope for
# this iteration.

# nginx.conf here is a full replacement for conf.d/default.conf, not the
# base image's top-level nginx.conf -- preserves the base image's http{}-level
# defaults (gzip, mime.types, sendfile, worker-tuning entrypoint output).
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /srv/app/dist /usr/share/nginx/html

EXPOSE 80

# wget confirmed present in this nginx:alpine build; used by convention over
# curl (also present, but wget is the more consistently shipped tool across
# Alpine base images generally).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
