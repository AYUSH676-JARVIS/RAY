"""Outcome Engine — Settlement & Ground Truth Verification.

Invariants:
- Outcome verification confirms reality.
- Verifies bank webhooks, settlement reports, and ledger matches.
- Prohibits false success: Gateway reporting success when ledger status is FAILED is rejected.
- Enforces strict currency and amount integrity: No silent currency conversions or over/under-reported recovery.
"""

from __future__ import annotations

import decimal
import uuid
from typing import Any, Dict, Optional


class ReconciliationStatus:
    VERIFIED_SETTLED = "VERIFIED_SETTLED"
    FALSE_SUCCESS_REJECTED = "FALSE_SUCCESS_REJECTED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    TRANSACTION_MISMATCH = "TRANSACTION_MISMATCH"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"


class OutcomeReconciler:
    """Verifies that attempted recoveries actually settled into merchant bank accounts."""

    def reconcile_payment(
        self,
        payment_id: uuid.UUID,
        reported_amount: decimal.Decimal,
        settled_amount: Optional[decimal.Decimal],
        settlement_status: str,
    ) -> Dict[str, Any]:
        """Confirm outcome reality against gateway settlement reports (backward-compatible)."""
        is_verified = (
            settlement_status in ["SETTLED", "CAPTURED"]
            and settled_amount is not None
            and settled_amount == reported_amount
        )
        return {
            "payment_id": str(payment_id),
            "is_verified": is_verified,
            "settlement_status": settlement_status,
            "variance": str(reported_amount - (settled_amount or decimal.Decimal("0.00"))),
        }

    def verify_authoritative_outcome(
        self,
        payment_id: uuid.UUID,
        requested_amount: decimal.Decimal,
        requested_currency: str,
        authoritative_status: str,
        settled_amount: Optional[decimal.Decimal] = None,
        settled_currency: Optional[str] = None,
        gateway_transaction_id: Optional[str] = None,
        expected_transaction_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Authoritative ground-truth verification preventing false success and accounting leakage.
        
        Rules:
        1. False Success Protection: If authoritative status is FAILED, CANCELLED, or UNKNOWN,
           the payment CANNOT be declared recovered.
        2. Transaction Matching: Gateway transaction ID must match expected attempt ID.
        3. Currency Integrity: Requested currency must match settled currency (no silent conversion).
        4. Amount Integrity: Requested amount must match settled amount. If under-settled,
           actual recovered revenue is strictly bounded by settled amount.
        """
        # 1. Authoritative status validation
        normalized_status = authoritative_status.upper().strip()
        if normalized_status not in ["SETTLED", "CAPTURED", "SUCCESS"]:
            return {
                "payment_id": str(payment_id),
                "is_recovered": False,
                "reconciliation_status": ReconciliationStatus.FALSE_SUCCESS_REJECTED,
                "authoritative_recovered_amount": decimal.Decimal("0.00"),
                "reason": f"Gateway claims success but authoritative status is '{normalized_status}'. False success rejected.",
            }

        # 2. Transaction ID matching
        if expected_transaction_id and gateway_transaction_id:
            if gateway_transaction_id.strip() != expected_transaction_id.strip():
                return {
                    "payment_id": str(payment_id),
                    "is_recovered": False,
                    "reconciliation_status": ReconciliationStatus.TRANSACTION_MISMATCH,
                    "authoritative_recovered_amount": decimal.Decimal("0.00"),
                    "reason": f"Transaction ID mismatch: expected '{expected_transaction_id}', received '{gateway_transaction_id}'.",
                }

        # 3. Currency matching
        eff_settled_curr = (settled_currency or requested_currency).upper().strip()
        eff_req_curr = requested_currency.upper().strip()
        if eff_settled_curr != eff_req_curr:
            return {
                "payment_id": str(payment_id),
                "is_recovered": False,
                "reconciliation_status": ReconciliationStatus.CURRENCY_MISMATCH,
                "authoritative_recovered_amount": decimal.Decimal("0.00"),
                "reason": f"Currency mismatch: requested {eff_req_curr} but settled in {eff_settled_curr}. Silent conversion prohibited.",
            }

        # 4. Amount matching
        if settled_amount is None or settled_amount < decimal.Decimal("0.00"):
            return {
                "payment_id": str(payment_id),
                "is_recovered": False,
                "reconciliation_status": ReconciliationStatus.AMOUNT_MISMATCH,
                "authoritative_recovered_amount": decimal.Decimal("0.00"),
                "reason": "Settled amount is missing or negative.",
            }

        if settled_amount < requested_amount:
            # Under-settlement: recovery is incomplete, cannot report full recovery
            return {
                "payment_id": str(payment_id),
                "is_recovered": False,
                "reconciliation_status": ReconciliationStatus.AMOUNT_MISMATCH,
                "authoritative_recovered_amount": settled_amount,
                "reason": f"Under-settled variance: requested {requested_amount} but received {settled_amount}.",
            }

        if settled_amount > requested_amount:
            # Over-settlement: potential duplicate capture or accounting anomaly
            return {
                "payment_id": str(payment_id),
                "is_recovered": False,
                "reconciliation_status": ReconciliationStatus.AMOUNT_MISMATCH,
                "authoritative_recovered_amount": requested_amount,
                "reason": f"Over-settled variance: requested {requested_amount} but received {settled_amount}. Flagged for review.",
            }

        # Exact authoritative verification
        return {
            "payment_id": str(payment_id),
            "is_recovered": True,
            "reconciliation_status": ReconciliationStatus.VERIFIED_SETTLED,
            "authoritative_recovered_amount": settled_amount,
            "reason": "Authoritative settlement ledger confirmed exact match.",
        }
