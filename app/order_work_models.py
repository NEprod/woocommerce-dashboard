"""Local operational records attached to the existing Shopify commerce graph."""
from app import db


class WorkPrint(db.Model):
    """Explicit printing milestone, separate from production state and stock."""
    __tablename__ = 'order_work_print'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('shopify_order.id'), nullable=False, index=True)
    request_id = db.Column(db.String(32), nullable=False)
    selection_digest = db.Column(db.String(64), nullable=False)
    actor = db.Column(db.String(160), nullable=False)
    at = db.Column(db.DateTime, nullable=False)
    __table_args__ = (db.UniqueConstraint('request_id','order_id'),)


class OrderWorkLine(db.Model):
    __tablename__ = 'order_work_line'
    line_id = db.Column(db.Integer, db.ForeignKey('shopify_order_line.id'), primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('shopify_order.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    state = db.Column(db.String(40), nullable=False)
    policy_json = db.Column(db.Text, nullable=False)
    bom_json = db.Column(db.Text, nullable=False)
    source_digest = db.Column(db.String(64), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    proof_required = db.Column(db.Boolean, nullable=False)
    committed_at = db.Column(db.DateTime)
    committed_by = db.Column(db.String(160))
    hold_reason = db.Column(db.String(400), nullable=False, default='')
    prior_state = db.Column(db.String(40))


class WorkRequirement(db.Model):
    __tablename__ = 'order_work_requirement'
    id = db.Column(db.Integer, primary_key=True)
    line_id = db.Column(db.Integer, db.ForeignKey('order_work_line.line_id'), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_item.id'), nullable=False, index=True)
    quantity = db.Column(db.BigInteger, nullable=False)
    reserved = db.Column(db.BigInteger, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)
    unit = db.Column(db.String(8), nullable=False)
    __table_args__ = (db.UniqueConstraint('line_id','item_id'),
        db.CheckConstraint('quantity > 0 AND reserved >= 0 AND reserved <= quantity'))


class WorkEvent(db.Model):
    __tablename__ = 'order_work_event'
    id = db.Column(db.String(32), primary_key=True)
    line_id = db.Column(db.Integer, db.ForeignKey('order_work_line.line_id'), nullable=False, index=True)
    action = db.Column(db.String(40), nullable=False)
    previous = db.Column(db.String(40), nullable=False)
    state = db.Column(db.String(40), nullable=False)
    actor = db.Column(db.String(160), nullable=False)
    at = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(400), nullable=False)
    details = db.Column(db.Text, nullable=False)
    digest = db.Column(db.String(64), nullable=False)


class WorkProof(db.Model):
    __tablename__ = 'order_work_proof'
    id = db.Column(db.Integer, primary_key=True)
    line_id = db.Column(db.Integer, db.ForeignKey('order_work_line.line_id'), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(32), nullable=False)
    method = db.Column(db.String(100), nullable=False, default='')
    evidence = db.Column(db.String(400), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    actor = db.Column(db.String(160), nullable=False)
    file_data = db.Column(db.LargeBinary)
    __table_args__ = (db.UniqueConstraint('line_id','version'),)


class WorkPick(db.Model):
    __tablename__ = 'order_work_pick'
    id = db.Column(db.String(32), db.ForeignKey('order_work_event.id'), primary_key=True)
    requirement_id = db.Column(db.Integer, db.ForeignKey('order_work_requirement.id'), nullable=False, index=True)
    transaction_id = db.Column(db.String(32), db.ForeignKey('inventory_transaction.id'), nullable=False, unique=True)
    quantity = db.Column(db.BigInteger, nullable=False)
    reverses_id = db.Column(db.String(32), db.ForeignKey('order_work_pick.id'), unique=True)
    __table_args__ = (db.CheckConstraint('quantity != 0'),)


class WorkPlan(db.Model):
    __tablename__ = 'order_work_plan'
    id = db.Column(db.String(32), primary_key=True)
    requirement_id = db.Column(db.Integer, db.ForeignKey('order_work_requirement.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_item.id'), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    quantity = db.Column(db.BigInteger, nullable=False)
    document = db.Column(db.Text, nullable=False)
    state = db.Column(db.String(20), nullable=False)
    actor = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    digest = db.Column(db.String(64), nullable=False)


class WorkOutbound(db.Model):
    __tablename__ = 'order_work_outbound'
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(32), db.ForeignKey('order_work_event.id'), nullable=False)
    channel = db.Column(db.String(16), nullable=False)
    state = db.Column(db.String(20), nullable=False, default='disabled')
    attempts = db.Column(db.Integer, nullable=False, default=0)
    payload = db.Column(db.Text, nullable=False)
    error = db.Column(db.String(400), nullable=False, default='')
    delivered_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint('event_id','channel'),)
