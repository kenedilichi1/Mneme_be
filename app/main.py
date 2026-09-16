import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

import app.core.db.models  # noqa: F401 ensures all models are registered before mapper configuration
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db.db import engine
from app.core.logging import setup_logging
from app.core.rate_limit import setup_rate_limiting

setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connected (%s@%s)", settings.POSTGRES_DB, settings.POSTGRES_HOST)
    yield
    await engine.dispose()
    logger.info("Database connection pool closed")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(SlowAPIMiddleware)
setup_rate_limiting(app)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")



@app.get("/")
def read_root():
    return {"message": f"Welcome to the {settings.APP_NAME}"}



@app.get("/health")
def read_health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)