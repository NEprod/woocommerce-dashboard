"""Derived publishing work/authority in the existing appdata database, not JSON authority."""
from app import db


class AutomaticPublishing(db.Model):
    __tablename__ = 'automatic_publishing'
    key = db.Column(db.String(32), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    revision = db.Column(db.Integer, nullable=False, default=0)
    config_digest = db.Column(db.String(64))
    store_id = db.Column(db.String(100))
    domain = db.Column(db.String(253))
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    required_fields_json = db.Column(db.Text, nullable=False, default='[]')
    updated_at = db.Column(db.DateTime)


class ShopifyPublishJob(db.Model):
    __tablename__ = 'shopify_publish_job'
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), primary_key=True)
    source_identity = db.Column(db.String(1024), nullable=False)
    requested_revision = db.Column(db.String(64), nullable=False)
    verified_revision = db.Column(db.String(64))
    running_revision = db.Column(db.String(64))
    generation = db.Column(db.Integer, nullable=False, default=1)
    state = db.Column(db.String(32), nullable=False, default='pending', index=True)
    trigger = db.Column(db.String(32), nullable=False, default='scan')
    priority = db.Column(db.Integer, nullable=False, default=0)
    owner = db.Column(db.String(100))
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_attempt = db.Column(db.DateTime, nullable=False, index=True)
    operation_id = db.Column(db.String(32), db.ForeignKey('catalogue_operation.id'))
    manual_actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    manual_revision = db.Column(db.String(64))
    manual_config = db.Column(db.String(64))
    error = db.Column(db.String(1000))
    updated_at = db.Column(db.DateTime, nullable=False)
    view_json = db.Column(db.Text)
    image_available = db.Column(db.Boolean)
    projected_at = db.Column(db.DateTime)
    view_revision = db.Column(db.String(64))
    view_pending = db.Column(db.Boolean, nullable=False, default=True, index=True)
    scan_operation_id = db.Column(db.String(32))


class ShopifyDueEvent(db.Model):
    __tablename__ = 'shopify_due_event'
    __table_args__ = (db.UniqueConstraint('product_id', 'event_type'),
                      db.Index('ix_shopify_due_pending', 'state', 'due_at'))
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    event_type = db.Column(db.String(32), nullable=False)
    schedule_revision = db.Column(db.String(64), nullable=False)
    snapshot_guard = db.Column(db.String(64), nullable=False)
    due_at = db.Column(db.DateTime, nullable=False)
    state = db.Column(db.String(32), nullable=False, default='pending')
    attempts = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.String(1000))
    operation_id = db.Column(db.String(32), db.ForeignKey('catalogue_operation.id'))


class ShopifyPublishingEvent(db.Model):
    __tablename__ = 'shopify_publishing_event'
    id = db.Column(db.String(80), primary_key=True)
    operation_id = db.Column(db.String(32), db.ForeignKey('catalogue_operation.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    event_type = db.Column(db.String(40), nullable=False)
    trigger = db.Column(db.String(32), nullable=False)
    local_revision = db.Column(db.String(64))
    occurred_at = db.Column(db.DateTime, nullable=False)
    receipt_json = db.Column(db.Text, nullable=False)
