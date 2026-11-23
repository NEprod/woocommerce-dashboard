"""Allow audited item barcode corrections, never identity/history rewrites."""
from alembic import op

revision = '0032_inventory_item_corrections'
down_revision = '0031_proof_attachment'
branch_labels = depends_on = None


def upgrade():
    op.execute('DROP TRIGGER inventory_identity_no_update')
    op.execute("""CREATE TRIGGER inventory_identity_no_update BEFORE UPDATE ON inventory_identity
        WHEN NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind
          OR (NEW.code IS NOT OLD.code AND (
            OLD.kind != 'item' OR NOT EXISTS (SELECT 1 FROM inventory_item WHERE id=OLD.id)
            OR NOT EXISTS (SELECT 1 FROM catalogue_operation
              WHERE operation_type='inventory_item_maintenance' AND status='succeeded'
              AND json_extract(scope,'$.action')='barcode_correction'
              AND json_extract(scope,'$.item_id')=OLD.id
              AND json_extract(scope,'$.before')=OLD.code
              AND json_extract(scope,'$.after')=NEW.code)))
        BEGIN SELECT RAISE(ABORT, 'Inventory identity changes require an audited item barcode correction'); END""")


def downgrade():
    op.execute('DROP TRIGGER inventory_identity_no_update')
    op.execute("""CREATE TRIGGER inventory_identity_no_update BEFORE UPDATE ON inventory_identity
        BEGIN SELECT RAISE(ABORT, 'Inventory history and barcode identities are immutable'); END""")
