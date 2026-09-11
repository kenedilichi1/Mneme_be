
ARG PYTHON_VERSION=3.12.13
FROM python:${PYTHON_VERSION}-slim as base

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONUNBUFFERED=1

WORKDIR /app
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project \
    && chown -R appuser:appuser /app/.venv

ENV UV_CACHE_DIR=/tmp/.uv-cache

USER appuser

COPY --chown=appuser:appuser . .

EXPOSE 8000

CMD ["uv", "run", "fastapi", "dev", "--host", "0.0.0.0"]
