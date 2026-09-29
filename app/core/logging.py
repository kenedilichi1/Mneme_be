import logging
import logging.config

from app.core.config import settings


_FMT = "%(asctime)s | %(levelname)-8s | %(name)s - %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """Configure the root logger and silence noisy third-party loggers."""
    log_level = settings.LOG_LEVEL

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": _FMT,
                    "datefmt": _DATE_FMT,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": log_level,
                "handlers": ["console"],
            },
            "loggers": {
                "uvicorn": {"level": log_level, "propagate": True},
                "uvicorn.access": {"level": log_level, "propagate": True},
                "uvicorn.error": {"level": log_level, "propagate": True},
                "sqlalchemy.engine": {
                    "level": "INFO" if settings.SQL_ECHO else "WARNING",
                    "propagate": True,
                },
            },
        }
    )

    logging.getLogger(__name__).info(
        "Logging initialised (level=%s, debug=%s)", log_level, settings.DEBUG
    )
