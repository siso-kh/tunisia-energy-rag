# L13: DB Overwhelmed — Attack Vectors

> **Loss:** DB Overwhelmed
> **Category:** Availability / Resource Exhaustion
> **Severity:** HIGH
> **Test Date:** 2026-08-23
> **Vectors:** 15

---

## Attack Surface

The system uses PostgreSQL with SQLAlchemy async engine:

```
Connection Pool:
├── pool_size = 10      (base connections)
├── max_overflow = 20   (overflow connections)
├── Total max = 30      (connections)
├── Connection timeout = 30s (default)
└── Pool recycle = None (no recycle)
```

### Database Operations

| Operation | Endpoint | Session Usage |
|-----------|----------|---------------|
| Chat | `/api/chat` | Hold for ~5-30s |
| Stream | `/api/chat/stream` | Hold for ~10-60s |
| Auth | `/api/auth/*` | Hold for ~0.1s |
| Conversations | `/api/conversations` | Hold for ~0.1s |
| Admin | `/api/admin/*` | Hold for ~0.1-5s |
| Outages | `/api/outages` | Hold for ~0.1s |

---

## Attack Vectors

### Category 1: Connection Pool Exhaustion (V1-V3)

| ID | Technique | Concurrency | Expected |
|----|-----------|-------------|----------|
| V1 | Chat requests only | 35 parallel | Pool exhaustion after 30 |
| V2 | Mixed endpoints | 40 parallel | Pool shared, faster exhaustion |
| V3 | Streaming requests | 25 parallel | Streams hold connections longer |

**How it works:**
1. Send N parallel requests
2. Each request holds a DB connection for 5-60 seconds
3. After 30 connections, new requests queue or fail
4. Pool overflow creates temporary connections (up to 20)

### Category 4: Long-Running Connection Hold (V4-V6)

| ID | Technique | Duration | Expected |
|----|-----------|----------|----------|
| V4 | Slow chat requests | 15s × 10 | 10 connections held |
| V5 | Held streams | 30s × 5 | 5 connections held |
| V6 | Sequential slow | 10s × 5 | Pool under constant load |

**How it works:**
1. Send requests that take long to process
2. Each request holds a connection for the entire duration
3. Slow requests = long connection hold = pool exhaustion

### Category 7: Stale Connection Detection (V7-V9)

| ID | Technique | Duration | Expected |
|----|-----------|----------|----------|
| V7 | Idle connections | 60s idle | Test connection recycling |
| V8 | Rapid reuse | 20 sequential | Test connection pooling |
| V9 | Error recovery | Error → valid | Test connection release |

**How it works:**
1. Open connection, hold idle, then use
2. Test if stale connections cause errors
3. Test if connections are properly released after errors

### Category 10: Query Timeout (V10-V12)

| ID | Technique | Concurrency | Expected |
|----|-----------|-------------|----------|
| V10 | Simple query (baseline) | 1 | <10s |
| V11 | Complex query (baseline) | 1 | <30s |
| V12 | Under load | 5 | Latency increase |

**How it works:**
1. Measure query execution time
2. Test if slow queries are killed
3. Test if query timeout is configurable

### Category 13: Cross-Endpoint Contention (V13-V15)

| ID | Technique | Load | Expected |
|----|-----------|------|----------|
| V13 | Chat + Auth | 20+10 | Auth blocked by chat |
| V14 | Chat + Admin | 20+10 | Admin blocked by chat |
| V15 | All endpoints | 15×3=45 | Complete exhaustion |

**How it works:**
1. Mix different endpoint types in parallel
2. Test if pool is shared or isolated
3. Measure impact on each endpoint

---

## Risk Assessment

| Category | Risk | Rationale |
|----------|------|-----------|
| Pool Exhaustion | **HIGH** | Pool is small (30), easy to exhaust |
| Long-Running Hold | **HIGH** | No connection timeout |
| Stale Connections | **MEDIUM** | Depends on PostgreSQL settings |
| Query Timeout | **MEDIUM** | No explicit timeout configured |
| Cross-Endpoint | **HIGH** | Pool is shared across all endpoints |

---

## Expected Verdict

**🔴 RED — VULNERABLE**

- ⚠️ No connection timeout configured
- ⚠️ No query timeout configured
- ⚠️ Pool shared across all endpoints
- ⚠️ Streaming holds connections longer
- ❌ No pool monitoring/alerting
