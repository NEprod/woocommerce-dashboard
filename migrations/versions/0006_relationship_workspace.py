"""Add reconstructable relationship workspace metadata.

Revision ID: 0006_relationship_workspace
Revises: 0005_relationships
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_relationship_workspace"
down_revision = "0005_relationships"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("product") as batch:
        batch.add_column(sa.Column("relationship_source_kind", sa.String(16), nullable=True))
        batch.add_column(sa.Column("relationships_updated_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_product_relationship_source_kind", ["relationship_source_kind"])


def downgrade():
    with op.batch_alter_table("product") as batch:
        batch.drop_index("ix_product_relationship_source_kind")
        batch.drop_column("relationships_updated_at")
        batch.drop_column("relationship_source_kind")
