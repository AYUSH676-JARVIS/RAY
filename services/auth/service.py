"""Authentication and Authorization Service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from typing import Callable, Optional
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from services.auth.models import Principal, Role, Permission, ROLE_PERMISSIONS
from services.config.settings import get_settings
from services.money_graph.database import get_db
from services.money_graph.models import Merchant


import base64
import json


def hash_token(raw_token: str) -> str:
    """Generate SHA-256 digest for an API token."""
    return hashlib.sha256(raw_token.strip().encode()).hexdigest()


def create_access_token(payload: dict, secret: str) -> str:
    """Create signed HS256 JWT using standard library."""
    header = {"alg": "HS256", "typ": "JWT"}
    hdr_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    pay_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signing_input = f"{hdr_b64}.{pay_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{hdr_b64}.{pay_b64}.{sig_b64}"


def decode_access_token(token: str, secret: str) -> dict:
    """Decode and verify signed HS256 JWT using standard library."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT token structure")
    hdr_b64, pay_b64, sig_b64 = parts
    signing_input = f"{hdr_b64}.{pay_b64}".encode()
    expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    rem = len(sig_b64) % 4
    padded_sig = sig_b64 + ("=" * (4 - rem) if rem else "")
    actual_sig = base64.urlsafe_b64decode(padded_sig.encode())
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Signature mismatch")
    rem_pay = len(pay_b64) % 4
    padded_pay = pay_b64 + ("=" * (4 - rem_pay) if rem_pay else "")
    return json.loads(base64.urlsafe_b64decode(padded_pay.encode()).decode())


def get_current_principal(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_merchant_id: Optional[str] = Header(None, alias="X-Merchant-ID"),
    db: Session = Depends(get_db),
) -> Principal:
    """FastAPI dependency for authenticating requests and resolving tenant principal.
    
    Rejects anonymous access with HTTP 401.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) == 1:
        token = parts[0]
    elif len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()

    # 1. Test / Synthetic Credential Schema: ray_test_<merchant_id_or_slug>_<role> or ray_test_<role>
    if token.startswith("ray_test_") or token.startswith("test_"):
        # CRITICAL P0 INVARIANT: Test tokens are strictly forbidden in production
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Production authentication violation: Test tokens are strictly forbidden in production environment.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload = token.replace("ray_test_", "").replace("test_", "")
        target_role = Role.MERCHANT_ADMIN
        target_merchant_id: Optional[uuid.UUID] = None

        # Check if entire payload is a role name (e.g. READ_ONLY, OPERATOR)
        role_match = None
        for r in Role:
            if payload.upper() == r.value:
                role_match = r
                break

        if role_match:
            target_role = role_match
        else:
            # Match role at suffix of payload
            for r in Role:
                suffix = f"_{r.value.lower()}"
                if payload.lower().endswith(suffix):
                    target_role = r
                    ident = payload[:-len(suffix)]
                    try:
                        target_merchant_id = uuid.UUID(ident)
                    except ValueError:
                        m = db.query(Merchant).filter(Merchant.slug == ident).first()
                        if m:
                            target_merchant_id = m.id
                        else:
                            # Specified merchant identity could not be found
                            raise HTTPException(
                                status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=f"Authentication rejected: Merchant '{ident}' not found.",
                                headers={"WWW-Authenticate": "Bearer"},
                            )
                    break

        # Check X-Merchant-ID header if target_merchant_id was not in token
        if not target_merchant_id and x_merchant_id:
            try:
                target_merchant_id = uuid.UUID(x_merchant_id)
            except ValueError:
                m = db.query(Merchant).filter(Merchant.slug == x_merchant_id).first()
                if m:
                    target_merchant_id = m.id

        # Fallback: In non-production testing ONLY, if role specified without merchant, bind to default merchant
        if not target_merchant_id:
            m = db.query(Merchant).first()
            if m:
                target_merchant_id = m.id
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication rejected: No merchant exists to bind test token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        perms = set(ROLE_PERMISSIONS.get(target_role, set()))
        return Principal(
            merchant_id=target_merchant_id,
            user_id=f"test_user_{target_role.value.lower()}",
            role=target_role,
            permissions=perms,
        )

    # 2. Live API Key Authentication: ray_live_<merchant_slug>_<key>
    if token.startswith("ray_live_"):
        parts = token.split("_")
        if len(parts) >= 3:
            slug = parts[2]
            merchant = db.query(Merchant).filter(Merchant.slug == slug).first()
            if not merchant:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Authentication rejected: Merchant with slug '{slug}' not found.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if merchant.status != "ACTIVE":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Authentication rejected: Merchant '{slug}' is {merchant.status}.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            perms = set(ROLE_PERMISSIONS[Role.MERCHANT_ADMIN])
            return Principal(
                merchant_id=merchant.id,
                user_id=f"api_key:{merchant.slug}",
                role=Role.MERCHANT_ADMIN,
                permissions=perms,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed live API key format. Expected ray_live_<merchant_slug>_<key>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Production HS256 JWT Bearer Token Authentication
    try:
        jwt_secret = settings.JWT_SECRET_KEY.get_secret_value()
        payload = decode_access_token(token, jwt_secret)
        raw_mid = payload.get("merchant_id") or payload.get("sub")
        if not raw_mid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT token missing required merchant_id claim.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        mid = uuid.UUID(str(raw_mid))
        merchant = db.query(Merchant).filter(Merchant.id == mid).first()
        if not merchant:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT token references nonexistent merchant.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if merchant.status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Merchant account '{merchant.slug}' is inactive.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        role_str = payload.get("role", Role.MERCHANT_ADMIN.value)
        role = Role(role_str) if role_str in Role._value2member_map_ else Role.MERCHANT_ADMIN
        perms = set(ROLE_PERMISSIONS.get(role, set()))
        return Principal(
            merchant_id=merchant.id,
            user_id=payload.get("user_id", f"jwt_user_{role.value.lower()}"),
            role=role,
            permissions=perms,
        )
    except (ValueError, json.JSONDecodeError):
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials or unknown token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(perm: Permission) -> Callable[[Principal], Principal]:
    """Dependency factory checking that caller has the specified permission."""
    def permission_checker(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_permission(perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Insufficient privileges. Required permission: '{perm.value}'.",
            )
        return principal

    return permission_checker
