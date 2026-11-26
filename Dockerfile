# Один образ на всю Студию: движок (python -m runtime run) и сервер Студии
# (python -m studio.server) с собранным холстом внутри. Какой процесс запустить –
# решает compose (deploy/compose.yaml), образ общий.

# ── 1. холст (web/dist) ──────────────────────────────────────────────────────
FROM node:24-alpine AS web
WORKDIR /web
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ── 2. python-часть ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /app
# сначала только зависимости – слой не пересобирается при правке кода
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY studio/ studio/
COPY runtime/ runtime/
COPY schema/ schema/
COPY demo/ demo/
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist web/dist

ENV PATH="/app/.venv/bin:$PATH" \
    STUDIO_DATA=/data \
    BOT_REPO_PATH=/bot-engine \
    STUDIO_ENV_FILE=/run/nonexistent \
    STUDIO_RUNTIME_HOST=0.0.0.0 \
    STUDIO_RUNTIME_PORT=8781

RUN useradd -u 1000 -m studio && mkdir -p /data && chown studio /data
USER studio
EXPOSE 8765 8781
CMD ["python", "-m", "studio.server", "--host", "0.0.0.0", "--port", "8765"]
