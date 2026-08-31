"""Add store-scoped Woo sync identities.

Revision ID: 0007_woo_sync_identity
Revises: 0006_relationship_workspace
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_woo_sync_identity"
down_revision = "0006_relationship_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "woo_product_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stable_identity", sa.String(1024), nullable=False),
        sa.Column("sku", sa.String(64)),
        sa.Column("store_key", sa.String(64), nullable=False),
        sa.Column("store_host", sa.String(253), nullable=False),
        sa.Column("woo_product_id", sa.Integer()),
        sa.Column("last_successful_sync_at", sa.DateTime()),
        sa.Column("last_published_digest", sa.String(64)),
        sa.Column("last_remote_modified_at", sa.DateTime()),
        sa.Column("last_remote_digest", sa.String(64)),
        sa.Column("sync_state", sa.String(32), nullable=False, server_default="unlinked"),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("store_key", "product_id", name="uq_woo_product_identity_store_product"),
        sa.UniqueConstraint("store_key", "woo_product_id", name="uq_woo_product_identity_store_remote"),
    )
    for column in ("product_id", "sku", "store_key", "woo_product_id"):
        op.create_index(f"ix_woo_product_identity_{column}", "woo_product_identity", [column])
    op.create_table(
        "woo_variation_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variation_id", sa.Integer(), sa.ForeignKey("variation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stable_identity", sa.String(1024), nullable=False),
        sa.Column("sku", sa.String(64)),
        sa.Column("store_key", sa.String(64), nullable=False),
        sa.Column("store_host", sa.String(253), nullable=False),
        sa.Column("woo_parent_product_id", sa.Integer()),
        sa.Column("woo_variation_id", sa.Integer()),
        sa.Column("last_successful_sync_at", sa.DateTime()),
        sa.Column("last_published_digest", sa.String(64)),
        sa.Column("last_remote_modified_at", sa.DateTime()),
        sa.Column("last_remote_digest", sa.String(64)),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("store_key", "variation_id", name="uq_woo_variation_identity_store_variation"),
        sa.UniqueConstraint("store_key", "woo_parent_product_id", "woo_variation_id", name="uq_woo_variation_identity_store_remote"),
    )
    for column in ("variation_id", "product_id", "sku", "store_key", "woo_parent_product_id", "woo_variation_id"):
        op.create_index(f"ix_woo_variation_identity_{column}", "woo_variation_identity", [column])


def downgrade():
    op.drop_table("woo_variation_identity")
    op.drop_table("woo_product_identity")
