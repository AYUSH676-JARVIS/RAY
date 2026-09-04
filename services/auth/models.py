"""RAY Authentication & RBAC Models.

Enforces zero-trust authentication, tenant isolation, and granular least-privilege RBAC.
"""

from __future__ import annotations

import enum
import uuid
from typing import Set
from pydantic import BaseModel, Field


class Role(str, enum.Enum):
    """Staff and system roles."""
    MERCHANT_ADMIN = "MERCHANT_ADMIN"
    OPERATOR = "OPERATOR"
    FINANCE = "FINANCE"
    ANALYST = "ANALYST"
    READ_ONLY = "READ_ONLY"


class Permission(str, enum.Enum):
    """Granular operational permissions."""
    PAYMENT_VIEW = "payment:view"
    OPPORTUNITY_VIEW = "opportunity:view"
    OPPORTUNITY_DETECT = "opportunity:detect"
    ACTION_EXECUTE = "action:execute"
    AUDIT_VIEW = "audit:view"
    DASHBOARD_VIEW = "dashboard:view"
    SETTINGS_MANAGE = "settings:manage"
    ADMIN_MANAGE = "admin:manage"


# Role-to-Permissions Mapping
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.MERCHANT_ADMIN: {
        Permission.PAYMENT_VIEW,
        Permission.OPPORTUNITY_VIEW,
        Permission.OPPORTUNITY_DETECT,
        Permission.ACTION_EXECUTE,
        Permission.AUDIT_VIEW,
        Permission.DASHBOARD_VIEW,
        Permission.SETTINGS_MANAGE,
        Permission.ADMIN_MANAGE,
    },
    Role.OPERATOR: {
        Permission.PAYMENT_VIEW,
        Permission.OPPORTUNITY_VIEW,
        Permission.OPPORTUNITY_DETECT,
        Permission.ACTION_EXECUTE,
        Permission.AUDIT_VIEW,
        Permission.DASHBOARD_VIEW,
    },
    Role.FINANCE: {
        Permission.PAYMENT_VIEW,
        Permission.OPPORTUNITY_VIEW,
        Permission.DASHBOARD_VIEW,
        Permission.AUDIT_VIEW,
    },
    Role.ANALYST: {
        Permission.PAYMENT_VIEW,
        Permission.OPPORTUNITY_VIEW,
        Permission.DASHBOARD_VIEW,
    },
    Role.READ_ONLY: {
        Permission.PAYMENT_VIEW,
        Permission.OPPORTUNITY_VIEW,
        Permission.DASHBOARD_VIEW,
    },
}


class Principal(BaseModel):
    """Authenticated entity with tenant binding and role permissions."""
    merchant_id: uuid.UUID
    user_id: str
    role: Role
    permissions: Set[Permission] = Field(default_factory=set)

    def has_permission(self, permission: Permission) -> bool:
        """Check if principal has the requested permission."""
        return permission in self.permissions
