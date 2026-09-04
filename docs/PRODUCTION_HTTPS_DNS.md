# RAY — Production HTTPS, DNS & Network Architecture
*Production Ingress, TLS & Infrastructure Guide*

---

## 1. Production Topology

```
Internet / Gateway (Razorpay Webhooks, Merchant Admins, API Consumers)
                      |
                      v
       [DNS Anycast Layer (Cloudflare / Route53)]
       A / AAAA Records -> Ingress IP
       CAA Record: "0 issue letsencrypt.org"
                      |
                      v
       [External TLS 1.3 Termination & DDoS Edge]
       (Cloudflare Edge / AWS Application Load Balancer)
                      |
                      v
       [Internal Reverse Proxy (Nginx / Caddy)]
       - Strict HTTP -> HTTPS (301 Permanent Redirect)
       - HSTS (Strict-Transport-Security: max-age=63072000)
       - Trusted Proxy Header Forwarding (X-Forwarded-For, X-Real-IP)
       - Strict RAW Webhook Body Preservation (client_max_body_size: 1MB)
                      |
        +-------------+-------------+
        |                           |
        v                           v
  [FastAPI Backend]          [Next.js Frontend]
  Port: 8000                 Port: 3000
        |
        v
  [PostgreSQL 16 & Background Workers]
```

---

## 2. External DNS Configuration Required

To deploy in a live merchant environment, the following DNS records must be configured with your registrar/DNS provider (e.g. Cloudflare, AWS Route 53):

| Record Type | Host / Name | Target / Value | Purpose |
|---|---|---|---|
| `A` | `ray.merchant.com` | `<Ingress Load Balancer IP>` | Merchant operations console & API |
| `CNAME` | `api.ray.merchant.com` | `ray.merchant.com` | Dedicated API & Webhook hostname |
| `CAA` | `@` | `0 issue "letsencrypt.org"` | Prevent unauthorized SSL certificate issuance |
| `TXT` | `_dmarc.merchant.com` | `v=DMARC1; p=reject; ...` | Email and security domain protection |

> **IMPORTANT**: The repository does not claim a live public domain exists in this sandbox. Public DNS propagation and CA issuance must be performed in your cloud provider environment.

---

## 3. Reverse Proxy Security Controls

### HSTS & Headers
- **Strict-Transport-Security**: `max-age=63072000; includeSubDomains; preload`
- **X-Content-Type-Options**: `nosniff`
- **X-Frame-Options**: `DENY`
- **Referrer-Policy**: `strict-origin-when-cross-origin`

### Webhook Path Integrity
Razorpay and other gateway webhooks rely on bit-for-bit raw body byte preservation for HMAC-SHA256 verification. The reverse proxy config ensures:
1. `proxy_request_buffering on` (ensures full payload is read before dispatch).
2. No body rewriting or character re-encoding.
3. Strict 1MB payload ceiling preventing buffer exhaustion attacks.

### Trusted Proxy Headers
When deployed behind an AWS ALB or Cloudflare:
- Nginx config sets `real_ip_header X-Forwarded-For;` and whitelists private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
- FastAPI `trusted_host` middleware reads the authoritative remote IP for rate limiting and audit logs.

---

## 4. Local Staging & Development Testing

For local HTTPS testing with self-signed or internal CA certificates:

```bash
# Generate local development certificates:
mkdir -p deploy/nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout deploy/nginx/ssl/ray_key.pem \
  -out deploy/nginx/ssl/ray_cert.pem \
  -subj "/CN=localhost/O=RAY Security"
```
