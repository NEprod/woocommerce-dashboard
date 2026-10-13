"""Store-scoped controlled Shopify attribute resources; no source/backfill changes."""
from alembic import op
import sqlalchemy as sa
revision = "0012_shopify_attributes"
down_revision = "0011_shopify_taxonomy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("shopify_attribute_identity",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("store_id",sa.String(100),nullable=False),
        sa.Column("source_key",sa.String(300),nullable=False),
        sa.Column("remote_id",sa.String(100)),
        sa.Column("state",sa.String(32),nullable=False),
        sa.Column("operation_id",sa.String(32),sa.ForeignKey("catalogue_operation.id",ondelete="SET NULL")),
        sa.Column("evidence_json",sa.Text(),nullable=False),
        sa.Column("updated_at",sa.DateTime(),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint("store_id","source_key",name="uq_shopify_attribute_source"),
        sa.UniqueConstraint("store_id","remote_id",name="uq_shopify_attribute_remote"),
        sa.CheckConstraint("length(evidence_json) <= 16384",name="ck_shopify_attribute_evidence"))


def downgrade():
    op.drop_table("shopify_attribute_identity")
