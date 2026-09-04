"""0003_task_queue

Revision ID: 0003_task_queue
Revises: 0002_webhook_and_outbox
Create Date: 2026-09-04 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0003_task_queue'
down_revision = '0002_webhook_and_outbox'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Durable Task Queue table
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    
    if 'task_queue' not in tables:
        op.create_table(
            'task_queue',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
            sa.Column('task_name', sa.String(128), nullable=False),
            sa.Column('payload_json', sa.Text(), nullable=False),
            sa.Column('status', sa.String(32), nullable=False, server_default='PENDING'),
            sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('max_retries', sa.Integer(), nullable=False, server_default='5'),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
            sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        )
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('task_queue')} if 'task_queue' in tables or 'task_queue' not in tables else set()
    for idx_name, cols in [
        ('ix_task_queue_merchant_id', ['merchant_id']),
        ('ix_task_queue_task_name', ['task_name']),
        ('ix_task_queue_status', ['status']),
        ('ix_task_queue_priority', ['priority']),
        ('ix_task_queue_scheduled_for', ['scheduled_for']),
    ]:
        if idx_name not in existing_indexes:
            op.create_index(idx_name, 'task_queue', cols)


def downgrade() -> None:
    op.drop_table('task_queue')
