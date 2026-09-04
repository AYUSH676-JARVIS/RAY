/**
 * RAY — Canonical Browser API Client & Gateway Routing Layer
 *
 * Implements:
 * 1. Single canonical API base URL.
 * 2. Same-origin routing (/api/*) in production behind NGINX.
 * 3. Prevention of Docker-internal hostnames (e.g. http://api:8000) leaking to browser.
 * 4. Automatic injection of X-Correlation-ID and Authorization headers.
 * 5. Clean error handling and typed responses.
 */

import { getAuthHeaders, setAuthToken, getActiveRole, getActiveMerchantSlug } from "./auth";

/**
 * Determine the canonical base URL for API requests.
 */
export function getApiBaseUrl(): string {
  const envBase = process.env.NEXT_PUBLIC_API_BASE || process.env.NEXT_PUBLIC_API_URL;
  if (envBase) {
    const trimmed = envBase.trim().replace(/\/+$/, "");
    // Prevent Docker internal hostnames from leaking to the browser
    if (trimmed.includes("http://api:") || trimmed.includes("http://ray_api:")) {
      return "";
    }
    return trimmed;
  }

  if (typeof window !== "undefined") {
    // If the browser is running on port 80/443 or standard domain, use same-origin relative path
    if (window.location.port === "80" || window.location.port === "443" || window.location.port === "") {
      return "";
    }
  }

  return "http://127.0.0.1:8000";
}

/**
 * Generate a random correlation ID for distributed request tracing.
 */
export function generateCorrelationId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `ray_req_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
  }
  return `ray_req_${Math.random().toString(36).substring(2, 14)}`;
}

/**
 * Canonical fetch wrapper that enforces authentication and correlation headers.
 */
export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const baseUrl = getApiBaseUrl();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = `${baseUrl}${normalizedPath}`;

  const headers = new Headers(options.headers || {});

  // Correlation tracing
  if (!headers.has("X-Correlation-ID")) {
    headers.set("X-Correlation-ID", generateCorrelationId());
  }

  // Ensure JSON headers for mutation requests
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  // Attach authentication headers if not explicitly supplied
  if (!headers.has("Authorization")) {
    const authHeaders = getAuthHeaders();
    for (const [key, value] of Object.entries(authHeaders)) {
      if (!headers.has(key)) {
        headers.set(key, value);
      }
    }
  }

  return fetch(url, {
    ...options,
    headers,
  });
}

/**
 * Initialize or refresh the user's session with a genuine signed JWT.
 * In development/demo mode, requests a signed JWT from the backend.
 */
export async function initializeSession(role?: string, merchantSlug?: string): Promise<string> {
  const targetRole = role || getActiveRole();
  const targetMerchant = merchantSlug || getActiveMerchantSlug();

  const baseUrl = getApiBaseUrl();
  const res = await fetch(`${baseUrl}/api/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      merchant_slug: targetMerchant,
      role: targetRole,
    }),
  });

  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`Authentication failed (${res.status}): ${errorText}`);
  }

  const data = await res.json();
  if (data.access_token) {
    setAuthToken(data.access_token);
    return data.access_token;
  }
  throw new Error("No access_token returned by auth endpoint");
}
