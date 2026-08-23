# L14: Rate Limit Bypass — Attack Vectors

## Overview

This document describes the attack vectors used to test whether the rate limiting system can be circumvented.

## Attack Categories

### 1. IP Spoofing (V1)

**Objective:** Bypass rate limiting by spoofing client IP addresses.

**Technique:**
- Send requests with fake `X-Forwarded-For` headers
- Each request appears to come from a different client
- If `TRUST_PROXY_HEADERS=true`, the rate limiter uses the spoofed IP

**Why It Works:**
- Rate limiting is IP-based
- `X-Forwarded-For` can be spoofed by any client
- No validation of the header value

**Expected Result:** Rate limit bypassed if `TRUST_PROXY_HEADERS=true`

---

### 2. IPv4/IPv6 Confusion (V2)

**Objective:** Bypass rate limiting by using different IP representations.

**Technique:**
- Send requests with different IP formats:
  - `127.0.0.1` (IPv4)
  - `::1` (IPv6)
  - `::ffff:127.0.0.1` (IPv4-mapped IPv6)
  - `0:0:0:0:0:0:0:1` (full IPv6)
- Each format might be treated as a different client

**Why It Works:**
- IP parsing libraries may normalize differently
- Rate limiter might not normalize IPs before comparison

**Expected Result:** Rate limit bypassed if formats treated differently

---

### 3. Header Manipulation (V3)

**Objective:** Bypass rate limiting by manipulating header formats.

**Technique:**
- Send requests with different header case variations:
  - `x-forwarded-for` (lowercase)
  - `X-Forwarded-For` (mixed case)
  - `X-FORWARDED-FOR` (uppercase)
- HTTP headers are case-insensitive, but the rate limiter might not handle this

**Why It Works:**
- Header parsing might be case-sensitive
- Different cases might create different keys

**Expected Result:** Rate limit bypassed if cases treated differently

---

### 4. Burst Attack (V4)

**Objective:** Bypass rate limiting by sending many concurrent requests.

**Technique:**
- Send 20 concurrent requests simultaneously
- Rate limiter might not check requests fast enough
- Some requests might get through before the counter is updated

**Why It Works:**
- In-memory rate limiting has race conditions
- Async request handling might not be atomic
- Counter updates are not instantaneous

**Expected Result:** Rate limit bypassed if >10/20 succeed

---

### 5. Timing Exploit (V5)

**Objective:** Bypass rate limiting by timing requests at window boundaries.

**Technique:**
- Exhaust the rate limit
- Wait for the window to reset (1 minute)
- Send requests at the exact reset time
- Might get double the allowance

**Why It Works:**
- Rate limiters use fixed time windows
- Window boundaries are predictable
- Clock skew can affect timing

**Expected Result:** Rate limit bypassed if >3/5 succeed

---

### 6. Endpoint Confusion (V6)

**Objective:** Bypass rate limiting by using different endpoints.

**Technique:**
- Send requests to different endpoints:
  - `/api/chat`
  - `/api/conversations`
  - `/api/sources`
  - `/api/health`
- If limits are per-endpoint, we get separate allowances

**Why It Works:**
- Rate limits might be defined per-route
- Different endpoints might have different limits
- No global rate limiting across all endpoints

**Expected Result:** Rate limit bypassed if >10/12 succeed

---

### 7. Method Confusion (V7)

**Objective:** Bypass rate limiting by using different HTTP methods.

**Technique:**
- Send POST requests to `/api/chat`
- Send GET requests to `/api/chat`
- If methods are treated differently, we get separate allowances

**Why It Works:**
- Rate limits might be defined per-method
- GET and POST might be counted separately
- SlowAPI might not aggregate methods

**Expected Result:** Rate limit bypassed if >5/10 succeed

---

### 8. Slow Drip (V8)

**Objective:** Bypass rate limiting by sending requests slowly over time.

**Technique:**
- Send 1 request per second for 60 seconds
- Stay below the burst limit
- Accumulate many requests over time

**Why It Works:**
- Rate limiter might only check bursts
- Sliding window might not work correctly
- Counter might reset before it's checked

**Expected Result:** Rate limit bypassed if >30/60 succeed

---

## Test Execution

```bash
# Run L14 exploit tests
python tests/exploit_rate_limit_bypass.py
```

## Expected Outcomes

| Vector | Expected | Risk |
|--------|----------|------|
| V1 XFF Spoofing | VULNERABLE if TRUST_PROXY_HEADERS=true | 🔴 HIGH |
| V2 IPv4/IPv6 | VULNERABLE if formats treated differently | 🔴 HIGH |
| V3 Header Case | REFUSED (headers are case-insensitive) | 🟢 LOW |
| V4 Burst Attack | VULNERABLE if race condition exists | 🔴 HIGH |
| V5 Timing Exploit | REFUSED (window boundaries are strict) | 🟢 LOW |
| V6 Endpoint Confusion | VULNERABLE if per-endpoint limits | 🟡 MEDIUM |
| V7 Method Confusion | REFUSED (methods share limits) | 🟢 LOW |
| V8 Slow Drip | VULNERABLE if sliding window broken | 🔴 HIGH |

## References

- OWASP Rate Limiting Cheat Sheet
- SlowAPI Documentation
- HTTP/1.1 Request Smuggling (for header manipulation context)
