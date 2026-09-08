"""Store-scoped registry definition identities and uncertain-create reservations."""
from alembic import op
import sqlalchemy as sa

revision = "0008_woo_taxonomy_identity"
down_revision = "0007_woo_sync_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "woo_taxonomy_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(96), nullable=False),
        sa.Column("local_key", sa.String(96), nullable=False),
        sa.Column("remote_scope", sa.Integer(), nullable=False),
        sa.Column("woo_id", sa.Integer()),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("local_digest", sa.String(64), nullable=False),
        sa.Column("remote_digest", sa.String(64)),
        sa.Column("verified_at", sa.DateTime()),
        sa.UniqueConstraint("store_key", "kind", "scope_key", "local_key", name="uq_taxonomy_local"),
        sa.UniqueConstraint("store_key", "kind", "remote_scope", "woo_id", name="uq_taxonomy_remote"),
    )


def downgrade():
    op.drop_table("woo_taxonomy_identity")
