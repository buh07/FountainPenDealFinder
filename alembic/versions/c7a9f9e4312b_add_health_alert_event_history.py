"""add_health_alert_event_history

Revision ID: c7a9f9e4312b
Revises: 9d51f7c2a6e1
Create Date: 2026-04-08 13:55:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7a9f9e4312b"
down_revision: Union[str, Sequence[str], None] = "9d51f7c2a6e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "health_alert_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("alert_signature", sa.String(length=64), nullable=True),
        sa.Column("alert_keys_json", sa.Text(), nullable=False),
        sa.Column("alert_count", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("destination", sa.String(length=1024), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("deduped", sa.Boolean(), nullable=False),
        sa.Column("cooldown_remaining_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_health_alert_event_alert_signature"), "health_alert_event", ["alert_signature"], unique=False)
    op.create_index(op.f("ix_health_alert_event_created_at"), "health_alert_event", ["created_at"], unique=False)
    op.create_index(op.f("ix_health_alert_event_deduped"), "health_alert_event", ["deduped"], unique=False)
    op.create_index(op.f("ix_health_alert_event_reason"), "health_alert_event", ["reason"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_health_alert_event_reason"), table_name="health_alert_event")
    op.drop_index(op.f("ix_health_alert_event_deduped"), table_name="health_alert_event")
    op.drop_index(op.f("ix_health_alert_event_created_at"), table_name="health_alert_event")
    op.drop_index(op.f("ix_health_alert_event_alert_signature"), table_name="health_alert_event")
    op.drop_table("health_alert_event")
