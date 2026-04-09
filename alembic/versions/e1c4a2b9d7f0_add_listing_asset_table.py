"""add_listing_asset_table

Revision ID: e1c4a2b9d7f0
Revises: d8b7f2e1a4c9
Create Date: 2026-04-09 15:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1c4a2b9d7f0"
down_revision: Union[str, Sequence[str], None] = "d8b7f2e1a4c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listing_asset",
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("local_path", sa.String(length=2048), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["raw_listing.listing_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("asset_id"),
        sa.UniqueConstraint("listing_id", "asset_type", "content_hash", name="uq_listing_asset_hash"),
    )
    op.create_index(op.f("ix_listing_asset_listing_id"), "listing_asset", ["listing_id"], unique=False)
    op.create_index(op.f("ix_listing_asset_asset_type"), "listing_asset", ["asset_type"], unique=False)
    op.create_index(op.f("ix_listing_asset_content_hash"), "listing_asset", ["content_hash"], unique=False)
    op.create_index(op.f("ix_listing_asset_created_at"), "listing_asset", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_listing_asset_created_at"), table_name="listing_asset")
    op.drop_index(op.f("ix_listing_asset_content_hash"), table_name="listing_asset")
    op.drop_index(op.f("ix_listing_asset_asset_type"), table_name="listing_asset")
    op.drop_index(op.f("ix_listing_asset_listing_id"), table_name="listing_asset")
    op.drop_table("listing_asset")
