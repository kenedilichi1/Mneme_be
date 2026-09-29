import asyncio
import logging

from app.worker.document_worker import start_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting document worker...")
    asyncio.run(start_worker())


if __name__ == "__main__":
    main()
