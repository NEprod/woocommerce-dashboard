"""Shopify-owned commerce snapshots, separate from authored catalogue/production state."""
from datetime import datetime
from app import db


class ShopifyOrder(db.Model):
    __tablename__ = 'shopify_order'
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.String(96), nullable=False)
    remote_id = db.Column(db.String(96), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    api_version = db.Column(db.String(10), nullable=False)
    customer_id = db.Column(db.String(96))
    customer_name = db.Column(db.String(255))
    email = db.Column(db.String(320))
    phone = db.Column(db.String(100))
    delivery_json = db.Column(db.Text, nullable=False, default='null')
    financial_status = db.Column(db.String(40), nullable=False)
    fulfillment_status = db.Column(db.String(40), nullable=False)
    cancelled_at = db.Column(db.DateTime)
    cancel_reason = db.Column(db.String(100))
    test = db.Column(db.Boolean, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    total = db.Column(db.Numeric(18, 4), nullable=False)
    money_json = db.Column(db.Text, nullable=False)
    shipping_json = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    outstanding_quantity = db.Column(db.Integer, nullable=False)
    personalised = db.Column(db.Boolean, nullable=False, default=False)
    redacted_at = db.Column(db.DateTime)
    lines = db.relationship('ShopifyOrderLine', backref='order', lazy=True,
                            order_by='ShopifyOrderLine.position')
    __table_args__ = (
        db.UniqueConstraint('store_id', 'remote_id', name='uq_order_store_gid'),
        db.Index('ix_order_store_created', 'store_id', 'created_at', 'id'),
        db.Index('ix_order_store_status', 'store_id', 'test', 'fulfillment_status', 'financial_status'),
        db.Index('ix_order_name', 'name'),
        db.Index('ix_order_created', 'created_at', 'id'),
        db.Index('ix_order_operational', 'test', 'cancelled_at', 'financial_status', 'outstanding_quantity'),
        db.CheckConstraint('quantity >= 0 AND outstanding_quantity >= 0', name='ck_order_quantities'),
    )


class ShopifyOrderLine(db.Model):
    __tablename__ = 'shopify_order_line'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('shopify_order.id', ondelete='RESTRICT'), nullable=False)
    remote_id = db.Column(db.String(96), nullable=False)
    product_id = db.Column(db.String(96))
    variant_id = db.Column(db.String(96))
    sku = db.Column(db.String(255))
    title = db.Column(db.String(1024), nullable=False)
    variant_title = db.Column(db.String(1024))
    position = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    current_quantity = db.Column(db.Integer, nullable=False)
    unfulfilled_quantity = db.Column(db.Integer, nullable=False)
    refundable_quantity = db.Column(db.Integer, nullable=False)
    is_current = db.Column(db.Boolean, nullable=False, default=True)
    money_json = db.Column(db.Text, nullable=False)
    properties_json = db.Column(db.Text, nullable=False)
    personalisation_json = db.Column(db.Text, nullable=False)
    personalised = db.Column(db.Boolean, nullable=False, default=False)
    __table_args__ = (
        db.UniqueConstraint('order_id', 'remote_id', name='uq_order_line_gid'),
        db.Index('ix_order_line_sku', 'sku', 'order_id'),
        db.CheckConstraint('quantity >= 0 AND current_quantity >= 0 AND unfulfilled_quantity >= 0 AND refundable_quantity >= 0', name='ck_order_line_quantities'),
    )


class ShopifyOrderSync(db.Model):
    __tablename__ = 'shopify_order_sync'
    store_id = db.Column(db.String(96), primary_key=True)
    domain = db.Column(db.String(255), nullable=False)
    checkpoint = db.Column(db.DateTime)
    window_start = db.Column(db.DateTime)
    window_end = db.Column(db.DateTime)
    cursor = db.Column(db.Text)
    pending_json = db.Column(db.Text, nullable=False, default='[]')
    page_cursor = db.Column(db.Text)
    page_more = db.Column(db.Boolean, nullable=False, default=False)
    state = db.Column(db.String(32), nullable=False, default='not_started')
    attempted_at = db.Column(db.DateTime)
    successful_at = db.Column(db.DateTime)
    error = db.Column(db.String(500))
    imported = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.CheckConstraint('length(pending_json) <= 32768', name='ck_order_sync_bound'),)
