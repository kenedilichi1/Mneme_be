import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.core.db.models  # noqa: F401 ensures all models are registered before mapper configuration
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db.db import engine
from app.core.logging import setup_logging
from app.core.rate_limit import rate_limit

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



@app.get("/")
def read_root():
    return {"message": f"Welcome to the {settings.APP_NAME}"}



@app.get("/health")
def read_health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)