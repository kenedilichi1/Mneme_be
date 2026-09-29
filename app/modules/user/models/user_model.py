import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, ForeignKey, DateTime, Index, UniqueConstraint, func, literal_column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base_class import BaseModel
from app.utils.timezone import utcnow
from app.modules.documents.models.document_model import Document
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.models.oauth_account_model import OauthAccount
from app.modules.auth.models.refresh_token_model import RefreshToken



class User(BaseModel):
    __tablename__ = "users"
    # Email is identity: unique across casings, backed by the same expression
    # the lookup uses (see UserRepository.get_by_email and the migration that
    # lowercased legacy rows).
    __table_args__ = (
        Index(
            "uq_users_email_lower",
            func.lower(literal_column("email")),
            unique=True,
        ),
    )

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # OTP lockout state: counted across all OTPs for this email (see otp_lockout)
    failed_otp_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    otp_attempts_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


    otp_codes: Mapped[list["OtpCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_accounts: Mapped[list["OauthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
