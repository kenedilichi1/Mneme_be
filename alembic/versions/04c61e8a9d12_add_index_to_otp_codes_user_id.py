"""add index to otp_codes user_id

Revision ID: 04c61e8a9d12
Revises: 03b50c9e5c31
Create Date: 2026-09-17 10:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "04c61e8a9d12"
down_revision: Union[str, Sequence[str], None] = "03b50c9e5c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_otp_codes_user_id"), "otp_codes", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_otp_codes_user_id"), table_name="otp_codes")
