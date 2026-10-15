import asyncio
import logging

# Registers every model (notably User) so SQLAlchemy can resolve string
# relationships like Document.user — same side-effect import as app/main.py.
import app.core.db.models  # noqa: F401

from app.worker.document_worker import start_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting document worker...")
    asyncio.run(start_worker())


if __name__ == "__main__":
    main()
