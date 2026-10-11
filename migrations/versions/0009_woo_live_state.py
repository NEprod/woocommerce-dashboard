"""Optional whole-product verification evidence; no inferred/backfilled state."""
from alembic import op
import sqlalchemy as sa

revision = "0009_woo_live_state"
down_revision = "0008_woo_taxonomy_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("woo_product_identity", sa.Column("live_state_json", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("woo_product_identity") as batch:
        batch.drop_column("live_state_json")
