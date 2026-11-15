"""Reserve a non-physical ledger destination without rewriting any movements."""
from alembic import op

revision = "0023_unassigned_stock"
down_revision = "0022_inventory_foundation"
branch_labels = depends_on = None


def upgrade():
    # Normal identities are positive and cannot use this code. Never adopt or
    # merge a location based on its name, even if it is already Unassigned.
    op.execute("INSERT INTO inventory_identity (id, code, kind) VALUES (0, '~UNASSIGNED', 'system')")
    op.execute("""INSERT INTO inventory_location (id, name, description, active, version)
                  VALUES (0, 'Unassigned', 'Built-in stock without a physical location.', 1, 1)""")


def downgrade():
    raise RuntimeError("Unassigned stock may have ledger history. Restore a paired backup instead of downgrading.")
