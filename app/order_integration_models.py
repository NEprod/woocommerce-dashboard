"""Store-bound webhook configuration and independently authorised status sync."""
from app import db


class OrderWebhookSetting(db.Model):
    __tablename__='order_webhook_setting'
    id=db.Column(db.Integer,primary_key=True)
    store_id=db.Column(db.String(96),nullable=False)
    topic=db.Column(db.String(40),nullable=False)
    name=db.Column(db.String(120),nullable=False)
    endpoint=db.Column(db.String(500),nullable=False)
    enabled=db.Column(db.Boolean,nullable=False,default=True)
    version=db.Column(db.Integer,nullable=False,default=1)
    remote_id=db.Column(db.String(96),unique=True)
    remote_json=db.Column(db.Text,nullable=False,default='{}')
    state=db.Column(db.String(32),nullable=False,default='local_only')
    error=db.Column(db.String(400),nullable=False,default='')
    verified_at=db.Column(db.DateTime)
    operation_until=db.Column(db.DateTime)
    __table_args__=(db.UniqueConstraint('store_id','topic','endpoint'),)


class OrderIntegrationSettings(db.Model):
    __tablename__='order_integration_settings'
    store_id=db.Column(db.String(96),primary_key=True)
    projection_enabled=db.Column(db.Boolean,nullable=False,default=False)
    fingerprint=db.Column(db.String(64),nullable=False)
    actor=db.Column(db.String(160),nullable=False)
    authorised_at=db.Column(db.DateTime,nullable=False)


class OrderStatusProjection(db.Model):
    __tablename__='order_status_projection'
    order_id=db.Column(db.Integer,db.ForeignKey('shopify_order.id'),primary_key=True)
    value=db.Column(db.String(120))
    state=db.Column(db.String(32),nullable=False,default='pending')
    error=db.Column(db.String(400),nullable=False,default='')
    attempts=db.Column(db.Integer,nullable=False,default=0)
    verified_at=db.Column(db.DateTime)
    next_attempt=db.Column(db.DateTime)
    owner=db.Column(db.String(32))
    lease_until=db.Column(db.DateTime)
