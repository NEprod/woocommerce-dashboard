"""Reserve durable per-component manufacturing batch identities."""
import sqlalchemy as sa
from alembic import op

revision = "0027_production_batches"
down_revision = "0026_image_fingerprints"
branch_labels = depends_on = None


def upgrade():
    op.add_column('inventory_item', sa.Column('default_location_id', sa.Integer()))
    op.create_table("inventory_product_costing_profile",
                    sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id"), primary_key=True),
                    sa.Column("sku", sa.String(191), primary_key=True), sa.Column("document", sa.Text(), nullable=False))
    op.create_table("inventory_costing_settings", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("document", sa.Text(), nullable=False), sa.Column("version", sa.Integer(), nullable=False))
    op.create_table("inventory_equipment", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("name", sa.String(160), nullable=False), sa.Column('category', sa.String(100), nullable=False),
                    sa.Column('make_model', sa.String(160), nullable=False), sa.Column("profile", sa.String(160), nullable=False),
                    sa.Column("watts", sa.String(32)), sa.Column("source", sa.String(24), nullable=False),
                    sa.Column("notes", sa.Text(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False),
                    sa.Column("version", sa.Integer(), nullable=False))
    equipment = sa.table('inventory_equipment', sa.column('name'), sa.column('category'), sa.column('make_model'), sa.column('profile'), sa.column('watts'),
                         sa.column('source'), sa.column('notes'), sa.column('active'), sa.column('version'))
    # This table is new; no existing operator records can be overwritten.
    op.bulk_insert(equipment, [dict(name=name, category='', make_model=name, profile=profile, watts=watts, source='estimated',
        notes='Provisional planning profile supplied for this milestone; not a measured electrical reading.', active=True, version=1)
        for name, profile, watts in [('Bambu Lab X2D','PLA provisional','250'),
                                    ('Bambu Lab X2D','Higher-temperature PC provisional','550'),
                                    ('Epson EcoTank ET-8550','Copying proxy provisional','17'),
                                    ('CO2 laser cutter','Electrical draw unknown; optical rating is not electrical draw',None),
                                    ('CW-5200 chiller','Unverified',None), ('Laser extractor fan','Unverified',None),
                                    ('Aquarium air-assist pump','Unverified',None)]])
    op.create_table("inventory_bom_scenario", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("name", sa.String(160), nullable=False), sa.Column("document", sa.Text(), nullable=False),
                    sa.Column("version", sa.Integer(), nullable=False),
                    sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id")), sa.Column("variant_sku", sa.String(191)))
    op.create_table(
        "inventory_production_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("component_id", sa.Integer(), sa.ForeignKey("inventory_item.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("planned_good", sa.Integer(), nullable=False),
        sa.Column("planned_recipe", sa.Text(), nullable=False),
        sa.Column("allocation_id", sa.String(32), nullable=False),
        sa.Column("allocation_ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("accepted_good", sa.Integer()),
        sa.Column("failed_qty", sa.Integer()),
        sa.Column("receipt_id", sa.String(32), sa.ForeignKey("inventory_transaction.id"), unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("component_id", "sequence"),
        sa.UniqueConstraint("allocation_id", "allocation_ordinal"),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("planned_good > 0"),
    )
    op.create_index("ix_inventory_production_batch_component_id", "inventory_production_batch", ["component_id"])
    # SQLite cannot ALTER an existing ledger table to add a foreign key without
    # rebuilding it, which would disturb its immutable-history triggers.
    op.add_column("inventory_movement", sa.Column("batch_id", sa.Integer()))
    op.create_index("ix_inventory_movement_batch_id", "inventory_movement", ["batch_id"])
    op.execute("""CREATE TRIGGER inventory_movement_valid_batch BEFORE INSERT ON inventory_movement
                WHEN NEW.batch_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM inventory_production_batch WHERE id=NEW.batch_id AND component_id=NEW.item_id)
                BEGIN SELECT RAISE(ABORT, 'Batch movements must reference their exact component'); END""")
    op.execute("""CREATE TRIGGER inventory_production_batch_identity_no_update
                BEFORE UPDATE ON inventory_production_batch
                WHEN NEW.code != OLD.code OR NEW.component_id != OLD.component_id OR NEW.sequence != OLD.sequence
                  OR NEW.planned_good != OLD.planned_good OR NEW.planned_recipe != OLD.planned_recipe
                  OR NEW.allocation_id != OLD.allocation_id OR NEW.allocation_ordinal != OLD.allocation_ordinal
                BEGIN SELECT RAISE(ABORT, 'Production batch identity is permanent'); END""")
    op.execute("""CREATE TRIGGER inventory_production_batch_identity_no_delete
                BEFORE DELETE ON inventory_production_batch
                BEGIN SELECT RAISE(ABORT, 'Production batch identity is permanent'); END""")


def downgrade():
    raise RuntimeError("Production batch identities are permanent; restore a paired backup instead of dropping them.")
