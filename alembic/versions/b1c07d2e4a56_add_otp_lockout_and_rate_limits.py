"""per-email OTP lockout counters and shared rate-limit storage

Revision ID: b1c07d2e4a56
Revises: 59f9de2b0b30
Create Date: 2026-09-28 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c07d2e4a56"
down_revision: Union[str, Sequence[str], None] = "59f9de2b0b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Lockout counter lives on the user so it spans all OTPs for the email
    op.add_column(
        "users",
        sa.Column("failed_otp_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("otp_attempts_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Per-OTP attempt counter superseded by the per-email lockout
    op.drop_column("otp_codes", "attempts")

    # Fixed-window rate-limit counters, shared by all processes
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_rate_limits_window_started_at", "rate_limits", ["window_started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limits_window_started_at", table_name="rate_limits")
    op.drop_table("rate_limits")
    op.add_column(
        "otp_codes",
        sa.Column("attempts", sa.Integer(), nullable=True),
    )
    op.drop_column("users", "otp_attempts_window_started_at")
    op.drop_column("users", "failed_otp_attempts")
