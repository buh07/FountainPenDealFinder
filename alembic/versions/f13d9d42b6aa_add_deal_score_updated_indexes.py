"""add_deal_score_updated_indexes

Revision ID: f13d9d42b6aa
Revises: e1c4a2b9d7f0
Create Date: 2026-04-09 15:25:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f13d9d42b6aa"
down_revision: Union[str, Sequence[str], None] = "e1c4a2b9d7f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_deal_score_updated_at",
        "deal_score",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_deal_score_bucket_updated_at",
        "deal_score",
        ["bucket", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_deal_score_bucket_updated_at", table_name="deal_score")
    op.drop_index("ix_deal_score_updated_at", table_name="deal_score")
