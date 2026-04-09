"""add_snapshot_and_review_indexes

Revision ID: d8b7f2e1a4c9
Revises: c7a9f9e4312b
Create Date: 2026-04-09 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d8b7f2e1a4c9"
down_revision: Union[str, Sequence[str], None] = "c7a9f9e4312b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_listing_snapshot_listing_captured",
        "listing_snapshot",
        ["listing_id", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_manual_review_created_at",
        "manual_review",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_manual_review_created_at", table_name="manual_review")
    op.drop_index("ix_listing_snapshot_listing_captured", table_name="listing_snapshot")
