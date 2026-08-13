"""Establish the frozen Phase 0 schema baseline.

Revision ID: 0001_phase0
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_phase0"
down_revision = None
branch_labels = None
depends_on = None


def _metadata():
    metadata = sa.MetaData()
    category = sa.Table(
        "category", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(191), nullable=False, unique=True),
        sa.Column("slug", sa.String(191)),
        sa.Column("woo_id", sa.Integer(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    collection = sa.Table(
        "collection", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(191), nullable=False),
        sa.Column("slug", sa.String(191), index=True),
        sa.Column("root_path", sa.String(1024), nullable=False, unique=True),
        sa.Column("sku_prefix", sa.String(64), nullable=False, unique=True),
        sa.Column("shared_json_path", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    service = sa.Table(
        "service", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100)),
        sa.Column("type", sa.String(20)),
        sa.Column("renewal_date", sa.Date()),
        sa.Column("auto_renew", sa.Boolean()),
        sa.Column("notes", sa.String(255)),
    )
    settings = sa.Table(
        "settings", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_folder", sa.String(512)),
        sa.Column("output_folder", sa.String(512)),
        sa.Column("url_prefix", sa.String(512)),
    )
    tag = sa.Table(
        "tag", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(191), nullable=False, unique=True),
        sa.Column("slug", sa.String(191)),
        sa.Column("woo_id", sa.Integer(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    user = sa.Table(
        "user", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(120), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password", sa.String(128), nullable=False),
        sa.Column("is_admin", sa.Boolean()),
    )
    product = sa.Table(
        "product", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(64), unique=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255)),
        sa.Column("product_type", sa.String(20)),
        sa.Column("collection_type", sa.String(50)),
        sa.Column("collection_id", sa.Integer(), sa.ForeignKey(collection.c.id), index=True),
        sa.Column("product_dir", sa.String(1024), index=True),
        sa.Column("shared_json_path", sa.String(1024)),
        sa.Column("override_json_path", sa.String(1024)),
        sa.Column("effective_json_path", sa.String(1024)),
        sa.Column("regular_price", sa.Numeric(10, 2)),
        sa.Column("sale_price", sa.Numeric(10, 2)),
        sa.Column("sale_start", sa.DateTime()),
        sa.Column("sale_end", sa.DateTime()),
        sa.Column("manage_stock", sa.Boolean()),
        sa.Column("stock_quantity", sa.Integer()),
        sa.Column("backorders", sa.String(20)),
        sa.Column("weight", sa.Numeric(10, 3)),
        sa.Column("length", sa.Numeric(10, 3)),
        sa.Column("width", sa.Numeric(10, 3)),
        sa.Column("height", sa.Numeric(10, 3)),
        sa.Column("shipping_class", sa.String(64)),
        sa.Column("short_description", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("external_url", sa.String(255)),
        sa.Column("button_text", sa.String(100)),
        sa.Column("upsell_ids", sa.Text()),
        sa.Column("cross_sell_ids", sa.Text()),
        sa.Column("status", sa.String(20)),
        sa.Column("catalog_visibility", sa.String(20)),
        sa.Column("reviews_allowed", sa.Boolean()),
        sa.Column("featured", sa.Boolean()),
        sa.Column("image_url", sa.String(512)),
        sa.Column("woo_id", sa.Integer(), index=True),
        sa.Column("woo_synced_at", sa.DateTime()),
        sa.Column("woo_updated_at", sa.DateTime()),
        sa.Column("local_updated_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    variation = sa.Table(
        "variation", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey(product.c.id), nullable=False, index=True),
        sa.Column("sku", sa.String(64), unique=True, index=True),
        sa.Column("regular_price", sa.Numeric(10, 2)),
        sa.Column("sale_price", sa.Numeric(10, 2)),
        sa.Column("sale_start", sa.DateTime()),
        sa.Column("sale_end", sa.DateTime()),
        sa.Column("manage_stock", sa.Boolean()),
        sa.Column("stock_quantity", sa.Integer()),
        sa.Column("backorders", sa.String(20)),
        sa.Column("weight", sa.Numeric(10, 3)),
        sa.Column("length", sa.Numeric(10, 3)),
        sa.Column("width", sa.Numeric(10, 3)),
        sa.Column("height", sa.Numeric(10, 3)),
        sa.Column("image_url", sa.String(512)),
        sa.Column("is_default", sa.Boolean()),
        sa.Column("visible", sa.Boolean()),
        sa.Column("status", sa.String(20)),
        sa.Column("menu_order", sa.Integer()),
        sa.Column("woo_id", sa.Integer(), index=True),
        sa.Column("woo_synced_at", sa.DateTime()),
        sa.Column("woo_updated_at", sa.DateTime()),
        sa.Column("local_updated_at", sa.DateTime()),
    )
    sa.Table(
        "product_categories", metadata,
        sa.Column("product_id", sa.Integer(), sa.ForeignKey(product.c.id), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey(category.c.id), primary_key=True),
    )
    sa.Table(
        "product_tags", metadata,
        sa.Column("product_id", sa.Integer(), sa.ForeignKey(product.c.id), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey(tag.c.id), primary_key=True),
    )
    sa.Table(
        "product_image", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey(product.c.id), nullable=False, index=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("alt_text", sa.String(255)),
        sa.Column("position", sa.Integer()),
        sa.Column("woo_id", sa.Integer(), index=True),
    )
    sa.Table(
        "variation_image", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variation_id", sa.Integer(), sa.ForeignKey(variation.c.id), nullable=False, index=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("alt_text", sa.String(255)),
        sa.Column("position", sa.Integer()),
    )
    sa.Table(
        "variation_attribute", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variation_id", sa.Integer(), sa.ForeignKey(variation.c.id), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("value", sa.String(191), nullable=False),
    )
    sa.Table(
        "product_asset", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey(product.c.id), nullable=False, index=True),
        sa.Column("variation_id", sa.Integer(), sa.ForeignKey(variation.c.id), index=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(50)),
        sa.Column("label", sa.String(255)),
        sa.Column("is_primary", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    return metadata


def upgrade():
    _metadata().create_all(bind=op.get_bind(), checkfirst=False)


def downgrade():
    _metadata().drop_all(bind=op.get_bind(), checkfirst=False)
