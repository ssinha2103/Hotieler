# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.8.22
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system --gid 10001 hotieler \
    && useradd --system --uid 10001 --gid hotieler --home-dir /app hotieler

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-install-project --all-groups

COPY --chown=hotieler:hotieler . .
RUN uv sync --frozen --no-cache --all-groups
RUN chown hotieler:hotieler /app

USER hotieler

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["uvicorn", "hotieler.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
