from app import db
from flask_login import UserMixin
from datetime import datetime, date
from sqlalchemy.sql import func

# -------------------- Auth / Settings --------------------


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)


class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_folder = db.Column(db.String(512))
    output_folder = db.Column(db.String(512))
    url_prefix = db.Column(db.String(512))


# -------------------- Associations --------------------

product_categories = db.Table(
    "product_categories",
    db.Column("product_id", db.Integer, db.ForeignKey("product.id"), primary_key=True),
    db.Column(
        "category_id", db.Integer, db.ForeignKey("category.id"), primary_key=True
    ),
)

product_tags = db.Table(
    "product_tags",
    db.Column("product_id", db.Integer, db.ForeignKey("product.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


# -------------------- Taxonomy --------------------


class Category(db.Model):
    __tablename__ = "category"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(191), nullable=False, unique=True)
    slug = db.Column(db.String(191))
    woo_id = db.Column(db.Integer, index=True)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, onupdate=func.now())


class Tag(db.Model):
    __tablename__ = "tag"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(191), nullable=False, unique=True)
    slug = db.Column(db.String(191))
    woo_id = db.Column(db.Integer, index=True)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, onupdate=func.now())


# -------------------- Collections --------------------


class Collection(db.Model):
    __tablename__ = "collection"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(191), nullable=False)
    slug = db.Column(db.String(191), index=True)
    root_path = db.Column(db.String(1024), unique=True, nullable=False)
    sku_prefix = db.Column(db.String(64), unique=True, nullable=False)
    shared_json_path = db.Column(db.String(1024), nullable=False)

    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, onupdate=func.now())

    products = db.relationship("Product", backref="collection", lazy=True)


# -------------------- Core Product --------------------


class Product(db.Model):
    __tablename__ = "product"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    sku = db.Column(db.String(64), unique=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255))
    product_type = db.Column(
        db.String(20), default="simple"
    )  # simple | variable | external
    collection_type = db.Column(db.String(50))  # optional label you used

    # NEW: linking + filesystem paths
    collection_id = db.Column(db.Integer, db.ForeignKey("collection.id"), index=True)
    product_dir = db.Column(db.String(1024), index=True)  # absolute folder path
    shared_json_path = db.Column(db.String(1024))  # mirrors collection.shared_json_path
    override_json_path = db.Column(
        db.String(1024)
    )  # product_info.json in product folder (optional)
    effective_json_path = db.Column(db.String(1024))  # override if present else shared

    # Pricing (fallback defaults for variations)
    regular_price = db.Column(db.Numeric(10, 2))
    sale_price = db.Column(db.Numeric(10, 2))
    sale_start = db.Column(db.DateTime)
    sale_end = db.Column(db.DateTime)

    # Inventory (product-level)
    manage_stock = db.Column(db.Boolean, default=False)
    stock_quantity = db.Column(db.Integer)
    backorders = db.Column(db.String(20))  # no | notify | yes

    # Shipping / dimensions (fallback defaults)
    weight = db.Column(db.Numeric(10, 3))
    length = db.Column(db.Numeric(10, 3))
    width = db.Column(db.Numeric(10, 3))
    height = db.Column(db.Numeric(10, 3))
    shipping_class = db.Column(db.String(64))

    # Content
    short_description = db.Column(db.Text)
    description = db.Column(db.Text)

    # Merchandising / links
    external_url = db.Column(db.String(255))
    button_text = db.Column(db.String(100))
    upsell_ids = db.Column(db.Text)
    cross_sell_ids = db.Column(db.Text)

    # States / visibility
    status = db.Column(db.String(20), default="publish")
    catalog_visibility = db.Column(
        db.String(20), default="visible"
    )  # visible|catalog|search|hidden
    reviews_allowed = db.Column(db.Boolean, default=True)
    featured = db.Column(db.Boolean, default=False)

    # Primary image shortcut (gallery below holds multiple)
    image_url = db.Column(db.String(512))

    # Woo sync
    woo_id = db.Column(db.Integer, index=True)
    woo_synced_at = db.Column(db.DateTime)
    woo_updated_at = db.Column(db.DateTime)
    local_updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    images = db.relationship(
        "ProductImage",
        backref="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.position.asc()",
    )
    variations = db.relationship(
        "Variation",
        backref="product",
        cascade="all, delete-orphan",
        lazy=True,
    )
    assets = db.relationship(
        "ProductAsset",
        backref="product",
        cascade="all, delete-orphan",
        lazy=True,
        primaryjoin="Product.id==ProductAsset.product_id",
    )
    categories = db.relationship(
        "Category", secondary=product_categories, lazy="subquery"
    )
    tags = db.relationship("Tag", secondary=product_tags, lazy="subquery")


class ProductImage(db.Model):
    __tablename__ = "product_image"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False, index=True
    )
    url = db.Column(db.String(512), nullable=False)
    alt_text = db.Column(db.String(255))
    position = db.Column(db.Integer, default=0)  # 0-based
    woo_id = db.Column(db.Integer, index=True)  # if/when you map to Woo attachment id


# -------------------- Variations --------------------


class Variation(db.Model):
    __tablename__ = "variation"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False, index=True
    )

    sku = db.Column(db.String(64), unique=True, index=True)

    # Pricing overrides
    regular_price = db.Column(db.Numeric(10, 2))
    sale_price = db.Column(db.Numeric(10, 2))
    sale_start = db.Column(db.DateTime)
    sale_end = db.Column(db.DateTime)

    # Inventory
    manage_stock = db.Column(db.Boolean)
    stock_quantity = db.Column(db.Integer)
    backorders = db.Column(db.String(20))  # no | notify | yes

    # Dimensions overrides
    weight = db.Column(db.Numeric(10, 3))
    length = db.Column(db.Numeric(10, 3))
    width = db.Column(db.Numeric(10, 3))
    height = db.Column(db.Numeric(10, 3))

    image_url = db.Column(db.String(512))
    is_default = db.Column(db.Boolean, default=False)
    visible = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default="publish")
    menu_order = db.Column(db.Integer, default=0)

    # Woo sync
    woo_id = db.Column(db.Integer, index=True)
    woo_synced_at = db.Column(db.DateTime)
    woo_updated_at = db.Column(db.DateTime)
    local_updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    images = db.relationship(
        "VariationImage",
        backref="variation",
        cascade="all, delete-orphan",
        order_by="VariationImage.position.asc()",
    )
    attributes = db.relationship(
        "VariationAttribute",
        backref="variation",
        cascade="all, delete-orphan",
        lazy=True,
    )
    # variation-level assets (optional)
    assets = db.relationship(
        "ProductAsset",
        backref="variation",
        cascade="all, delete-orphan",
        lazy=True,
        primaryjoin="Variation.id==ProductAsset.variation_id",
    )


class VariationImage(db.Model):
    __tablename__ = "variation_image"
    id = db.Column(db.Integer, primary_key=True)
    variation_id = db.Column(
        db.Integer, db.ForeignKey("variation.id"), nullable=False, index=True
    )
    url = db.Column(db.String(512), nullable=False)
    alt_text = db.Column(db.String(255))
    position = db.Column(db.Integer, default=0)


class VariationAttribute(db.Model):
    __tablename__ = "variation_attribute"
    id = db.Column(db.Integer, primary_key=True)
    variation_id = db.Column(
        db.Integer, db.ForeignKey("variation.id"), nullable=False, index=True
    )
    name = db.Column(db.String(100), nullable=False)  # e.g. "Size" or "pa_size"
    value = db.Column(db.String(191), nullable=False)  # e.g. "A3"


# -------------------- Local assets --------------------


class ProductAsset(db.Model):
    __tablename__ = "product_asset"
    id = db.Column(db.Integer, primary_key=True)

    # Always belongs to a product
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False, index=True
    )

    # Optionally belongs to a variation
    variation_id = db.Column(
        db.Integer, db.ForeignKey("variation.id"), nullable=True, index=True
    )

    # Local filesystem path to file or folder
    path = db.Column(db.String(1024), nullable=False)

    # Useful metadata
    kind = db.Column(
        db.String(50), default="other"
    )  # mockup | svg | source | template | other
    label = db.Column(db.String(255))
    is_primary = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, server_default=func.now())


# -------------------- Extra model you had --------------------


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    type = db.Column(db.String(20))  # e.g. "Hosting", "Domain"
    renewal_date = db.Column(db.Date)
    auto_renew = db.Column(db.Boolean, default=True)
    notes = db.Column(db.String(255))
