"""Shopify operational collection mappings and comparison summaries; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0011_shopify_taxonomy"
down_revision = "0010_shopify_product_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("shopify_collection_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(1100), nullable=False),
        sa.Column("remote_id", sa.String(100)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("standard_category_id", sa.String(150)),
        sa.Column("operation_id", sa.String(32), sa.ForeignKey("catalogue_operation.id", ondelete="SET NULL")),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("store_id", "source_key", name="uq_shopify_collection_source"),
        sa.UniqueConstraint("store_id", "remote_id", name="uq_shopify_collection_remote"),
        sa.CheckConstraint("length(evidence_json) <= 32768", name="ck_shopify_collection_evidence"))
    op.create_table("shopify_product_comparison",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("source_identity", sa.String(1024), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("compared_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("store_id", "source_identity", name="uq_shopify_comparison_source"),
        sa.CheckConstraint("length(summary_json) <= 32768", name="ck_shopify_comparison_bound"))


def downgrade():
    op.drop_table("shopify_product_comparison")
    op.drop_table("shopify_collection_identity")
