"""canonical lowercase emails with case-insensitive uniqueness

Email is identity: `Foo@x.com` and `foo@x.com` must be the same account.
Legacy rows are lowercased (merging any case-duplicates first, keeping the
oldest account and re-pointing its children), then a unique index on
lower(email) makes the database enforce what the application now guarantees
on every write.

Revision ID: c9e2f1a74b30
Revises: b1c07d2e4a56
Create Date: 2026-09-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9e2f1a74b30"
down_revision: Union[str, Sequence[str], None] = "b1c07d2e4a56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Rows grouped by their canonical (lowercased, trimmed) address; `rn` marks
# every duplicate after the first and `keep_id` names the survivor (oldest
# created_at, then lowest id). Repeated per statement because a WITH clause
# only lives for one statement.
_RANKED_CTE = """
    WITH ranked AS (
        SELECT id,
               lower(btrim(email)) AS email_norm,
               row_number() OVER (
                   PARTITION BY lower(btrim(email))
                   ORDER BY created_at, id
               ) AS rn,
               first_value(id) OVER (
                   PARTITION BY lower(btrim(email))
                   ORDER BY created_at, id
               ) AS keep_id
        FROM users
    )
"""

_CHILD_TABLES = ("otp_codes", "oauth_accounts", "documents")


def upgrade() -> None:
    # 1. Fold case-duplicates into the oldest account. Children are re-pointed
    #    before the losers are deleted so no row (or cascade) is lost.
    for table in _CHILD_TABLES:
        op.execute(
            _RANKED_CTE
            + f"""
            UPDATE {table} t
               SET user_id = r.keep_id
              FROM ranked r
             WHERE t.user_id = r.id AND r.rn > 1
            """
        )
    op.execute(
        _RANKED_CTE
        + """
        DELETE FROM users u
         USING ranked r
         WHERE u.id = r.id AND r.rn > 1
        """
    )

    # 2. Every stored address in canonical form (the exact-match unique index
    #    ix_users_email cannot collide: same-norm rows were merged above).
    op.execute(
        """
        UPDATE users
           SET email = lower(btrim(email))
         WHERE email IS DISTINCT FROM lower(btrim(email))
        """
    )

    # 3. The database now rejects case-variant duplicates outright.
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.func.lower(sa.literal_column("email"))],
        unique=True,
    )


def downgrade() -> None:
    # Data changes (merged/lowercased emails) are intentionally kept: they are
    # lossless and reversible only by restoring from backup.
    op.drop_index("uq_users_email_lower", table_name="users")
