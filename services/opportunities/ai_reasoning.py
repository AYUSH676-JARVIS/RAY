"""AI Safety, Structured Validation & Prompt Injection Defense.

Core Invariants:
1. AI recommends. Deterministic policy authorizes. Deterministic action layer executes.
2. AI cannot bypass policy or directly execute financial actions.
3. Structured AI output: Validated against strict Pydantic schemas.
4. Evidence Binding: All cited evidence IDs must empirically exist in the Money Graph.
5. Prompt Injection Defense: Untrusted customer/order text is sanitized and contained.
6. Deterministic Fallback: Any parsing error, hallucination, or safety breach falls back to deterministic provider.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, field_validator

from services.money_graph.schemas import FullMoneyContext
from services.opportunities.failure_intelligence import CandidateStrategy


class PromptInjectionDetectedError(ValueError):
    """Raised when an adversarial instruction injection payload is detected in untrusted inputs."""
    pass


class PromptInjectionSanitizer:
    """Detects and neutralizes adversarial prompt injection payloads in untrusted merchant/customer inputs."""

    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous\s+|prior\s+)?(instructions|policy|rules|guardrails)", re.IGNORECASE),
        re.compile(r"bypass\s+(policy|security|limits|guardrails)", re.IGNORECASE),
        re.compile(r"system\s*:\s*(you\s+are|override)", re.IGNORECASE),
        re.compile(r"grant\s+(100%|full)\s+refund", re.IGNORECASE),
        re.compile(r"disregard\s+(velocity|risk|fraud)", re.IGNORECASE),
        re.compile(r"override\s+(policy|limits)", re.IGNORECASE),
        re.compile(r"execute\s+action\s+immediately", re.IGNORECASE),
        re.compile(r"transfer\s+all\s+money", re.IGNORECASE),
    ]

    PII_PATTERNS = [
        # Credit Card Numbers (13-19 digits with optional dashes/spaces)
        (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[CARD_REDACTED]"),
        # US SSN format (XXX-XX-XXXX)
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
        # API Keys / Bearer Tokens / Secrets
        (re.compile(r"(?:bearer\s+[a-zA-Z0-9_\-\.]{16,})|(?:(?:api_?key|secret|token|password)[\s:=]+['\"]?[a-zA-Z0-9_\-\.]{16,}['\"]?)", re.IGNORECASE), "[SECRET_REDACTED]"),
    ]

    @classmethod
    def sanitize_untrusted_text(cls, text: Optional[str]) -> str:
        """Sanitize untrusted text string to neutralize prompt injection and redact PII / credentials."""
        if not text:
            return ""

        clean = text.strip()
        # 1. Neutralize injection patterns
        for pat in cls.INJECTION_PATTERNS:
            if pat.search(clean):
                clean = pat.sub("[UNTRUSTED_INSTRUCTION_REDACTED]", clean)

        # 2. Redact PII and secrets
        for pat, replacement in cls.PII_PATTERNS:
            clean = pat.sub(replacement, clean)

        return clean


class AIStrategyRecommendation(BaseModel):
    """Strictly typed structured output expected from AI reasoning models."""
    recommended_strategy: CandidateStrategy
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence in recommendation [0.0 - 1.0]")
    diagnosis: str = Field(min_length=5, description="Factual diagnosis of failure cause")
    cited_evidence_ids: List[str] = Field(default_factory=list, description="IDs of empirical entities cited as evidence")

    @field_validator("confidence_score")
    @classmethod
    def check_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence_score must be between 0.0 and 1.0")
        return v


class AIReasoningValidator:
    """Verifies that AI outputs strictly adhere to empirical facts and bounded schemas."""

    @staticmethod
    def validate_recommendation(
        rec: AIStrategyRecommendation,
        context: FullMoneyContext,
    ) -> Tuple[bool, Optional[str]]:
        """Verify recommendation bounds and evidence binding against the real Money Graph.
        
        Returns:
            (is_valid: bool, error_reason: Optional[str])
        """
        # 1. Collect all valid entity IDs in the connected Money Graph
        valid_entity_ids = {
            str(context.payment.id),
            str(context.payment.merchant_id),
            str(context.payment.order_id),
            str(context.payment.customer_id),
        }
        if context.customer:
            valid_entity_ids.add(str(context.customer.id))
            valid_entity_ids.add(context.customer.external_id)
        if context.failure:
            valid_entity_ids.add(context.failure.failure_code)
        for att in context.attempts:
            valid_entity_ids.add(att.idempotency_key)
            if att.gateway_transaction_id:
                valid_entity_ids.add(att.gateway_transaction_id)

        # 2. Check that every cited evidence ID exists in the Money Graph
        for cited_id in rec.cited_evidence_ids:
            if cited_id not in valid_entity_ids:
                return (
                    False,
                    f"AI hallucination rejected: Cited evidence '{cited_id}' does not exist in the connected Money Graph.",
                )

        # 3. Strategy must be a legal CandidateStrategy
        if not isinstance(rec.recommended_strategy, CandidateStrategy):
            return False, f"Illegal candidate strategy '{rec.recommended_strategy}'."

        return True, None
