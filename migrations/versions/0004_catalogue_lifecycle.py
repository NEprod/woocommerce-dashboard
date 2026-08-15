"""Add catalogue lifecycle and reconciliation history.

Revision ID: 0004_lifecycle
Revises: 0003_projection
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_lifecycle"
down_revision = "0003_projection"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("product", "variation"):
        op.add_column(
            table,
            sa.Column(
                "catalogue_status",
                sa.String(20),
                nullable=False,
                server_default="active",
            ),
        )
        op.add_column(table, sa.Column("missing_at", sa.DateTime()))
        op.add_column(table, sa.Column("restored_at", sa.DateTime()))
        op.create_index(f"ix_{table}_catalogue_status", table, ["catalogue_status"])

    op.add_column("variation", sa.Column("source_identity", sa.String(1024)))
    op.create_index(
        "ix_variation_source_identity", "variation", ["source_identity"]
    )

    for column in (
        "products_missing",
        "products_restored",
        "variations_missing",
        "variations_restored",
    ):
        op.add_column(
            "catalogue_operation",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )

    op.add_column(
        "catalogue_operation_item",
        sa.Column(
            "product_restored", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "catalogue_operation_item",
        sa.Column(
            "variations_missing", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "catalogue_operation_item",
        sa.Column(
            "variations_restored", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade():
    op.drop_column("catalogue_operation_item", "variations_restored")
    op.drop_column("catalogue_operation_item", "variations_missing")
    op.drop_column("catalogue_operation_item", "product_restored")

    for column in (
        "variations_restored",
        "variations_missing",
        "products_restored",
        "products_missing",
    ):
        op.drop_column("catalogue_operation", column)

    op.drop_index("ix_variation_source_identity", table_name="variation")
    op.drop_column("variation", "source_identity")
    for table in ("variation", "product"):
        op.drop_index(f"ix_{table}_catalogue_status", table_name=table)
        op.drop_column(table, "restored_at")
        op.drop_column(table, "missing_at")
        op.drop_column(table, "catalogue_status")
