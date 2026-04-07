"""add_proxy_profit_tracking_columns

Revision ID: 4f22d9c6f33d
Revises: 8de48d5c297e
Create Date: 2026-04-07 18:24:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f22d9c6f33d"
down_revision: Union[str, Sequence[str], None] = "8de48d5c297e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proxy_option_estimate",
        sa.Column("resale_reference_jpy", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "proxy_option_estimate",
        sa.Column("expected_profit_jpy", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "proxy_option_estimate",
        sa.Column("expected_profit_pct", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "proxy_option_estimate",
        sa.Column("arbitrage_rank", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_proxy_option_estimate_arbitrage_rank",
        "proxy_option_estimate",
        ["arbitrage_rank"],
        unique=False,
    )

    # SQLite does not support ALTER COLUMN DROP DEFAULT.
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("proxy_option_estimate", "resale_reference_jpy", server_default=None)
        op.alter_column("proxy_option_estimate", "expected_profit_jpy", server_default=None)
        op.alter_column("proxy_option_estimate", "expected_profit_pct", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_proxy_option_estimate_arbitrage_rank", table_name="proxy_option_estimate")
    op.drop_column("proxy_option_estimate", "arbitrage_rank")
    op.drop_column("proxy_option_estimate", "expected_profit_pct")
    op.drop_column("proxy_option_estimate", "expected_profit_jpy")
    op.drop_column("proxy_option_estimate", "resale_reference_jpy")
