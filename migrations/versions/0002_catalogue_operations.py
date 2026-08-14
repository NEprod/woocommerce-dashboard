"""Add persistent catalogue operation history.

Revision ID: 0002_operations
Revises: 0001_phase0
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_operations"
down_revision = "0001_phase0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalogue_operation",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("scope", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("products_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("marker_state", sa.String(32), nullable=False, server_default="not_started"),
        sa.Column("recovery_state", sa.String(32), nullable=False, server_default="none"),
    )
    op.create_index(
        "ix_catalogue_operation_operation_type",
        "catalogue_operation",
        ["operation_type"],
    )
    op.create_index(
        "ix_catalogue_operation_status", "catalogue_operation", ["status"]
    )
    op.create_index(
        "ix_catalogue_operation_started_at", "catalogue_operation", ["started_at"]
    )
    op.create_table(
        "catalogue_operation_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.String(32),
            sa.ForeignKey("catalogue_operation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_path", sa.String(1024)),
        sa.Column("sku", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("database_state", sa.String(32), nullable=False, server_default="not_started"),
        sa.Column("marker_state", sa.String(32), nullable=False, server_default="not_started"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index(
        "ix_catalogue_operation_item_operation_id",
        "catalogue_operation_item",
        ["operation_id"],
    )
    op.create_index(
        "ix_catalogue_operation_item_status",
        "catalogue_operation_item",
        ["status"],
    )
    op.create_index(
        "ix_catalogue_operation_item_sku", "catalogue_operation_item", ["sku"]
    )


def downgrade():
    op.drop_index("ix_catalogue_operation_item_sku", table_name="catalogue_operation_item")
    op.drop_index("ix_catalogue_operation_item_status", table_name="catalogue_operation_item")
    op.drop_index(
        "ix_catalogue_operation_item_operation_id",
        table_name="catalogue_operation_item",
    )
    op.drop_table("catalogue_operation_item")
    op.drop_index("ix_catalogue_operation_started_at", table_name="catalogue_operation")
    op.drop_index("ix_catalogue_operation_status", table_name="catalogue_operation")
    op.drop_index(
        "ix_catalogue_operation_operation_type", table_name="catalogue_operation"
    )
    op.drop_table("catalogue_operation")
