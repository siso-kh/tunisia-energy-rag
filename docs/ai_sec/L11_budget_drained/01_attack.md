# L11: Budget Drained — Attack Vectors

> **Loss:** Budget Drained
> **Category:** Availability / Cost Amplification
> **Severity:** HIGH
> **Test Date:** 2026-08-23
> **Vectors:** 15

---

## Attack Surface

The system uses an external LLM API (Mistral Large) for query rewriting and
answer generation. Each chat request makes **2 LLM calls**:

1. **Query rewriting** — converts user query to standalone query
2. **Answer generation** — generates response with RAG context

### Cost Model

| Component | Token Usage | Cost Driver |
|-----------|-------------|-------------|
| Query rewriting | ~200-500 tokens | Per request |
| Answer generation | ~500-2000 tokens | Per request |
| Chat history | Up to 1500 tokens | Token budget |
| RAG context | ~500 tokens | 5 chunks |

**Estimated cost per request:** ~1000-4000 tokens (0.0001-0.0004 USD at Mistral pricing)

### Current Defenses

| Defense | Mechanism | Configuration |
|---------|-----------|---------------|
| Rate limiting | slowapi | `RATE_LIMIT_CHAT = 10/minute` |
| Token budget | `get_optimized_history()` | `HISTORY_TOKEN_BUDGET = 1500` |
| LLM call limit | None | No per-user budget cap |
| Cost tracking | Prometheus counters | `LLM_TOKENS` metric |

---

## Attack Vectors

### Category 1: Expensive Queries (V1-V3)

| ID | Technique | Query | Why It Matters |
|----|-----------|-------|----------------|
| V1 | Maximize tokens | 5000-char query with every topic | Each token costs money |
| V2 | Complex reasoning | Multi-part analysis request | Forces LLM to generate long response |
| V3 | Multi-turn expensive | Large history + expensive query | History tokens + query tokens = max cost |

**How it works:**
1. Attacker sends extremely long queries to maximize token usage
2. Each query makes 2 LLM calls (rewrite + generate)
3. Long queries = more input tokens = higher cost
4. Complex questions = longer responses = more output tokens

### Category 2: Rate Limit Bypass (V4-V6)

| ID | Technique | Method | Why It Matters |
|----|-----------|--------|----------------|
| V4 | IP rotation | X-Forwarded-For header | Bypass IP-based rate limiting |
| V5 | Different endpoints | /api/chat/stream | Bypass per-route limits |
| V6 | Different methods | GET on /api/chat | Test method-based bypass |

**How it works:**
1. Attacker rotates IP addresses via proxy headers
2. Each new IP gets fresh rate limit quota
3. If `TRUST_PROXY_HEADERS=true`, rate limiting is based on X-Forwarded-For
4. Attacker can send unlimited requests with rotating IPs

### Category 3: Cost Amplification (V7-V9)

| ID | Technique | Method | Why It Matters |
|----|-----------|--------|----------------|
| V7 | Repeated expensive | Same query 5 times | Each repetition costs money |
| V8 | Large history + expensive | Max history + complex query | Maximum token usage |
| V9 | Context injection | Force expensive retrieval | Maximize RAG context tokens |

**How it works:**
1. Attacker sends same expensive query repeatedly
2. Rate limiting should kick in after ~10 requests/minute
3. But attacker can wait and send in bursts
4. No per-user budget cap means unlimited spending

### Category 4: Concurrent Attack (V10-V12)

| ID | Technique | Concurrency | Why It Matters |
|----|-----------|-------------|----------------|
| V10 | Parallel chat | 10 simultaneous | Overwhelm rate limiter |
| V11 | Parallel stream | 5 simultaneous | Streaming may have separate limits |
| V12 | Mixed endpoints | 8 mixed requests | Test shared vs separate limits |

**How it works:**
1. Attacker sends multiple requests simultaneously
2. Rate limiter may not handle concurrency correctly
3. If rate limiter is per-request (not per-second), burst gets through
4. Each concurrent request = 2 LLM calls = real cost

### Category 5: Edge Cases (V13-V15)

| ID | Technique | Payload | Why It Matters |
|----|-----------|---------|----------------|
| V13 | Oversized payload | 50KB query | May bypass size limits |
| V14 | Massive history | 100 messages | Token budget should truncate |
| V15 | Unicode bomb | Chinese chars × 1000 | Different tokenization = more tokens |

**How it works:**
1. Oversized payloads may not be rejected
2. Massive histories trigger token budget truncation
3. Unicode characters may tokenize differently (1-2 tokens each)
4. Attacker uses Unicode to increase token count

---

## Risk Assessment

| Category | Risk | Rationale |
|----------|------|-----------|
| Expensive Queries | **HIGH** | No per-user budget cap |
| Rate Limit Bypass | **MEDIUM** | Depends on TRUST_PROXY_HEADERS config |
| Cost Amplification | **HIGH** | Repeated requests drain budget |
| Concurrent Attack | **MEDIUM** | Rate limiter may handle concurrency |
| Edge Cases | **LOW** | Token budget and size limits should apply |

---

## Expected Verdict

**🟡 YELLOW — Partially Protected**

- ✅ Rate limiting works (10/minute default)
- ✅ Token budget limits history (1500 tokens)
- ⚠️ No per-user budget cap
- ⚠️ Rate limit may be bypassed via proxy headers
- ⚠️ No cost tracking per user

---

## Recommendations

1. **Per-user budget cap** — Limit LLM spending per user per day/month
2. **Cost tracking** — Track token usage per user, not just globally
3. **Query length limit** — Reject queries over 10KB
4. **Concurrent request limit** — Limit to 3-5 concurrent requests per user
5. **Burst protection** — Rate limit by second, not just minute
