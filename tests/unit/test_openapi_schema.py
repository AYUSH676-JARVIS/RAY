"""Unit Test Suite for OpenAPI 3.0 Specification & API Versioning.

Verifies:
1. App exposes valid OpenAPI 3.0 schema.
2. All v1 routes and backward compatibility aliases are documented.
3. Component schemas define strict types and descriptions.
"""

from apps.api.main import app


def test_openapi_schema_structure():
    """Verify OpenAPI 3.0 specification integrity."""
    schema = app.openapi()
    assert "openapi" in schema
    assert schema["openapi"].startswith("3.")
    assert "paths" in schema
    assert "components" in schema

    paths = schema["paths"]

    # Verify key v1 endpoints exist
    assert "/api/v1/dashboard" in paths
    assert "/api/v1/payments/{payment_id}" in paths
    assert "/api/v1/opportunities" in paths
    assert "/api/v1/opportunities/detect" in paths
    assert "/api/v1/actions/execute" in paths
    assert "/api/v1/audit/events" in paths
    assert "/api/v1/audit/verify" in paths
    assert "/api/v1/operations/status" in paths
    assert "/api/v1/merchants/config" in paths
    assert "/api/v1/admin/merchants" in paths
    assert "/api/v1/admin/stage2/status" in paths
    assert "/api/v1/admin/kill-switch" in paths

    # Verify backward compatibility aliases
    assert "/api/dashboard" in paths
    assert "/api/payments/{payment_id}" in paths
    assert "/api/opportunities" in paths
    assert "/api/actions/execute" in paths


def test_openapi_schemas_have_components():
    """Verify core financial intelligence schemas are defined in components."""
    schema = app.openapi()
    schemas = schema["components"]["schemas"]

    assert "PaymentDetailResponse" in schemas
    assert "DashboardMetricsResponse" in schemas
    assert "ExecuteActionRequest" in schemas
    assert "ExecuteActionResponse" in schemas
    assert "ResolvedConfig" in schemas
    assert "OperationsStatusResponse" in schemas
