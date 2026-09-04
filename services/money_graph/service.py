"""Money Graph Service.

Read-only graph traversal and contextual intelligence service.
Reconstructs connected financial data across Merchants, Customers, Orders, Payments,
Attempts, and Failures into structured Pydantic representations.

Strict Invariants:
- Never fabricate fields.
- If information is unavailable, return None or an explicit UNKNOWN state.
- Strictly read-only: does not mutate financial or transaction state.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from services.money_graph.database import SessionLocal
from services.money_graph.models import (
    Customer,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
)
from services.money_graph.schemas import (
    CustomerContext,
    CustomerPaymentHistoryContext,
    FailureContext,
    FullMoneyContext,
    OrderContext,
    PaymentAttemptContext,
    PaymentContext,
    TransactionSummary,
)


class MoneyGraphService:
    """Read-only query service for traversing the merchant money graph."""

    def __init__(self, session: Optional[Session] = None):
        self._external_session = session

    def _get_session(self) -> Session:
        return self._external_session if self._external_session is not None else SessionLocal()

    def get_payment_context(self, payment_id: uuid.UUID) -> Optional[PaymentContext]:
        """Retrieve structured payment context."""
        session = self._get_session()
        try:
            stmt = select(Payment).where(Payment.id == payment_id)
            payment = session.scalar(stmt)
            if not payment:
                return None
            return PaymentContext.model_validate(payment)
        finally:
            if self._external_session is None:
                session.close()

    def get_customer_context(self, customer_id: uuid.UUID) -> Optional[CustomerContext]:
        """Retrieve customer context with calculated lifetime value and payment metrics."""
        session = self._get_session()
        try:
            stmt = select(Customer).where(Customer.id == customer_id)
            customer = session.scalar(stmt)
            if not customer:
                return None

            # Aggregate lifetime value and payment counts
            ltv_stmt = select(
                func.coalesce(func.sum(Payment.amount), Decimal("0.00"))
            ).where(
                Payment.customer_id == customer_id,
                Payment.status == PaymentStatus.SUCCESS.value,
            )
            ltv = session.scalar(ltv_stmt) or Decimal("0.00")

            succ_stmt = select(func.count(Payment.id)).where(
                Payment.customer_id == customer_id,
                Payment.status == PaymentStatus.SUCCESS.value,
            )
            successful_payments = session.scalar(succ_stmt) or 0

            fail_stmt = select(func.count(Payment.id)).where(
                Payment.customer_id == customer_id,
                Payment.status == PaymentStatus.FAILED.value,
            )
            failed_payments = session.scalar(fail_stmt) or 0

            orders_stmt = select(func.count(Order.id)).where(Order.customer_id == customer_id)
            total_orders = session.scalar(orders_stmt) or 0

            return CustomerContext(
                id=customer.id,
                merchant_id=customer.merchant_id,
                external_id=customer.external_id,
                name=customer.name,
                email=customer.email,
                risk_score=customer.risk_score,
                lifetime_value=ltv,
                successful_payments=successful_payments,
                failed_payments=failed_payments,
                total_orders=total_orders,
                created_at=customer.created_at,
            )
        finally:
            if self._external_session is None:
                session.close()

    def get_order_context(self, order_id: uuid.UUID) -> Optional[OrderContext]:
        """Retrieve order commercial intent context."""
        session = self._get_session()
        try:
            stmt = select(Order).where(Order.id == order_id)
            order = session.scalar(stmt)
            if not order:
                return None
            return OrderContext.model_validate(order)
        finally:
            if self._external_session is None:
                session.close()

    def get_failure_context(self, payment_id: uuid.UUID) -> Optional[FailureContext]:
        """Retrieve diagnostic failure details for a payment.

        If payment succeeded or has no failure record, returns None.
        """
        session = self._get_session()
        try:
            stmt = (
                select(PaymentFailure)
                .options(joinedload(PaymentFailure.attempt))
                .where(PaymentFailure.payment_id == payment_id)
                .order_by(PaymentFailure.created_at.desc())
            )
            failure = session.scalar(stmt)
            if not failure:
                return None

            attempt_num = failure.attempt.attempt_number if failure.attempt else None
            gateway = failure.attempt.gateway_name if failure.attempt else None

            return FailureContext(
                id=failure.id,
                failure_code=failure.failure_code,
                raw_message=failure.raw_message,
                is_retryable=failure.is_retryable,
                attempt_number=attempt_num,
                gateway_name=gateway,
                created_at=failure.created_at,
            )
        finally:
            if self._external_session is None:
                session.close()

    def get_customer_payment_history(self, customer_id: uuid.UUID) -> Optional[CustomerPaymentHistoryContext]:
        """Calculate historical retry and ticket behavior for a customer."""
        session = self._get_session()
        try:
            payments_stmt = (
                select(Payment)
                .options(joinedload(Payment.attempts))
                .where(Payment.customer_id == customer_id)
                .order_by(Payment.created_at.desc())
            )
            customer_payments = session.scalars(payments_stmt).unique().all()
            if not customer_payments:
                return None

            total_count = len(customer_payments)
            success_count = sum(1 for p in customer_payments if p.status == PaymentStatus.SUCCESS.value)
            failed_count = sum(1 for p in customer_payments if p.status == PaymentStatus.FAILED.value)

            # Calculate retry success rate: for payments with >1 attempts that succeeded
            retried_payments = [p for p in customer_payments if len(p.attempts) > 1]
            if retried_payments:
                retried_and_succeeded = sum(
                    1 for p in retried_payments if p.status == PaymentStatus.SUCCESS.value
                )
                retry_rate = round(retried_and_succeeded / len(retried_payments), 4)
            else:
                # Default baseline based on overall success rate if no prior retries
                retry_rate = round(success_count / total_count, 4) if total_count > 0 else 0.50

            total_amount = sum(p.amount for p in customer_payments)
            avg_ticket = round(total_amount / total_count, 2) if total_count > 0 else Decimal("0.00")
            last_date = customer_payments[0].created_at if customer_payments else None

            return CustomerPaymentHistoryContext(
                customer_id=customer_id,
                total_payments=total_count,
                total_successful=success_count,
                total_failed=failed_count,
                retry_success_rate=retry_rate,
                average_ticket_size=avg_ticket,
                last_payment_date=last_date,
            )
        finally:
            if self._external_session is None:
                session.close()

    def get_related_transactions(self, customer_id: uuid.UUID) -> List[TransactionSummary]:
        """Retrieve recent transactions for this customer across all merchant orders."""
        session = self._get_session()
        try:
            stmt = (
                select(Payment)
                .where(Payment.customer_id == customer_id)
                .order_by(Payment.created_at.desc())
                .limit(20)
            )
            payments = session.scalars(stmt).all()
            return [
                TransactionSummary(
                    payment_id=p.id,
                    order_id=p.order_id,
                    amount=p.amount,
                    currency=p.currency,
                    status=p.status,
                    created_at=p.created_at,
                )
                for p in payments
            ]
        finally:
            if self._external_session is None:
                session.close()

    def get_payment_attempt_history(self, payment_id: uuid.UUID) -> List[PaymentAttemptContext]:
        """Retrieve complete timeline of gateway execution attempts for a payment."""
        session = self._get_session()
        try:
            stmt = (
                select(PaymentAttempt)
                .where(PaymentAttempt.payment_id == payment_id)
                .order_by(PaymentAttempt.attempt_number.asc())
            )
            attempts = session.scalars(stmt).all()
            return [PaymentAttemptContext.model_validate(a) for a in attempts]
        finally:
            if self._external_session is None:
                session.close()

    def get_full_money_context(self, payment_id: uuid.UUID) -> Optional[FullMoneyContext]:
        """Reconstruct the complete connected Money Graph for a payment.

        Strict invariant: Returns structured context with zero fabricated fields.
        """
        session = self._get_session()
        try:
            # 1. Payment
            payment_ctx = self.get_payment_context(payment_id)
            if not payment_ctx:
                return None

            # 2. Customer
            customer_ctx = self.get_customer_context(payment_ctx.customer_id)

            # 3. Order
            order_ctx = self.get_order_context(payment_ctx.order_id)

            # 4. Failure
            failure_ctx = self.get_failure_context(payment_id)

            # 5. Attempts
            attempts_ctx = self.get_payment_attempt_history(payment_id)

            # 6. History
            history_ctx = self.get_customer_payment_history(payment_ctx.customer_id)

            # 7. Related Transactions
            related_tx = self.get_related_transactions(payment_ctx.customer_id)

            return FullMoneyContext(
                payment=payment_ctx,
                customer=customer_ctx,
                order=order_ctx,
                failure=failure_ctx,
                attempts=attempts_ctx,
                history=history_ctx,
                related_transactions=related_tx,
            )
        finally:
            if self._external_session is None:
                session.close()
