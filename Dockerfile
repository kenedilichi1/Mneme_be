
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

# uv comes from PyPI, not the ghcr.io image: ghcr.io is unreachable from some
# networks and the build dies on `COPY --from=ghcr.io/...` metadata resolution.
ARG UV_VERSION=0.11.32
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev \
    && chown -R appuser:appuser /app/.venv

ENV UV_CACHE_DIR=/tmp/.uv-cache

# Native libs for opencv-python (docling -> rapidocr / tableformer).
# python-slim ships without them, so `import cv2` dies with
# ImportError: libxcb.so.1 and every PDF parse fails.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face model cache (embedding + Docling models); mounted as a volume in compose
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p ${HF_HOME} && chown -R appuser:appuser /app/.cache

USER appuser

COPY --chown=appuser:appuser . .

EXPOSE 8000

CMD ["/app/.venv/bin/fastapi", "run", "--host", "0.0.0.0", "--port", "8000", "app/main.py"]
