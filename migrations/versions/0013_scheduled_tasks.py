"""Persistent task runs, SQLite ownership and shared Shopify capacity; no catalogue backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0013_scheduled_tasks"
down_revision = "0012_shopify_attributes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scheduled_task",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_due", sa.DateTime(), nullable=False),
        *[sa.Column(name, sa.DateTime()) for name in ("last_scheduled", "last_started", "last_completed", "last_successful")],
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_outcome", sa.String(32)), sa.Column("duration_seconds", sa.Float()),
        sa.Column("active_run_id", sa.String(32)), sa.Column("owner", sa.String(32)),
        sa.Column("lease_until", sa.DateTime()), sa.Column("resume_at", sa.DateTime()),
        sa.Column("selection_cursor", sa.Integer(), nullable=False),
        sa.CheckConstraint("interval_minutes BETWEEN 1 AND 1440", name="ck_task_interval"))
    op.create_table("scheduled_task_run",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_key", sa.String(64), sa.ForeignKey("scheduled_task.key"), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False), sa.Column("state", sa.String(32), nullable=False),
        *[sa.Column(name, sa.DateTime(), nullable=name != "created_at") for name in ("created_at", "started_at", "completed_at")],
        sa.Column("duration_seconds", sa.Float()), sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.CheckConstraint("length(data_json) <= 65536", name="ck_task_run_bound"))
    op.create_index("ix_scheduled_task_run_task_key", "scheduled_task_run", ["task_key"])
    op.create_table("shopify_api_capacity", sa.Column("key", sa.String(64), primary_key=True),
        *[sa.Column(name, sa.Float()) for name in ("available", "maximum", "restore_rate", "requested_cost")],
        sa.Column("observed_at", sa.DateTime()), sa.Column("priority_until", sa.DateTime()),
        sa.Column("priority",sa.Integer(),nullable=False,server_default="0"))
    op.execute(sa.text("INSERT INTO scheduled_task (key, enabled, interval_minutes, next_due, state, selection_cursor) "
                       "VALUES ('shopify_products', 1, 20, datetime('now', '+20 minutes'), 'idle', 0)"))


def downgrade():
    op.drop_table("shopify_api_capacity")
    op.drop_table("scheduled_task_run")
    op.drop_table("scheduled_task")
