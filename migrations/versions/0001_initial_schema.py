"""0001_initial_schema

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-02 06:30:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Merchants
    op.create_table(
        'merchants',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('status', sa.String(50), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_merchants_slug', 'merchants', ['slug'], unique=True)

    # 2. Customers
    op.create_table(
        'customers',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('risk_score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('risk_score >= 0.0 AND risk_score <= 1.0', name='chk_customer_risk_score'),
    )
    op.create_index('ix_customers_merchant_id', 'customers', ['merchant_id'])
    op.create_index('ix_customers_external_id', 'customers', ['external_id'])
    op.create_index('ix_customers_email', 'customers', ['email'])

    # 3. Orders
    op.create_table(
        'orders',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('customer_id', sa.Uuid(as_uuid=True), sa.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('status', sa.String(50), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('amount >= 0.0', name='chk_order_amount_non_negative'),
    )
    op.create_index('ix_orders_merchant_id', 'orders', ['merchant_id'])
    op.create_index('ix_orders_customer_id', 'orders', ['customer_id'])

    # 4. Payments
    op.create_table(
        'payments',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('order_id', sa.Uuid(as_uuid=True), sa.ForeignKey('orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('customer_id', sa.Uuid(as_uuid=True), sa.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('status', sa.String(50), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('amount >= 0.0', name='chk_payment_amount_non_negative'),
    )
    op.create_index('ix_payments_merchant_id', 'payments', ['merchant_id'])
    op.create_index('ix_payments_order_id', 'payments', ['order_id'])
    op.create_index('ix_payments_customer_id', 'payments', ['customer_id'])

    # 5. Payment Attempts
    op.create_table(
        'payment_attempts',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('payment_id', sa.Uuid(as_uuid=True), sa.ForeignKey('payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('gateway_name', sa.String(100), nullable=False),
        sa.Column('gateway_transaction_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_payment_attempts_merchant_id', 'payment_attempts', ['merchant_id'])
    op.create_index('ix_payment_attempts_payment_id', 'payment_attempts', ['payment_id'])
    op.create_index('ix_payment_attempts_idempotency_key', 'payment_attempts', ['idempotency_key'], unique=True)
    op.create_index('ix_payment_attempts_payment_attempt_num', 'payment_attempts', ['payment_id', 'attempt_number'])

    # 6. Payment Failures
    op.create_table(
        'payment_failures',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('payment_id', sa.Uuid(as_uuid=True), sa.ForeignKey('payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('payment_attempt_id', sa.Uuid(as_uuid=True), sa.ForeignKey('payment_attempts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('failure_code', sa.String(100), nullable=False),
        sa.Column('raw_message', sa.Text(), nullable=False),
        sa.Column('is_retryable', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_payment_failures_merchant_id', 'payment_failures', ['merchant_id'])
    op.create_index('ix_payment_failures_payment_id', 'payment_failures', ['payment_id'])
    op.create_index('ix_payment_failures_attempt_id', 'payment_failures', ['payment_attempt_id'])
    op.create_index('ix_payment_failures_failure_code', 'payment_failures', ['failure_code'])

    # 7. Recovery Opportunities
    op.create_table(
        'recovery_opportunities',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('payment_id', sa.Uuid(as_uuid=True), sa.ForeignKey('payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('failure_id', sa.Uuid(as_uuid=True), sa.ForeignKey('payment_failures.id', ondelete='CASCADE'), nullable=False),
        sa.Column('strategy_name', sa.String(100), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('estimated_recoverable_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='OPEN'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_recovery_opportunities_merchant_id', 'recovery_opportunities', ['merchant_id'])
    op.create_index('ix_recovery_opportunities_payment_id', 'recovery_opportunities', ['payment_id'])
    op.create_index('ix_recovery_opportunities_failure_id', 'recovery_opportunities', ['failure_id'])
    op.create_index('ix_recovery_opportunities_status', 'recovery_opportunities', ['status'])

    # 8. Recovery Actions
    op.create_table(
        'recovery_actions',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('opportunity_id', sa.Uuid(as_uuid=True), sa.ForeignKey('recovery_opportunities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_type', sa.String(100), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=False),
        sa.Column('execution_status', sa.String(50), nullable=False, server_default='BLOCKED_STAGE1_SAFETY'),
        sa.Column('parameters_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_recovery_actions_merchant_id', 'recovery_actions', ['merchant_id'])
    op.create_index('ix_recovery_actions_opportunity_id', 'recovery_actions', ['opportunity_id'])
    op.create_index('ix_recovery_actions_idempotency_key', 'recovery_actions', ['idempotency_key'], unique=True)

    # 9. Policy Decisions
    op.create_table(
        'policy_decisions',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('opportunity_id', sa.Uuid(as_uuid=True), sa.ForeignKey('recovery_opportunities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_id', sa.Uuid(as_uuid=True), sa.ForeignKey('recovery_actions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decision', sa.String(50), nullable=False),
        sa.Column('rule_matched', sa.String(255), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('risk_score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('risk_score >= 0.0 AND risk_score <= 1.0', name='chk_policy_decision_risk_score'),
    )
    op.create_index('ix_policy_decisions_merchant_id', 'policy_decisions', ['merchant_id'])
    op.create_index('ix_policy_decisions_opportunity_id', 'policy_decisions', ['opportunity_id'])
    op.create_index('ix_policy_decisions_action_id', 'policy_decisions', ['action_id'])

    # 10. Agent Runs
    op.create_table(
        'agent_runs',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('trigger_type', sa.String(100), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='RUNNING'),
        sa.Column('tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False, server_default='0.000000'),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_runs_merchant_id', 'agent_runs', ['merchant_id'])
    op.create_index('ix_agent_runs_agent_name', 'agent_runs', ['agent_name'])

    # 11. Tool Calls
    op.create_table(
        'tool_calls',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('run_id', sa.Uuid(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tool_name', sa.String(100), nullable=False),
        sa.Column('input_payload_json', sa.JSON(), nullable=True),
        sa.Column('output_payload_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tool_calls_merchant_id', 'tool_calls', ['merchant_id'])
    op.create_index('ix_tool_calls_run_id', 'tool_calls', ['run_id'])

    # 12. Audit Events
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('merchant_id', sa.Uuid(as_uuid=True), sa.ForeignKey('merchants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('sequence_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('run_id', sa.Uuid(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('entity_type', sa.String(100), nullable=False),
        sa.Column('entity_id', sa.Uuid(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('actor_type', sa.String(50), nullable=False),
        sa.Column('actor_id', sa.String(255), nullable=False),
        sa.Column('payload_before_json', sa.JSON(), nullable=True),
        sa.Column('payload_after_json', sa.JSON(), nullable=True),
        sa.Column('previous_event_hash', sa.String(64), nullable=True),
        sa.Column('event_hash', sa.String(64), nullable=False, server_default='0' * 64),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('merchant_id', 'sequence_number', name='uq_audit_merchant_seq'),
    )
    op.create_index('ix_audit_events_merchant_id', 'audit_events', ['merchant_id'])
    op.create_index('ix_audit_events_run_id', 'audit_events', ['run_id'])
    op.create_index('ix_audit_events_entity_id', 'audit_events', ['entity_id'])
    op.create_index('ix_audit_events_event_type', 'audit_events', ['event_type'])
    op.create_index('ix_audit_events_merchant_timestamp', 'audit_events', ['merchant_id', 'timestamp'])


def downgrade() -> None:
    op.drop_table('audit_events')
    op.drop_table('tool_calls')
    op.drop_table('agent_runs')
    op.drop_table('policy_decisions')
    op.drop_table('recovery_actions')
    op.drop_table('recovery_opportunities')
    op.drop_table('payment_failures')
    op.drop_table('payment_attempts')
    op.drop_table('payments')
    op.drop_table('orders')
    op.drop_table('customers')
    op.drop_table('merchants')
