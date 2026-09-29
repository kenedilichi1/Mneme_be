from datetime import datetime

from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class RateLimit(Base):
    """One fixed-window counter row per rate-limit key.

    Lives in Postgres so limits are shared by every process and survive
    restarts (see app/core/rate_limit.py).
    """

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    # indexed so the periodic stale-row cleanup can scan cheaply
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False)
