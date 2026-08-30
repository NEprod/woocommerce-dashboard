"""Add local product relationship edges.

Revision ID: 0005_relationships
Revises: 0004_lifecycle
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_relationships"
down_revision = "0004_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_relationship",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_product_id",
            sa.Integer(),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_sku", sa.String(64), nullable=False),
        sa.Column(
            "resolved_target_product_id",
            sa.Integer(),
            sa.ForeignKey("product.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source_product_id",
            "relationship_type",
            "target_sku",
            name="uq_product_relationship_edge",
        ),
    )
    op.create_index(
        "ix_product_relationship_source_product_id",
        "product_relationship",
        ["source_product_id"],
    )
    op.create_index(
        "ix_product_relationship_target_sku",
        "product_relationship",
        ["target_sku"],
    )
    op.create_index(
        "ix_product_relationship_resolved_target_product_id",
        "product_relationship",
        ["resolved_target_product_id"],
    )
    op.create_index(
        "ix_product_relationship_relationship_type",
        "product_relationship",
        ["relationship_type"],
    )


def downgrade():
    op.drop_index(
        "ix_product_relationship_relationship_type",
        table_name="product_relationship",
    )
    op.drop_index(
        "ix_product_relationship_resolved_target_product_id",
        table_name="product_relationship",
    )
    op.drop_index(
        "ix_product_relationship_target_sku",
        table_name="product_relationship",
    )
    op.drop_index(
        "ix_product_relationship_source_product_id",
        table_name="product_relationship",
    )
    op.drop_table("product_relationship")
