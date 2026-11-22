"""One global test-order policy and explicit immutable print milestones."""
from alembic import op
import sqlalchemy as s
revision='0030_order_workflow'
down_revision='0029_order_integrations'
branch_labels=depends_on=None


def upgrade():
    op.add_column('settings',s.Column('include_test_orders',s.Boolean(),nullable=True))
    op.create_table('order_work_print',s.Column('id',s.Integer(),primary_key=True),
        s.Column('order_id',s.Integer(),s.ForeignKey('shopify_order.id'),nullable=False),
        s.Column('request_id',s.String(32),nullable=False),s.Column('selection_digest',s.String(64),nullable=False),
        s.Column('actor',s.String(160),nullable=False),s.Column('at',s.DateTime(),nullable=False),
        s.UniqueConstraint('request_id','order_id'))
    op.create_index('ix_order_work_print_order_id','order_work_print',['order_id'])
    for action in ('UPDATE','DELETE'):
        op.execute(f"CREATE TRIGGER order_work_print_no_{action.lower()} BEFORE {action} ON order_work_print BEGIN SELECT RAISE(ABORT,'Print history is append-only'); END")


def downgrade():
    raise RuntimeError('Retained print history requires a paired backup; destructive downgrade refused.')
