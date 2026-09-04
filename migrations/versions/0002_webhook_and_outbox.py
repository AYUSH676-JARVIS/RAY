"""0002_webhook_and_outbox

Revision ID: 0002_webhook_and_outbox
Revises: 0001_initial_schema
Create Date: 2026-09-03 02:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0002_webhook_and_outbox'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Webhook Deliveries
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('gateway_name', sa.String(50), nullable=False),
        sa.Column('event_id', sa.String(255), nullable=False),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('signature', sa.String(255), nullable=True),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='RECEIVED'),
        sa.Column('error_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('gateway_name', 'event_id', name='uq_webhook_gateway_event'),
    )
    op.create_index('ix_webhook_deliveries_merchant_id', 'webhook_deliveries', ['merchant_id'])
    op.create_index('ix_webhook_deliveries_event_id', 'webhook_deliveries', ['event_id'])
    op.create_index('ix_webhook_deliveries_event_type', 'webhook_deliveries', ['event_type'])
    op.create_index('ix_webhook_deliveries_created_at', 'webhook_deliveries', ['created_at'])

    # 2. Outbox Events
    op.create_table(
        'outbox_events',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('aggregate_type', sa.String(100), nullable=False),
        sa.Column('aggregate_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='PENDING'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_outbox_events_merchant_id', 'outbox_events', ['merchant_id'])
    op.create_index('ix_outbox_events_aggregate_type', 'outbox_events', ['aggregate_type'])
    op.create_index('ix_outbox_events_aggregate_id', 'outbox_events', ['aggregate_id'])
    op.create_index('ix_outbox_events_event_type', 'outbox_events', ['event_type'])
    op.create_index('ix_outbox_events_status', 'outbox_events', ['status'])
    op.create_index('ix_outbox_events_created_at', 'outbox_events', ['created_at'])

    # 3. System Safety Controls (Distributed Kill-Switch & Stage 2 Activation)
    op.create_table(
        'system_safety_controls',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('scope', sa.String(50), nullable=False, server_default='GLOBAL'),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('mode', sa.String(50), nullable=False, server_default='STAGE1_SAFETY'),
        sa.Column('is_live_authorized', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('kill_switch_engaged', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('kill_switch_engaged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('kill_switch_engaged_by', sa.String(255), nullable=True),
        sa.Column('kill_switch_reason', sa.Text(), nullable=True),
        sa.Column('single_transaction_cap', sa.Numeric(12, 2), nullable=False, server_default='5000.00'),
        sa.Column('daily_volume_cap', sa.Numeric(12, 2), nullable=False, server_default='25000.00'),
        sa.Column('current_daily_volume', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint('scope', name='uq_system_safety_scope'),
    )
    op.create_index('ix_system_safety_controls_scope', 'system_safety_controls', ['scope'])
    op.create_index('ix_system_safety_controls_merchant_id', 'system_safety_controls', ['merchant_id'])


def downgrade() -> None:
    op.drop_table('system_safety_controls')
    op.drop_table('outbox_events')
    op.drop_table('webhook_deliveries')
