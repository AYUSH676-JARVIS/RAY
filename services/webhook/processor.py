"""Inbound Webhook Processor with Deduplication, Tenant Isolation & State Machine Defense.

Connects:
Inbound Webhook
    ↓
Cryptographic Verification
    ↓
Event Deduplication (Idempotent replay defense)
    ↓
Tenant Isolation & Resolution
    ↓
Event Normalization
    ↓
State Machine Transition Validation
    ↓
Money Graph Update & Decision Workflow Trigger
    ↓
SHA-256 Audit Trail
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.action_layer.gateway import (
    GatewayStatus,
    NormalizedWebhookEvent,
    PaymentGateway,
    RazorpayGateway,
    SimulationGateway,
)
from services.audit.logger import AuditLogger
from services.money_graph.models import (
    ActorType,
    AuditEvent,
    Merchant,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    WebhookDelivery,
    OutboxEvent,
)
from services.money_graph.state_machine import (
    InvalidStateTransitionError,
    validate_payment_transition,
)
from services.webhook.security import (
    InvalidSignatureError,
    MissingSignatureError,
    WebhookSecurityVerifier,
)

logger = logging.getLogger("ray.webhook")


class WebhookProcessingResult:
    def __init__(
        self,
        success: bool,
        event_id: str,
        status: str,
        message: str,
        is_duplicate: bool = False,
        payment_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        audit_event_id: Optional[str] = None,
    ):
        self.success = success
        self.event_id = event_id
        self.status = status
        self.message = message
        self.is_duplicate = is_duplicate
        self.payment_id = payment_id
        self.workflow_id = workflow_id
        self.audit_event_id = audit_event_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "event_id": self.event_id,
            "status": self.status,
            "message": self.message,
            "is_duplicate": self.is_duplicate,
            "payment_id": self.payment_id,
            "workflow_id": self.workflow_id,
            "audit_event_id": self.audit_event_id,
        }


class WebhookProcessor:
    """Production processor for inbound gateway webhooks."""

    def __init__(
        self,
        session: Session,
        gateway: Optional[PaymentGateway] = None,
        trigger_decision_workflow: bool = True,
    ):
        self.session = session
        self.gateway = gateway or RazorpayGateway()
        self.trigger_decision_workflow = trigger_decision_workflow

    def _resolve_merchant_by_account_id(self, account_id: str) -> Optional[Merchant]:
        """Resolve a Merchant entity from an inbound gateway account identifier.
        
        Checks:
        1. Exact UUID match against Merchant.id.
        2. Exact string match against Merchant.slug.
        3. Normalized slug or UUID match stripping 'acc_' prefix.
        """
        if not account_id or not isinstance(account_id, str):
            return None

        clean_id = account_id.strip()

        # 1. Direct UUID match on Merchant.id
        try:
            m_uuid = uuid.UUID(clean_id)
            m = self.session.scalar(select(Merchant).where(Merchant.id == m_uuid))
            if m:
                return m
        except (ValueError, TypeError, AttributeError):
            pass

        # 2. Match on Merchant.slug
        m = self.session.scalar(select(Merchant).where(Merchant.slug == clean_id))
        if m:
            return m

        # 3. If account_id starts with 'acc_', check without prefix
        if clean_id.startswith("acc_"):
            stripped = clean_id[4:]
            m = self.session.scalar(select(Merchant).where(Merchant.slug == stripped))
            if m:
                return m
            try:
                m_uuid = uuid.UUID(stripped)
                m = self.session.scalar(select(Merchant).where(Merchant.id == m_uuid))
                if m:
                    return m
            except (ValueError, TypeError, AttributeError):
                pass

        return None

    def process_inbound_webhook(
        self,
        gateway_name: str,
        raw_body: bytes,
        signature: Optional[str],
        timestamp_header: Optional[str] = None,
        merchant_id: Optional[uuid.UUID] = None,
        custom_secret: Optional[str] = None,
    ) -> WebhookProcessingResult:
        """Process inbound webhook with signature verification, deduplication, and state safety."""
        # 1. Cryptographic Signature & Freshness Verification
        secret = WebhookSecurityVerifier.get_gateway_secret(gateway_name, custom_secret)
        WebhookSecurityVerifier.verify_signature(raw_body, signature, secret, gateway_name)
        WebhookSecurityVerifier.verify_timestamp_freshness(timestamp_header)

        # 2. Parse Raw Payload
        try:
            raw_payload = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Malformed JSON webhook payload: {e}")

        payload_hash = WebhookSecurityVerifier.compute_payload_hash(raw_body)

        # 3. Normalize Event via Gateway Abstraction
        normalized: NormalizedWebhookEvent = self.gateway.normalize_webhook_event(
            raw_payload=raw_payload,
            signature=signature,
        )

        event_id = normalized.event_id
        event_type = normalized.event_type

        # 4. Durable Idempotency & Deduplication
        existing_delivery = self.session.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.gateway_name == gateway_name,
                WebhookDelivery.event_id == event_id,
            )
        )
        if existing_delivery:
            logger.info(f"Duplicate webhook event '{event_id}' received; returning harmless idempotent response.")
            return WebhookProcessingResult(
                success=True,
                event_id=event_id,
                status="DUPLICATE",
                message="Duplicate webhook event delivery safely acknowledged without state mutation.",
                is_duplicate=True,
                payment_id=normalized.payment_id,
            )

        # Validate merchant_id foreign key existence before storing on WebhookDelivery
        valid_merchant_id = None
        if merchant_id:
            m_exists = self.session.scalar(select(Merchant.id).where(Merchant.id == merchant_id))
            if m_exists:
                valid_merchant_id = merchant_id

        # Validate gateway account_id if present in payload to prevent tenant spoofing
        payload_acc_id = raw_payload.get("account_id")
        if payload_acc_id:
            m_by_acc = self._resolve_merchant_by_account_id(str(payload_acc_id))
            if m_by_acc:
                if valid_merchant_id and m_by_acc.id != valid_merchant_id:
                    return WebhookProcessingResult(
                        success=False,
                        event_id=event_id,
                        status="REJECTED",
                        message="Cross-tenant account mismatch: Gateway account in webhook conflicts with merchant context.",
                    )
                valid_merchant_id = m_by_acc.id
            else:
                return WebhookProcessingResult(
                    success=False,
                    event_id=event_id,
                    status="REJECTED",
                    message=f"Unknown gateway account mapping: '{payload_acc_id}'.",
                )

        # Record Initial Delivery Record
        delivery_record = WebhookDelivery(
            id=uuid.uuid4(),
            merchant_id=valid_merchant_id,
            gateway_name=gateway_name,
            event_id=event_id,
            event_type=event_type,
            signature=signature[:64] if signature else None,
            payload_hash=payload_hash,
            status="RECEIVED",
            created_at=datetime.now(timezone.utc),
        )
        try:
            self.session.add(delivery_record)
            self.session.commit()
        except Exception:
            self.session.rollback()
            # Concurrent duplicate delivery won the race
            logger.info(f"Concurrent race deduplicated for event '{event_id}'.")
            return WebhookProcessingResult(
                success=True,
                event_id=event_id,
                status="DUPLICATE",
                message="Duplicate webhook event delivery safely acknowledged without state mutation.",
                is_duplicate=True,
                payment_id=normalized.payment_id,
            )


        # 5. Tenant & Payment Resolution
        payment: Optional[Payment] = None
        target_payment_id = normalized.payment_id
        effective_tenant_id = valid_merchant_id or merchant_id

        if target_payment_id:
            try:
                pay_uuid = uuid.UUID(target_payment_id)
                payment_query = select(Payment).where(Payment.id == pay_uuid)
                if effective_tenant_id:
                    payment_query = payment_query.where(Payment.merchant_id == effective_tenant_id)
                payment = self.session.scalar(payment_query)
            except (ValueError, TypeError):
                # Target ID might be external gateway payment ID (e.g. pay_...)
                # Look up through payment attempts
                attempt = self.session.scalar(
                    select(PaymentAttempt).where(PaymentAttempt.gateway_transaction_id == target_payment_id)
                )
                if attempt:
                    payment = attempt.payment
                    if effective_tenant_id and payment.merchant_id != effective_tenant_id:
                        payment = None  # Tenant boundary isolation!

        # If payment is specified in payload but not found for this tenant, fail closed safely
        if target_payment_id and not payment:
            delivery_record.status = "REJECTED"
            delivery_record.error_reason = "Payment reference not found or cross-tenant boundary violated."
            self.session.commit()
            return WebhookProcessingResult(
                success=False,
                event_id=event_id,
                status="REJECTED",
                message="Webhook referenced an unresolvable payment entity.",
                payment_id=target_payment_id,
            )

        # Associate tenant if discovered
        effective_merchant_id = payment.merchant_id if payment else valid_merchant_id
        if effective_merchant_id and not delivery_record.merchant_id:
            delivery_record.merchant_id = effective_merchant_id
            self.session.commit()

        # 6. Authoritative State Machine Validation & Transition
        workflow_id: Optional[str] = None
        audit_event_id: Optional[str] = None

        if payment:
            current_status = PaymentStatus(payment.status)
            target_status = self._map_gateway_status_to_payment_status(normalized.status, event_type)

            try:
                # Apply financial state machine rule
                validate_payment_transition(
                    current_status,
                    target_status,
                    evidence_id=f"webhook:{gateway_name}:{event_id}",
                )
                # Transition is legally authorized
                payment.status = target_status.value
                payment.updated_at = datetime.now(timezone.utc)

                # Atomically record Transactional Outbox Event (Phase 5)
                outbox_evt = OutboxEvent(
                    id=uuid.uuid4(),
                    merchant_id=effective_merchant_id,
                    aggregate_type="PAYMENT",
                    aggregate_id=payment.id,
                    event_type=f"PAYMENT_{target_status.value}",
                    payload_json={
                        "payment_id": str(payment.id),
                        "status": target_status.value,
                        "gateway": gateway_name,
                        "event_id": event_id,
                        "amount": str(payment.amount),
                        "currency": payment.currency,
                    },
                    status="PENDING",
                    created_at=datetime.now(timezone.utc),
                )
                self.session.add(outbox_evt)

            except InvalidStateTransitionError as e:
                # Out-of-order or illegal webhook event (e.g. late CAPTURED on REFUNDED)
                logger.warning(f"Out-of-order webhook transition rejected: {e}")
                delivery_record.status = "REJECTED_OUT_OF_ORDER"
                delivery_record.error_reason = str(e)
                self.session.commit()

                # Audit out-of-order rejection
                if effective_merchant_id:
                    AuditLogger.record_event(
                        session=self.session,
                        merchant_id=effective_merchant_id,
                        entity_type="WEBHOOK",
                        entity_id=delivery_record.id,
                        event_type="WEBHOOK_REJECTED_OUT_OF_ORDER",
                        actor_type=ActorType.ACTION_LAYER,
                        actor_id="webhook_processor",
                        payload_after={"reason": str(e), "event_id": event_id},
                    )
                    self.session.commit()

                return WebhookProcessingResult(
                    success=False,
                    event_id=event_id,
                    status="REJECTED_OUT_OF_ORDER",
                    message=f"Illegal or out-of-order state transition blocked: {e}",
                    payment_id=str(payment.id),
                )

            # 7. Connect Inbound Failure -> Money Graph & Decision Workflow (Phase 3)
            if target_status == PaymentStatus.FAILED:
                # Ingest failure into Money Graph
                failure_code = normalized.failure_code or "ISSUER_DECLINED"
                failure_msg = normalized.failure_message or "Payment failed via gateway webhook notification"

                attempt_record = PaymentAttempt(
                    id=uuid.uuid4(),
                    merchant_id=effective_merchant_id,
                    payment_id=payment.id,
                    attempt_number=len(payment.attempts) + 1 if payment.attempts else 1,
                    idempotency_key=f"idem_wh_{event_id}",
                    gateway_name=gateway_name,
                    gateway_transaction_id=normalized.payment_id or event_id,
                    status="FAILED",
                    created_at=datetime.now(timezone.utc),
                )
                self.session.add(attempt_record)

                failure_record = PaymentFailure(
                    id=uuid.uuid4(),
                    merchant_id=effective_merchant_id,
                    payment_id=payment.id,
                    payment_attempt_id=attempt_record.id,
                    failure_code=failure_code,
                    raw_message=failure_msg,
                    is_retryable=failure_code in ["BANK_TIMEOUT", "NETWORK_ERROR", "INSUFFICIENT_FUNDS"],
                    created_at=datetime.now(timezone.utc),
                )
                self.session.add(failure_record)
                self.session.commit()

                # Trigger canonical decision workflow if configured
                if self.trigger_decision_workflow:
                    from services.orchestrator.workflow import run_decision_workflow
                    try:
                        wf_res = run_decision_workflow(
                            payment_id=payment.id,
                            merchant_id=effective_merchant_id,
                            session=self.session,
                            stage_1_safety_lock=True,  # STRICT STAGE 1 SAFETY LOCK PRESERVED
                        )
                        workflow_id = str(wf_res.workflow_id)
                    except Exception as err:
                        logger.error(f"Decision workflow execution encountered error: {err}")

        # 8. Mark Delivery Complete & Append Cryptographic Audit Event
        delivery_record.status = "PROCESSED"
        delivery_record.processed_at = datetime.now(timezone.utc)
        self.session.commit()

        if effective_merchant_id:
            audit_evt = AuditLogger.record_event(
                session=self.session,
                merchant_id=effective_merchant_id,
                entity_type="WEBHOOK",
                entity_id=delivery_record.id,
                event_type=f"WEBHOOK_PROCESSED_{event_type.upper().replace('.', '_')}",
                actor_type=ActorType.ACTION_LAYER,
                actor_id=f"gateway:{gateway_name.lower()}",
                payload_after={
                    "event_id": event_id,
                    "event_type": event_type,
                    "payment_id": str(payment.id) if payment else None,
                    "status": "PROCESSED",
                    "workflow_id": workflow_id,
                },
            )
            self.session.commit()
            audit_event_id = str(audit_evt.id)

        return WebhookProcessingResult(
            success=True,
            event_id=event_id,
            status="PROCESSED",
            message="Webhook event verified, state transition applied, and audit chained successfully.",
            payment_id=str(payment.id) if payment else None,
            workflow_id=workflow_id,
            audit_event_id=audit_event_id,
        )

    def _map_gateway_status_to_payment_status(self, gateway_status: str, event_type: str) -> PaymentStatus:
        """Map normalized gateway status and event type to domain PaymentStatus."""
        if "captured" in event_type or "settled" in event_type:
            return PaymentStatus.CAPTURED
        if "authorized" in event_type:
            return PaymentStatus.SUCCESS
        if gateway_status == GatewayStatus.SUCCEEDED.value:
            return PaymentStatus.SUCCESS
        if gateway_status == GatewayStatus.FAILED.value:
            return PaymentStatus.FAILED
        return PaymentStatus.UNKNOWN
