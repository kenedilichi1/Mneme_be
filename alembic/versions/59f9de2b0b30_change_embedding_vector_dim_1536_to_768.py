"""change_embedding_vector_dim_1536_to_768

Revision ID: 59f9de2b0b30
Revises: 04c61e8a9d12
Create Date: 2026-09-26 13:56:25.490252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59f9de2b0b30'
down_revision: Union[str, Sequence[str], None] = '04c61e8a9d12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Vectors from the old model can't be converted between dimensions, so they
    # are dropped; affected documents must be re-processed to re-embed.
    op.execute(
        "ALTER TABLE document_chunks "
        "ALTER COLUMN embedding TYPE vector(768) USING NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE document_chunks "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )
