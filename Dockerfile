# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
COPY web/public ./public
RUN npm run build

FROM python:3.12-slim-bookworm AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends gcc git libc6-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY native/dmrdecode.c native/dmrdecode.h native/THIRD_PARTY_LICENSES.txt ./native/
COPY scripts/build-codec.sh ./scripts/build-codec.sh
RUN ./scripts/build-codec.sh
COPY deploy/build_sqlite.py ./deploy/build_sqlite.py
RUN python deploy/build_sqlite.py /sqlite-build

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client sshpass ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ARG APP_UID=1000
ARG APP_GID=1000
RUN test "$APP_UID" -gt 0 && test "$APP_GID" -gt 0 \
    && if ! getent group "$APP_GID" >/dev/null; then groupadd --gid "$APP_GID" starwave; fi \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home starwave \
    && mkdir -p /data /models/cache \
    && chown "$APP_UID:$APP_GID" /data /models /models/cache
WORKDIR /app
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /sqlite-build/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
COPY --from=python-build /sqlite-build/SQLITE_PROVENANCE.txt /app/SQLITE_PROVENANCE.txt
RUN ldconfig && python -c "import sqlite3; assert sqlite3.sqlite_version == '3.53.1'"
COPY --from=python-build /app/native/build/libdmrdecode.so /app/native/build/libdmrdecode.so
COPY --from=python-build /app/native/build/THIRD_PARTY_LICENSES.txt /app/native/build/THIRD_PARTY_LICENSES.txt
COPY backend ./backend
COPY LICENSE /app/LICENSE
COPY --from=web-build /build/web/dist ./web/dist
COPY --chmod=755 scripts/container-entrypoint.sh /usr/local/bin/starwave-entrypoint
ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/starwave \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    CACHE_DIR=/models/cache \
    XDG_CACHE_HOME=/models/cache \
    HF_HOME=/models/cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    BIND_HOST=0.0.0.0 \
    REQUIRE_EXPLICIT_SSH=true \
    PORT=8788
USER ${APP_UID}:${APP_GID}
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8788')+'/api/health',timeout=3).close()"
ENTRYPOINT ["starwave-entrypoint"]
CMD ["python", "-m", "backend.main"]
