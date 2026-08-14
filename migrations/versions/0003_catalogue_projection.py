"""Activate catalogue projection and portable provenance.

Revision ID: 0003_projection
Revises: 0002_operations
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_projection"
down_revision = "0002_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("collection", sa.Column("collection_type", sa.String(50)))
    op.add_column("collection", sa.Column("source_relpath", sa.String(1024)))
    op.add_column("collection", sa.Column("shared_json_relpath", sa.String(1024)))
    op.create_index(
        "ix_collection_source_relpath",
        "collection",
        ["source_relpath"],
        unique=True,
    )

    op.add_column("product", sa.Column("source_relpath", sa.String(1024)))
    op.add_column("product", sa.Column("shared_json_relpath", sa.String(1024)))
    op.add_column("product", sa.Column("override_json_relpath", sa.String(1024)))
    op.add_column("product", sa.Column("effective_json_relpath", sa.String(1024)))
    op.add_column("product", sa.Column("resolved_row_json", sa.Text()))
    op.add_column("product", sa.Column("published", sa.Boolean()))
    op.add_column("product", sa.Column("tax_status", sa.String(20)))
    op.add_column("product", sa.Column("tax_class", sa.String(100)))
    op.add_column("product", sa.Column("in_stock", sa.Boolean()))
    op.add_column("product", sa.Column("sold_individually", sa.Boolean()))
    op.add_column("product", sa.Column("purchase_note", sa.Text()))
    op.add_column("product", sa.Column("download_limit", sa.Integer()))
    op.add_column("product", sa.Column("download_expiry_days", sa.Integer()))
    op.add_column("product", sa.Column("grouped_products", sa.Text()))
    op.add_column("product", sa.Column("menu_order", sa.Integer()))
    op.add_column("product", sa.Column("meta_title", sa.String(255)))
    op.add_column("product", sa.Column("meta_description", sa.Text()))
    op.create_index("ix_product_source_relpath", "product", ["source_relpath"])

    op.create_table(
        "product_attribute",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("values", sa.Text(), nullable=False),
        sa.Column("visible", sa.Boolean()),
        sa.Column("is_global", sa.Boolean()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_product_attribute_product_id",
        "product_attribute",
        ["product_id"],
    )

    op.add_column("variation", sa.Column("source_relpath", sa.String(1024)))
    op.add_column("variation", sa.Column("resolved_row_json", sa.Text()))
    op.create_index("ix_variation_source_relpath", "variation", ["source_relpath"])

    op.add_column("variation_attribute", sa.Column("visible", sa.Boolean()))
    op.add_column("variation_attribute", sa.Column("is_global", sa.Boolean()))
    op.add_column("variation_attribute", sa.Column("position", sa.Integer()))

    op.add_column("product_asset", sa.Column("source_relpath", sa.String(1024)))
    op.create_index(
        "ix_product_asset_source_relpath",
        "product_asset",
        ["source_relpath"],
    )


def downgrade():
    op.drop_index("ix_product_asset_source_relpath", table_name="product_asset")
    op.drop_column("product_asset", "source_relpath")

    op.drop_column("variation_attribute", "position")
    op.drop_column("variation_attribute", "is_global")
    op.drop_column("variation_attribute", "visible")

    op.drop_index("ix_variation_source_relpath", table_name="variation")
    op.drop_column("variation", "resolved_row_json")
    op.drop_column("variation", "source_relpath")

    op.drop_index("ix_product_attribute_product_id", table_name="product_attribute")
    op.drop_table("product_attribute")

    op.drop_index("ix_product_source_relpath", table_name="product")
    for column in (
        "meta_description",
        "meta_title",
        "menu_order",
        "grouped_products",
        "download_expiry_days",
        "download_limit",
        "purchase_note",
        "sold_individually",
        "in_stock",
        "tax_class",
        "tax_status",
        "published",
        "resolved_row_json",
        "effective_json_relpath",
        "override_json_relpath",
        "shared_json_relpath",
        "source_relpath",
    ):
        op.drop_column("product", column)

    op.drop_index("ix_collection_source_relpath", table_name="collection")
    op.drop_column("collection", "shared_json_relpath")
    op.drop_column("collection", "source_relpath")
    op.drop_column("collection", "collection_type")
