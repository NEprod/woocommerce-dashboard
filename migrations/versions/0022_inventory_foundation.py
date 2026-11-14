"""Workshop-only stock masters, recipes and immutable inventory ledger."""
import sqlalchemy as sa
from alembic import op

revision = "0022_inventory_foundation"
down_revision = "0021_manual_review_worker"
branch_labels = depends_on = None


def upgrade():
    def fk(name, table, *, pk=False, nullable=None, unique=False):
        return sa.Column(name, sa.Integer(), sa.ForeignKey(table+".id"), primary_key=pk,
                         nullable=(not pk if nullable is None else nullable), unique=unique)

    def string(name, size, *, nullable=False):
        return sa.Column(name, sa.String(size), nullable=nullable)

    def number(name, *, nullable=True):
        return sa.Column(name, sa.BigInteger(), nullable=nullable)

    op.create_table("inventory_identity", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("code", sa.String(24), unique=True, nullable=False), string("kind", 12))
    op.create_table("inventory_item", fk("id", "inventory_identity", pk=True),
                    string("kind", 12), string("name", 160), string("category", 100),
                    sa.Column("description", sa.Text(), nullable=False), string("specification", 250),
                    string("filament_type", 100), string("filament_colour", 100), number("spool_grams"),
                    number("sheet_length"), number("sheet_width"), number("sheet_thickness"),
                    string("unit", 8), number("pack_qty"), string("pack_unit", 8, nullable=True), number("pack_price"),
                    string("supplier", 160), string("supplier_sku", 100), string("supplier_url", 500),
                    number("minimum", nullable=False), number("reorder"),
                    sa.Column("yield_qty", sa.Integer()), sa.Column("image", sa.LargeBinary()),
                    sa.Column("active", sa.Boolean(), nullable=False), sa.Column("version", sa.Integer(), nullable=False))
    op.create_index("ix_inventory_item_kind", "inventory_item", ["kind"])
    op.create_table("inventory_location", fk("id", "inventory_identity", pk=True), string("name", 160),
                    sa.Column("description", sa.Text(), nullable=False), fk("parent_id", "inventory_location"),
                    sa.Column("active", sa.Boolean(), nullable=False), sa.Column("version", sa.Integer(), nullable=False))
    op.create_table("inventory_recipe_material", sa.Column("id", sa.Integer(), primary_key=True),
                    fk("component_id", "inventory_item", nullable=False), fk("material_id", "inventory_item", nullable=False),
                    number("qty", nullable=False), string("unit", 8),
                    sa.UniqueConstraint("component_id", "material_id"), sa.CheckConstraint("qty > 0"))
    op.create_table("inventory_transaction", sa.Column("id", sa.String(32), primary_key=True),
                    string("kind", 24), fk("item_id", "inventory_item", nullable=False), string("actor", 160), string("actor_label", 160),
                    sa.Column("created_at", sa.DateTime(), nullable=False), string("reason", 400),
                    sa.Column("details", sa.Text(), nullable=False), string("request_hash", 64),
                    sa.Column("reverses_id", sa.String(32), sa.ForeignKey("inventory_transaction.id"), unique=True),
                    number("cost"))
    op.create_index("ix_inventory_transaction_created_at", "inventory_transaction", ["created_at"])
    op.create_index("ix_inventory_transaction_item_kind", "inventory_transaction", ["item_id", "kind"])
    op.create_table("inventory_movement", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("transaction_id", sa.String(32), sa.ForeignKey("inventory_transaction.id"), nullable=False),
                    fk("item_id", "inventory_item", nullable=False), fk("location_id", "inventory_location", nullable=False),
                    number("qty", nullable=False), string("unit", 8), string("kind", 24), number("cost"),
                    sa.CheckConstraint("qty != 0"))
    op.create_index("ix_inventory_movement_balance", "inventory_movement", ["item_id", "location_id"])
    op.create_index("ix_inventory_movement_transaction", "inventory_movement", ["transaction_id"])
    for table in ("inventory_transaction", "inventory_movement", "inventory_identity"):
        for verb in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'Inventory history and barcode identities are immutable'); END""")


def downgrade():
    raise RuntimeError("Inventory contains durable identities and movements. Restore a paired backup; no destructive downgrade.")
