"""add_review_snapshot_and_policy_tables

Revision ID: 9d51f7c2a6e1
Revises: 4f22d9c6f33d
Create Date: 2026-04-07 20:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d51f7c2a6e1"
down_revision: Union[str, Sequence[str], None] = "4f22d9c6f33d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupon_rule",
        sa.Column("coupon_rule_id", sa.String(length=36), nullable=False),
        sa.Column("proxy_name", sa.String(length=64), nullable=False),
        sa.Column("marketplace_source", sa.String(length=64), nullable=True),
        sa.Column("coupon_id", sa.String(length=128), nullable=False),
        sa.Column("discount_type", sa.String(length=32), nullable=False),
        sa.Column("discount_value", sa.Float(), nullable=False),
        sa.Column("min_buy_price_jpy", sa.Integer(), nullable=False),
        sa.Column("max_discount_jpy", sa.Integer(), nullable=True),
        sa.Column("is_stackable", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("coupon_rule_id"),
    )
    op.create_index(op.f("ix_coupon_rule_coupon_id"), "coupon_rule", ["coupon_id"], unique=False)
    op.create_index(op.f("ix_coupon_rule_marketplace_source"), "coupon_rule", ["marketplace_source"], unique=False)
    op.create_index(op.f("ix_coupon_rule_proxy_name"), "coupon_rule", ["proxy_name"], unique=False)

    op.create_table(
        "listing_image",
        sa.Column("image_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("image_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["raw_listing.listing_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("image_id"),
        sa.UniqueConstraint("listing_id", "image_url", name="uq_listing_image_url"),
    )
    op.create_index(op.f("ix_listing_image_listing_id"), "listing_image", ["listing_id"], unique=False)

    op.create_table(
        "listing_snapshot",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("current_price_jpy", sa.Integer(), nullable=False),
        sa.Column("price_buy_now_jpy", sa.Integer(), nullable=True),
        sa.Column("bid_count", sa.Integer(), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_attributes_json", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["raw_listing.listing_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("listing_id", "snapshot_hash", name="uq_listing_snapshot_hash"),
    )
    op.create_index(op.f("ix_listing_snapshot_captured_at"), "listing_snapshot", ["captured_at"], unique=False)
    op.create_index(op.f("ix_listing_snapshot_listing_id"), "listing_snapshot", ["listing_id"], unique=False)
    op.create_index(op.f("ix_listing_snapshot_snapshot_hash"), "listing_snapshot", ["snapshot_hash"], unique=False)

    op.create_table(
        "manual_review",
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("corrected_classification_id", sa.String(length=255), nullable=True),
        sa.Column("corrected_condition_grade", sa.String(length=32), nullable=True),
        sa.Column("is_false_positive", sa.Boolean(), nullable=False),
        sa.Column("was_purchased", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["raw_listing.listing_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_index(op.f("ix_manual_review_action_type"), "manual_review", ["action_type"], unique=False)
    op.create_index(op.f("ix_manual_review_listing_id"), "manual_review", ["listing_id"], unique=False)

    op.create_table(
        "proxy_pricing_policy",
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("proxy_name", sa.String(length=64), nullable=False),
        sa.Column("marketplace_source", sa.String(length=64), nullable=True),
        sa.Column("service_fee_jpy", sa.Integer(), nullable=False),
        sa.Column("intl_shipping_jpy", sa.Integer(), nullable=False),
        sa.Column("min_buy_price_jpy", sa.Integer(), nullable=False),
        sa.Column("max_buy_price_jpy", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("policy_id"),
    )
    op.create_index(op.f("ix_proxy_pricing_policy_marketplace_source"), "proxy_pricing_policy", ["marketplace_source"], unique=False)
    op.create_index(op.f("ix_proxy_pricing_policy_proxy_name"), "proxy_pricing_policy", ["proxy_name"], unique=False)

    op.create_table(
        "training_example",
        sa.Column("example_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("source_review_id", sa.String(length=36), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("label_json", sa.Text(), nullable=False),
        sa.Column("feature_json", sa.Text(), nullable=False),
        sa.Column("split", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["raw_listing.listing_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_review_id"], ["manual_review.review_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("example_id"),
    )
    op.create_index(op.f("ix_training_example_listing_id"), "training_example", ["listing_id"], unique=False)
    op.create_index(op.f("ix_training_example_source_review_id"), "training_example", ["source_review_id"], unique=False)
    op.create_index(op.f("ix_training_example_task_type"), "training_example", ["task_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_training_example_task_type"), table_name="training_example")
    op.drop_index(op.f("ix_training_example_source_review_id"), table_name="training_example")
    op.drop_index(op.f("ix_training_example_listing_id"), table_name="training_example")
    op.drop_table("training_example")

    op.drop_index(op.f("ix_proxy_pricing_policy_proxy_name"), table_name="proxy_pricing_policy")
    op.drop_index(op.f("ix_proxy_pricing_policy_marketplace_source"), table_name="proxy_pricing_policy")
    op.drop_table("proxy_pricing_policy")

    op.drop_index(op.f("ix_manual_review_listing_id"), table_name="manual_review")
    op.drop_index(op.f("ix_manual_review_action_type"), table_name="manual_review")
    op.drop_table("manual_review")

    op.drop_index(op.f("ix_listing_snapshot_snapshot_hash"), table_name="listing_snapshot")
    op.drop_index(op.f("ix_listing_snapshot_listing_id"), table_name="listing_snapshot")
    op.drop_index(op.f("ix_listing_snapshot_captured_at"), table_name="listing_snapshot")
    op.drop_table("listing_snapshot")

    op.drop_index(op.f("ix_listing_image_listing_id"), table_name="listing_image")
    op.drop_table("listing_image")

    op.drop_index(op.f("ix_coupon_rule_proxy_name"), table_name="coupon_rule")
    op.drop_index(op.f("ix_coupon_rule_marketplace_source"), table_name="coupon_rule")
    op.drop_index(op.f("ix_coupon_rule_coupon_id"), table_name="coupon_rule")
    op.drop_table("coupon_rule")
