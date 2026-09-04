# RAY Control Plane — Production Network, DNS & TLS Specification

**Status**: `REQUIRES EXTERNAL INFRASTRUCTURE`  
**Classification**: Network Architecture & External Ingress Runbook  

---

## 1. Network Topography & Ingress Architecture

```
                                  [Internet]
                                       │
                         [Public DNS: ray.fintech.company]
                                       │
                                       ▼
                       [Cloudflare / AWS CloudFront]
                         (DDoS Protection, WAF)
                                       │
                                       ▼
                       [Reverse Proxy / Nginx Ingress]
                         - TLS 1.3 Termination (Let's Encrypt / ACM)
                         - HSTS (max-age=31536000; includeSubDomains; preload)
                         - Structured JSON access logging with X-Correlation-ID
                                 /           \
                                /             \
                   Path: /api/ *               Path: /*
                              ▼                 ▼
                     [ray_api:8000]       [ray_web:3000]
                      (FastAPI Core)      (Next.js GUI)
                              │
                              ▼
                     [ray_postgres:5432]
```

---

## 2. DNS Configuration Requirements

To point external traffic and Razorpay webhooks to the RAY control plane:

| Record Type | Host | Target / Value | Purpose |
| :--- | :--- | :--- | :--- |
| **A** | `ray.yourdomain.com` | `203.0.113.10` (Static Public Elastic IP) | Primary Ingress VIP |
| **CNAME** | `api.ray.yourdomain.com` | `ray.yourdomain.com` | API sub-domain routing |
| **CAA** | `@` | `0 issue "letsencrypt.org"` | Restrict CA issuance to Let's Encrypt |

---

## 3. TLS / HTTPS Certificate Generation

### Automatic ACME / Let's Encrypt Setup (Certbot)
```bash
# On the public ingress host:
certbot certonly --standalone \
  --preferred-challenges http \
  -d ray.yourdomain.com \
  --email security@yourdomain.com \
  --agree-tos --non-interactive

# Mount certificates into deploy/nginx/ssl:
# fullchain.pem -> deploy/nginx/ssl/ray.crt
# privkey.pem   -> deploy/nginx/ssl/ray.key
```

---

## 4. Razorpay Webhook Configuration in Dashboard

1. Log in to the [Razorpay Merchant Dashboard](https://dashboard.razorpay.com/).
2. Navigate to **Settings** $\to$ **Webhooks** $\to$ **Add New Webhook**.
3. **Webhook URL**:
   ```
   https://ray.yourdomain.com/api/v1/webhooks/razorpay
   ```
4. **Secret**: Enter a cryptographically random 32+ character secret (e.g. generated via `openssl rand -hex 32`).
5. Set this secret in the RAY environment:
   ```bash
   RAZORPAY_WEBHOOK_SECRET="<generated_secret>"
   ```
6. **Active Events**:
   - `payment.authorized`
   - `payment.failed`
   - `payment.captured`
   - `order.paid`
7. **Alert Email**: Configure on-call team alias.

---

## 5. Required External Actions (Infrastructure Checklist)

- [ ] Register public domain name.
- [ ] Provision static public IP on cloud provider (AWS EC2 / GCP Compute / Bare Metal).
- [ ] Configure DNS A-Record pointing `ray.yourdomain.com` $\to$ Public IP.
- [ ] Open inbound ports 80 and 443 on cloud firewall / security group.
- [ ] Run `docker-compose -f docker-compose.prod.yml up -d`.
- [ ] Verify TLS handshake: `curl -Iv https://ray.yourdomain.com/health`.
