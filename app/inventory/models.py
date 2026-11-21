"""Local stock masters and immutable ledger. Quantities and GBP use 10⁻⁶ integers."""
from app import db


class Identity(db.Model):
    __tablename__ = "inventory_identity"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), nullable=False, unique=True)
    kind = db.Column(db.String(12), nullable=False)


class Item(db.Model):
    __tablename__ = "inventory_item"
    id = db.Column(db.Integer, db.ForeignKey("inventory_identity.id"), primary_key=True)
    identity = db.relationship(Identity, lazy="joined")
    kind = db.Column(db.String(12), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(100), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    specification = db.Column(db.String(250), nullable=False, default="")
    filament_type = db.Column(db.String(100), nullable=False, default="")
    filament_colour = db.Column(db.String(100), nullable=False, default="")
    spool_grams = db.Column(db.BigInteger)
    sheet_length = db.Column(db.BigInteger)
    sheet_width = db.Column(db.BigInteger)
    sheet_thickness = db.Column(db.BigInteger)
    unit = db.Column(db.String(8), nullable=False)
    pack_qty = db.Column(db.BigInteger)
    pack_unit = db.Column(db.String(8))
    pack_price = db.Column(db.BigInteger)
    supplier = db.Column(db.String(160), nullable=False, default="")
    supplier_sku = db.Column(db.String(100), nullable=False, default="")
    supplier_url = db.Column(db.String(500), nullable=False, default="")
    minimum = db.Column(db.BigInteger, nullable=False, default=0)
    reorder = db.Column(db.BigInteger)
    yield_qty = db.Column(db.Integer)
    default_location_id = db.Column(db.Integer, db.ForeignKey("inventory_location.id"))
    image = db.deferred(db.Column(db.LargeBinary))
    active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    materials = db.relationship("RecipeMaterial", foreign_keys="RecipeMaterial.component_id",
                                order_by="RecipeMaterial.id", cascade="all, delete-orphan")


class Location(db.Model):
    __tablename__ = "inventory_location"
    id = db.Column(db.Integer, db.ForeignKey("inventory_identity.id"), primary_key=True)
    identity = db.relationship(Identity, lazy="joined")
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    parent_id = db.Column(db.Integer, db.ForeignKey("inventory_location.id"))
    parent = db.relationship("Location", remote_side=[id])
    active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)


class RecipeMaterial(db.Model):
    __tablename__ = "inventory_recipe_material"
    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.Integer, db.ForeignKey("inventory_item.id"), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("inventory_item.id"), nullable=False)
    material = db.relationship(Item, foreign_keys=[material_id])
    qty = db.Column(db.BigInteger, nullable=False)
    unit = db.Column(db.String(8), nullable=False)
    __table_args__ = (db.UniqueConstraint("component_id", "material_id"), db.CheckConstraint("qty > 0"))


class ProductionBatch(db.Model):
    """Reserved manufacturing identity; planning never creates stock."""
    __tablename__ = "inventory_production_batch"
    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.Integer, db.ForeignKey("inventory_item.id"), nullable=False, index=True)
    component = db.relationship(Item)
    sequence = db.Column(db.Integer, nullable=False)
    code = db.Column(db.String(64), nullable=False, unique=True)
    planned_good = db.Column(db.Integer, nullable=False)
    planned_recipe = db.Column(db.Text, nullable=False)
    allocation_id = db.Column(db.String(32), nullable=False)
    allocation_ordinal = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="planned")
    accepted_good = db.Column(db.Integer)
    failed_qty = db.Column(db.Integer)
    receipt_id = db.Column(db.String(32), db.ForeignKey("inventory_transaction.id"), unique=True)
    created_at = db.Column(db.DateTime, nullable=False)
    __table_args__ = (db.UniqueConstraint("component_id", "sequence"),
                      db.UniqueConstraint("allocation_id", "allocation_ordinal"),
                      db.CheckConstraint("sequence > 0"), db.CheckConstraint("planned_good > 0"))


class CostingSettings(db.Model):
    __tablename__ = "inventory_costing_settings"
    id = db.Column(db.Integer, primary_key=True)
    document = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)


class Equipment(db.Model):
    __tablename__ = "inventory_equipment"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(100), nullable=False, default="")
    make_model = db.Column(db.String(160), nullable=False, default="")
    profile = db.Column(db.String(160), nullable=False, default="")
    watts = db.Column(db.String(32))
    source = db.Column(db.String(24), nullable=False, default="estimated")
    notes = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)


class BomScenario(db.Model):
    __tablename__ = "inventory_bom_scenario"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    document = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    variant_sku = db.Column(db.String(191))


class ProductCostingProfile(db.Model):
    """Current per-sellable planning assumptions; inventory lines remain in the product BOM."""
    __tablename__ = "inventory_product_costing_profile"
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), primary_key=True)
    sku = db.Column(db.String(191), primary_key=True)
    document = db.Column(db.Text, nullable=False)


class Transaction(db.Model):
    __tablename__ = "inventory_transaction"
    id = db.Column(db.String(32), primary_key=True)
    kind = db.Column(db.String(24), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory_item.id"), nullable=False)
    item = db.relationship(Item, lazy="joined")
    actor = db.Column(db.String(160), nullable=False)
    actor_label = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, index=True)
    reason = db.Column(db.String(400), nullable=False)
    details = db.Column(db.Text, nullable=False)
    request_hash = db.Column(db.String(64), nullable=False)
    reverses_id = db.Column(db.String(32), db.ForeignKey("inventory_transaction.id"), unique=True)
    moves = db.relationship("Movement", order_by="Movement.id", lazy="selectin")
    cost = db.Column(db.BigInteger)
    __table_args__ = (db.Index("ix_inventory_transaction_item_kind", "item_id", "kind"),)


class Movement(db.Model):
    __tablename__ = "inventory_movement"
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(32), db.ForeignKey("inventory_transaction.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory_item.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("inventory_production_batch.id"), index=True)
    item = db.relationship(Item, lazy="joined")
    location_id = db.Column(db.Integer, db.ForeignKey("inventory_location.id"), nullable=False)
    location = db.relationship(Location, lazy="joined")
    qty = db.Column(db.BigInteger, nullable=False)
    unit = db.Column(db.String(8), nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    cost = db.Column(db.BigInteger)
    __table_args__ = (db.CheckConstraint("qty != 0"),
                      db.Index("ix_inventory_movement_balance", "item_id", "location_id"),
                      db.Index("ix_inventory_movement_transaction", "transaction_id"))
