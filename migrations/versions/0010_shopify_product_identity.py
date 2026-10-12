"""Independent Shopify draft identities; no backfill or Woo changes."""
from alembic import op
import sqlalchemy as sa

revision = "0010_shopify_product_identity"
down_revision = "0009_woo_live_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shopify_product_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="SET NULL")),
        sa.Column("source_identity", sa.String(1024), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("store_key", sa.String(64), nullable=False),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("store_domain", sa.String(253), nullable=False),
        sa.Column("shopify_product_id", sa.String(100)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("verification_state", sa.String(32), nullable=False),
        sa.Column("operation_id", sa.String(32), sa.ForeignKey("catalogue_operation.id", ondelete="SET NULL")),
        sa.Column("reviewed_digest", sa.String(64), nullable=False),
        sa.Column("media_evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("verified_at", sa.DateTime()),
        sa.UniqueConstraint("store_key", "product_id", name="uq_shopify_store_product"),
        sa.UniqueConstraint("store_key", "source_identity", name="uq_shopify_store_source"),
        sa.UniqueConstraint("store_key", "shopify_product_id", name="uq_shopify_store_remote"),
        sa.CheckConstraint("length(media_evidence_json) <= 65536", name="ck_shopify_evidence_bound"),
    )
    for column in ("product_id", "store_key"):
        op.create_index(f"ix_shopify_product_identity_{column}", "shopify_product_identity", [column])


def downgrade():
    op.drop_table("shopify_product_identity")
