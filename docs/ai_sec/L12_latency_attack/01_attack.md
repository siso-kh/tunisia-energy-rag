# L12: Latency Attack — Attack Vectors

> **Loss:** Latency Attack
> **Category:** Availability / Service Degradation
> **Severity:** MEDIUM
> **Test Date:** 2026-08-23
> **Vectors:** 15

---

## Attack Surface

The system processes each chat request through a 3-step pipeline:

1. **Query Rewriting** — LLM call (~2-5s)
2. **Hybrid Retrieval** — ChromaDB + BM25 (~0.5-2s)
3. **Answer Generation** — LLM call (~3-10s)

**Total per-request latency:** 5-17 seconds (simple query) to 30-60s (complex query)

### Resource Constraints

| Resource | Limit | Impact of Exhaustion |
|----------|-------|---------------------|
| DB connections | pool_size=10, max_overflow=20 (total 30) | Requests queue/fail |
| LLM API calls | External Mistral API | Rate limiting from provider |
| Memory | Server RAM | SSE buffering for streams |
| Event loop | Single-threaded asyncio | CPU-bound tasks block all |

---

## Attack Vectors

### Category 1: Slow Queries (V1-V3)

| ID | Technique | Query | Expected Latency |
|----|-----------|-------|------------------|
| V1 | Complex reasoning | Multi-page analysis request | >15s |
| V2 | Multi-language | French + English + Arabic response | >10s |
| V3 | Context heavy | Cross-reference all documents | >20s |

**How it works:**
1. Attacker sends queries that force complex reasoning
2. LLM generates long responses (more tokens = more time)
3. Each request holds a DB connection for the entire duration
4. Slow queries block the event loop if not properly async

### Category 4: Connection Pool Exhaustion (V4-V6)

| ID | Technique | Concurrency | Expected Impact |
|----|-----------|-------------|-----------------|
| V4 | Sequential slow queries | 15 sequential | Pool exhaustion after 10 |
| V5 | Parallel slow queries | 15 parallel | Immediate pool exhaustion |
| V6 | Parallel streaming | 20 parallel streams | Streams hold connections longer |

**How it works:**
1. Database pool has 10 base + 20 overflow = 30 max connections
2. Each chat request holds a connection for 5-30 seconds
3. 15 parallel requests = 15 connections (exceeds pool_size)
4. Overflow connections are created but may cause contention
5. >30 requests = connection timeout/failure

**Impact:**
- Requests queue waiting for connections
- New connections take time to establish
- Server becomes unresponsive for new requests

### Category 7: Streaming Hold (V7-V9)

| ID | Technique | Concurrent | Hold Time | Expected Impact |
|----|-----------|------------|-----------|-----------------|
| V7 | Single held stream | 1 | 30s | 1 connection held |
| V8 | Multiple held streams | 10 | 15s | 10 connections held |
| V9 | Rapid reconnect | 20 cycles | instant | Resource leak potential |

**How it works:**
1. Attacker opens SSE stream but doesn't read events
2. Stream holds DB connection + server memory
3. Multiple held streams = multiple locked connections
4. Rapid connect/disconnect may cause resource leaks

### Category 10: Response Time Degradation (V10-V12)

| ID | Technique | Concurrency | Expected Impact |
|----|-----------|-------------|-----------------|
| V10 | Baseline measurement | 1 | Establish baseline |
| V11 | Under load (5 concurrent) | 5 | Slight degradation |
| V12 | Heavy load (10 concurrent slow) | 10 | Severe degradation |

**How it works:**
1. Measure response time under normal conditions
2. Add concurrent load to increase latency
3. Compare before/after to quantify degradation

### Category 13: Edge Cases (V13-V15)

| ID | Technique | Payload | Expected Impact |
|----|-----------|---------|-----------------|
| V13 | Empty query | `""` | Fast rejection (400) |
| V14 | Whitespace only | `"   "` | Fast rejection (400) |
| V15 | SQL injection | `SELECT * FROM...` | Processed normally |

---

## Risk Assessment

| Category | Risk | Rationale |
|----------|------|-----------|
| Slow Queries | **MEDIUM** | Legitimate use case, but can be weaponized |
| Pool Exhaustion | **HIGH** | 30 connections exhausted by 30 parallel requests |
| Streaming Hold | **HIGH** | Streams hold connections indefinitely |
| Latency Degradation | **MEDIUM** | Depends on server capacity |
| Edge Cases | **LOW** | Fast rejection |

---

## Expected Verdict

**🟡 YELLOW — Partially Protected**

- ✅ Empty/whitespace queries rejected quickly
- ⚠️ No per-request timeout on server
- ⚠️ No connection pool monitoring/alerting
- ⚠️ Streaming connections held indefinitely
- ⚠️ No query complexity limiting

---

## Recommendations

1. **Per-request timeout** — Kill requests taking >60s
2. **Connection pool monitoring** — Alert when >80% utilized
3. **Streaming timeout** — Auto-close streams after 5 minutes
4. **Query complexity limit** — Reject queries >10KB
5. **Request prioritization** — Simple queries get priority
