/**
 * RAY — Authentication & Tenant Session Management
 *
 * Enforces:
 * 1. Clean token management without hardcoded production credentials.
 * 2. Strict isolation: Demo mode credentials (ray_test_*) are strictly disabled in production.
 * 3. Session tokens stored in memory / sessionStorage (never leaked to localStorage or git).
 * 4. Zero exposure of JWT signing keys or private secrets to client bundles.
 */

const TOKEN_STORAGE_KEY = "ray_auth_token";
const ROLE_STORAGE_KEY = "ray_auth_role";
const MERCHANT_STORAGE_KEY = "ray_auth_merchant";

let memoryToken: string | null = null;
let memoryRole: string = "MERCHANT_ADMIN";
let memoryMerchant: string = "apex-retail-intl";

export function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function isProductionEnvironment(): boolean {
  return process.env.NODE_ENV === "production" && process.env.NEXT_PUBLIC_DEMO_MODE !== "true";
}

/**
 * Retrieve the current active authentication token.
 */
export function getAuthToken(): string {
  if (isBrowser()) {
    try {
      const stored = window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
      if (stored) return stored;
    } catch {
      // Fall through to memory token if storage is restricted
    }
  }
  if (memoryToken) return memoryToken;

  // CRITICAL P0 INVARIANT: Demo credentials are strictly disabled in production
  if (!isProductionEnvironment()) {
    const role = getActiveRole();
    const merchant = getActiveMerchantSlug();
    return `ray_test_${merchant}_${role}`;
  }
  return "";
}

/**
 * Persist the current active authentication token.
 */
export function setAuthToken(token: string): void {
  memoryToken = token;
  if (isBrowser()) {
    try {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch {
      // Memory storage acts as fallback
    }
  }
}

/**
 * Clear the current authentication session.
 */
export function clearAuthToken(): void {
  memoryToken = null;
  if (isBrowser()) {
    try {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
      window.sessionStorage.removeItem(ROLE_STORAGE_KEY);
      window.sessionStorage.removeItem(MERCHANT_STORAGE_KEY);
    } catch {
      // Ignore storage errors
    }
  }
}

/**
 * Get active operator role.
 */
export function getActiveRole(): string {
  if (isBrowser()) {
    try {
      const stored = window.sessionStorage.getItem(ROLE_STORAGE_KEY);
      if (stored) return stored;
    } catch {
      // Fallback
    }
  }
  return memoryRole;
}

/**
 * Set active operator role.
 */
export function setActiveRole(role: string): void {
  memoryRole = role;
  if (isBrowser()) {
    try {
      window.sessionStorage.setItem(ROLE_STORAGE_KEY, role);
    } catch {
      // Fallback
    }
  }
}

/**
 * Get active merchant slug.
 */
export function getActiveMerchantSlug(): string {
  if (isBrowser()) {
    try {
      const stored = window.sessionStorage.getItem(MERCHANT_STORAGE_KEY);
      if (stored) return stored;
    } catch {
      // Fallback
    }
  }
  return memoryMerchant;
}

/**
 * Set active merchant slug.
 */
export function setActiveMerchantSlug(slug: string): void {
  memoryMerchant = slug;
  if (isBrowser()) {
    try {
      window.sessionStorage.setItem(MERCHANT_STORAGE_KEY, slug);
    } catch {
      // Fallback
    }
  }
}

/**
 * Build standard authentication headers.
 * In production: returns authentic bearer token or empty headers.
 * Under no circumstances does this return hardcoded ray_test_* tokens in production.
 */
export function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  const headers: Record<string, string> = {};

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const merchantSlug = getActiveMerchantSlug();
  if (merchantSlug) {
    headers["X-Merchant-ID"] = merchantSlug;
  }

  return headers;
}
