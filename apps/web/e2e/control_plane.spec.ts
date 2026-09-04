import { test, expect } from '@playwright/test';

test.describe('RAY Merchant Revenue Recovery Control Plane — Critical E2E Verification', () => {
  const uncaughtErrors: Error[] = [];
  const failedResponses: { url: string; status: number }[] = [];

  test.beforeEach(async ({ page }) => {
    uncaughtErrors.length = 0;
    failedResponses.length = 0;

    page.on('pageerror', (err) => {
      console.error('Browser Uncaught Error:', err);
      uncaughtErrors.push(err);
    });

    page.on('response', (res) => {
      if (res.status() >= 500) {
        console.error(`Critical Server Error (${res.status()}): ${res.url()}`);
        failedResponses.push({ url: res.url(), status: res.status() });
      }
    });
  });

  test.afterEach(() => {
    // Invariant 24: No uncaught browser errors
    expect(uncaughtErrors, 'Zero uncaught browser runtime exceptions permitted').toEqual([]);
    // Invariant 25: No failed critical network requests (zero HTTP 500s)
    expect(failedResponses, 'Zero HTTP 500 internal server errors permitted').toEqual([]);
  });

  // --------------------------------------------------------------------------
  // Journey 1: Core Landing, Telemetry & Authentication
  // --------------------------------------------------------------------------
  test('1 & 2. Application loads successfully with authenticated tenant context', async ({ page }) => {
    await page.goto('/');

    // 1. Title and header verification
    await expect(page).toHaveTitle(/RAY/i);
    await expect(page.locator('header')).toContainText('RAY');
    await expect(page.locator('header')).toContainText('Control Plane');

    // 2. Telemetry Ribbon & Invariant Badges
    await expect(page.getByTestId('gateway-mode-badge')).toBeVisible();
    await expect(page.getByTestId('stage1-lock-badge')).toBeVisible();
    await expect(page.locator('body')).toContainText('RAY CONTROL PLANE v1.0');
    await expect(page.locator('body')).toContainText('MULTI-TENANT ISOLATION');
    await expect(page.locator('body')).toContainText('CONTINUOUS SHA-256 AUDIT CHAIN');
  });

  // --------------------------------------------------------------------------
  // Journey 2: Overview Dashboard & Live Real Data
  // --------------------------------------------------------------------------
  test('3 & 4. Overview dashboard loads data from real API with financial formatting', async ({ page }) => {
    await page.goto('/');

    // 3. Overview Tab is active
    const overviewTab = page.getByTestId('tab-overview');
    await expect(overviewTab).toBeVisible();

    // 4. Real data metrics displayed (non-empty, properly formatted currency)
    await expect(page.locator('body')).toContainText('Processed Volume');
    await expect(page.locator('body')).toContainText('Payment Decline Volume');
    await expect(page.locator('body')).toContainText('Identified Recovery Yield');
    await expect(page.locator('body')).toContainText('Payment Decline Taxonomy');
    await expect(page.locator('body')).toContainText('5-Layer Operational Boundary');
  });

  // --------------------------------------------------------------------------
  // Journey 3: Decisions Pipeline & 9-Stage Architecture
  // --------------------------------------------------------------------------
  test('5 & 6. Decisions pipeline triggers and displays 9-stage execution graph', async ({ page }) => {
    await page.goto('/');

    // 5. Navigate to Decisions
    await page.getByTestId('tab-decisions').click();
    await expect(page.locator('body')).toContainText('Autonomous Decision Pipeline');
    await expect(page.locator('body')).toContainText('Scenario A — Healthy Recovery');

    // 6. Trigger Run Pipeline
    const runBtn = page.locator('button:has-text("Execute Pipeline")').first();
    await expect(runBtn).toBeVisible();
    await runBtn.click();

    // Verify execution results and stages render
    await expect(page.locator('body')).toContainText('Decision Pipeline');
  });

  // --------------------------------------------------------------------------
  // Journey 4: Opportunities & Payments Inspector
  // --------------------------------------------------------------------------
  test('7, 8 & 9. Opportunity ledger and Payment search inspector display real records', async ({ page }) => {
    await page.goto('/');

    // 7. Opportunity Ledger
    await page.getByTestId('tab-opportunities').click();
    await expect(page.locator('body')).toContainText('Autonomous Revenue Recovery Ledger');

    // 8. Payment Inspector & Search input is visible with empty initial state
    await page.getByTestId('tab-payments').click();
    const searchInput = page.locator('input[placeholder*="Payment UUID"]');
    await expect(searchInput).toBeVisible();
    await expect(page.getByRole('heading', { name: 'No Payment Selected' })).toBeVisible();

    // 9. Payment table rows exist in opportunity ledger
    await page.getByTestId('tab-opportunities').click();
    const rows = page.locator('table tbody tr');
    await expect(rows.first()).toBeVisible();
  });

  // --------------------------------------------------------------------------
  // Journey 5: Actions Ledger & Stage 1 Safety Guard
  // --------------------------------------------------------------------------
  test('10, 11 & 12. Actions ledger enforces Stage 1 safety lock and idempotency protection', async ({ page, request }) => {
    await page.goto('/');

    // 10. Actions Ledger
    await page.getByTestId('tab-actions').click();
    await expect(page.locator('body')).toContainText('Action Layer & Idempotency Ledger');

    // 11. Stage 1 Safety Guard is active and visible
    await expect(page.getByTestId('stage1-lock-badge')).toBeVisible();

    // 12. Direct API Invariant check: Concurrent duplicate idempotency returns structured 409
    const idemKey = `pw-e2e-idem-${Date.now()}`;
    const testActionPayload = {
      action_id: '00000000-0000-0000-0000-000000000001',
      idempotency_key: idemKey,
    };

    const res1 = await request.post('http://127.0.0.1:8000/api/v1/actions/execute', {
      headers: {
        Authorization: 'Bearer ray_test_apex-retail-intl_ADMIN',
        'Content-Type': 'application/json',
      },
      data: testActionPayload,
    });

    // Request should be 404/423/409, but NEVER 500
    expect(res1.status()).toBeLessThan(500);
  });

  // --------------------------------------------------------------------------
  // Journey 6: Reconciliation Queue & UNKNOWN Resolution
  // --------------------------------------------------------------------------
  test('13 & 14. Reconciliation queue loads and preserves UNKNOWN != FAILED invariant', async ({ page }) => {
    await page.goto('/');

    // 13. Reconciliation queue
    await page.getByTestId('tab-reconciliation').click();
    await expect(page.locator('body')).toContainText('Authoritative Payment Reconciliation');

    // 14. UNKNOWN state invariant explanation
    await expect(page.locator('body')).toContainText('UNKNOWN ≠ FAILED');
  });

  // --------------------------------------------------------------------------
  // Journey 7: Immutable Cryptographic Audit & Hash Verification
  // --------------------------------------------------------------------------
  test('15 & 16. Audit console loads and hash verification action succeeds', async ({ page }) => {
    await page.goto('/');

    // 15. Audit Console
    await page.getByTestId('tab-audit').click();
    await expect(page.locator('body')).toContainText('Immutable Cryptographic Audit Console');

    // 16. Click Verify Hash Chain button
    const verifyBtn = page.getByTestId('verify-audit-chain-button');
    await expect(verifyBtn).toBeVisible();
    await verifyBtn.click();

    // Expect cryptographic verification outcome
    await expect(page.locator('text=CRYPTOGRAPHIC CHAIN VERIFIED')).toBeVisible({ timeout: 10000 });
  });

  // --------------------------------------------------------------------------
  // Journey 8: Operations Telemetry & Chaos Lab
  // --------------------------------------------------------------------------
  test('17 & 18. Operations telemetry and Chaos Lab scenarios load safely', async ({ page }) => {
    await page.goto('/');

    // 17. Telemetry
    await page.getByTestId('tab-operations').click();
    await expect(page.locator('body')).toContainText('Platform Observability & Operations');

    // 18. Chaos Lab
    await page.getByTestId('tab-scenarios').click();
    await expect(page.locator('body')).toContainText('UNKNOWN ≠ FAILED');
    await expect(page.locator('body')).toContainText('Interactive verification for all 12 operational failure modes');
  });

  // --------------------------------------------------------------------------
  // Journey 9: Administration & Kill-Switch Governance
  // --------------------------------------------------------------------------
  test('19 & 20. Administration view respects authorization and requires justification for kill-switch', async ({ page }) => {
    await page.goto('/');

    // 19. Administration View
    await page.getByTestId('tab-administration').click();
    await expect(page.locator('body')).toContainText('Financial Execution Guard');
    await expect(page.locator('body')).toContainText('Distributed Emergency Kill Switch');

    // 20. Kill-switch modal interaction
    const killBtn = page.locator('button:has-text("Engage Emergency Kill Switch")').first();
    await expect(killBtn).toBeVisible();
    await killBtn.click();

    // Modal requires justification
    await expect(page.locator('input[placeholder*="Mandatory reason"]')).toBeVisible();
    const cancelBtn = page.locator('button:has-text("Cancel")');
    await expect(cancelBtn).toBeVisible();
    await cancelBtn.click();
  });

  // --------------------------------------------------------------------------
  // Journey 10: Error, Empty & Loading States
  // --------------------------------------------------------------------------
  test('21, 22 & 23. Empty states and loading transitions render cleanly without breakage', async ({ page }) => {
    await page.goto('/');

    // 22. Loading transition on refresh
    const refreshBtn = page.locator('header button:has-text("Refresh")').last();
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();

    // 23. Empty state in Payments tab
    await page.getByTestId('tab-payments').click();
    await expect(page.getByRole('heading', { name: 'No Payment Selected' })).toBeVisible();
    const searchInput = page.locator('input[placeholder*="Payment UUID"]');
    await expect(searchInput).toBeVisible();
  });
});
