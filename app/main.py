import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.core.db.models  # noqa: F401 ensures all models are registered before mapper configuration
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db.db import engine
from app.core.logging import setup_logging
from app.core.rate_limit import rate_limit
from app.core.responses import error_json, http_exception_response, ok

from app.core.queue.queue import rabbitmq

setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connected (%s@%s)", settings.get_db_name, settings.POSTGRES_HOST)
    yield
    await rabbitmq.close()
    await engine.dispose()
    logger.info("Resources closed cleanly")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

cors_kwargs = {
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if "*" in settings.CORS_ORIGINS:
    cors_kwargs["allow_origins"] = ["*"]
    cors_kwargs["allow_credentials"] = False
else:
    cors_kwargs["allow_origins"] = settings.CORS_ORIGINS
    cors_kwargs["allow_credentials"] = True

app.add_middleware(CORSMiddleware, **cors_kwargs)


app.include_router(
    api_router,
    prefix="/api/v1",
    dependencies=[
        Depends(rate_limit("global", settings.GLOBAL_RATE_LIMIT_PER_MINUTE))
    ],
)


# ── Uniform error envelope ───────────────────────────────────────────────────
# Every error (raised HTTPException, validation failure, unexpected crash)
# leaves the API in the same shape: {"success": false, "error": {...}}.
# Success responses are enveloped by each handler via ok()/SuccessEnvelope.


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return http_exception_response(exc)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    return error_json(422, "Request validation failed", exc.errors())


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception while processing %s", request.url.path)
    return error_json(500, "Internal server error")


@app.get("/")
def read_root():
    return ok({"app": settings.APP_NAME}, "Welcome")


@app.get("/health")
def read_health():
    return ok({"status": "ok"}, "OK")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)