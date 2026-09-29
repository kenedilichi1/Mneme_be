"""documents status check constraint

Revision ID: b5e7f9a1c3d4
Revises: a4c6d8f0b2e1
Create Date: 2026-09-29 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b5e7f9a1c3d4"
down_revision: Union[str, Sequence[str], None] = "a4c6d8f0b2e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('pending_upload', 'uploaded', 'processing', 'processed', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_status", "documents", type_="check")
