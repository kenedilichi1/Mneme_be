import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base_class import BaseModel
from app.utils.timezone import utcnow



class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    first_name:Mapped[str] = mapped_column(String, nullable=True)
    last_name:Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    otp_codes: Mapped[list["OtpCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_accounts: Mapped[list["OauthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
