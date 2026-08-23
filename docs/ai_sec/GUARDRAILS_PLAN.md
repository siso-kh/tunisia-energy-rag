# Guardrails Implementation Plan — Tunisia Energy RAG

> **Date:** 2026-08-23
> **Status:** Ready for implementation
> **Priority:** 6 critical vulnerabilities to fix

---

## Executive Summary

The security testing identified **6 critical vulnerabilities** requiring immediate guardrails. This plan organizes fixes into three phases:

| Phase | Timeline | Focus | Impact |
|---|---|---|---|
| **P0: Emergency** | Week 1 | Block active exploitation | Stops attacks in progress |
| **P1: Defense-in-depth** | Week 2-3 | Reduce attack surface | Hardens system against future attacks |
| **P2: Monitoring** | Week 4+ | Detect and respond | Enables ongoing security posture |

---

## Phase 1: Emergency Fixes (Week 1)

### 1.1 Fix L4: IDOR on Conversations (CRITICAL)

**File:** `src/api/main.py`

**Current Code (Vulnerable):**
```python
@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(conversation_id: uuid.UUID, session=Depends(get_db_dependency)):
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationDetailOut(...)
```

**Fixed Code:**
```python
@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    # CRITICAL: Owner validation
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    if conversation.owner_id != owner.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    return ConversationDetailOut(...)
```

**Effort:** 5 lines
**Impact:** Prevents any user from reading other users' conversations

---

### 1.2 Fix L11: Add Rate Limiting to Streaming Endpoint (CRITICAL)

**File:** `src/api/main.py`

**Current Code (Vulnerable):**
```python
@app.post("/api/chat/stream")
@limiter.limit(CHAT_LIMIT)
async def chat_stream_endpoint(...):
```

**Issue:** The `@limiter.limit` decorator is applied but may not work correctly with streaming responses. Need to add manual rate limiting inside the handler.

**Fixed Code:**
```python
@app.post("/api/chat/stream")
async def chat_stream_endpoint(
    request: Request,
    payload: QueryRequest,
    user: Optional[User] = Depends(get_current_user_optional),
):
    # Manual rate limiting for streaming (decorators don't work with StreamingResponse)
    from src.api.ratelimit import limiter
    if limiter.enabled:
        # Check rate limit manually
        key = await limiter._key_func(request)
        if await limiter._rate_limiter.is_rate_limited(key, CHAT_LIMIT):
            raise HTTPException(status_code=429, detail="Too many requests")
    
    user_query = payload.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    # ... rest of handler
```

**Effort:** 10 lines
**Impact:** Prevents unlimited LLM spending via streaming

---

### 1.3 Fix L13: Increase DB Pool Size (CRITICAL)

**File:** `src/database/connection.py`

**Current Code:**
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
)
```

**Fixed Code:**
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=30,           # Was 10 — handle 30 concurrent connections
    max_overflow=20,        # Keep overflow for burst handling
    pool_recycle=3600,      # Recycle connections every hour
    pool_pre_ping=True,     # Detect stale connections before use
    pool_timeout=30,        # Wait max 30s for a connection
)
```

**Effort:** 1 line (change pool_size)
**Impact:** Prevents pool exhaustion at 11 concurrent requests

---

### 1.4 Fix L14: Disable XFF Spoofing (CRITICAL)

**File:** `.env`

**Current Setting:**
```
TRUST_PROXY_HEADERS=true
```

**Fixed Setting:**
```
TRUST_PROXY_HEADERS=false
```

**Effort:** 1 line
**Impact:** Blocks IP spoofing via X-Forwarded-For headers

---

### 1.5 Fix L14: Add Concurrency Limiter (CRITICAL)

**File:** `src/api/main.py`

**New Code:**
```python
import asyncio

# Global concurrency limiter — prevents pool exhaustion from burst traffic
_concurrency_semaphore = asyncio.Semaphore(15)  # Max 15 concurrent LLM requests

@app.post("/api/chat")
@limiter.limit(CHAT_LIMIT)
async def chat_endpoint(...):
    async with _concurrency_semaphore:
        # ... existing handler code

@app.post("/api/chat/stream")
async def chat_stream_endpoint(...):
    async with _concurrency_semaphore:
        # ... existing handler code
```

**Effort:** 10 lines
**Impact:** Prevents burst attacks from exhausting DB pool

---

## Phase 2: Defense-in-Depth (Week 2-3)

### 2.1 Build Guardrails Module

**File:** `src/rag/guardrails.py` (NEW)

```python
"""Guardrails layer for the Tunisia Energy RAG system.

Provides:
- Query sanitization (detect/block prompt injection)
- Output validation (block leaked content)
- Domain enforcement (energy sector only)
- Credential leak detection
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GuardrailResult:
    """Result of guardrail checks."""
    allowed: bool
    sanitised_query: Optional[str] = None
    blocked: bool = False
    block_reason: Optional[str] = None
    detections: List[str] = field(default_factory=list)


# ─── Injection Patterns ────────────────────────────────────────────────────────

INJECTION_PATTERNS = [
    # Direct extraction
    (r"ignore\s+(previous|all|your)\s+instructions", "INJECTION:ignore_instructions"),
    (r"print\s+(your|the)\s+(system\s+)?prompt", "INJECTION:print_prompt"),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "INJECTION:reveal_prompt"),
    (r"show\s+me\s+(your|the)\s+(system\s+)?prompt", "INJECTION:show_prompt"),
    (r"output\s+(your|the)\s+instructions", "INJECTION:output_instructions"),
    (r"repeat\s+(everything|all)\s+above", "INJECTION:repeat_above"),
    (r"translate\s+(your|the)\s+instructions", "INJECTION:translate_instructions"),
    
    # Role hijacking
    (r"you\s+are\s+now\s+a\s+general", "INJECTION:role_hijack"),
    (r"forget\s+your\s+(role|instructions)", "INJECTION:forget_role"),
    (r"act\s+as\s+(a\s+)?different\s+assistant", "INJECTION:role_switch"),
    (r"from\s+now\s+on\s+you\s+will\s+answer\s+all", "INJECTION:constraint_removal"),
    
    # Instruction override
    (r"do\s+not\s+use\s+the\s+provided\s+context", "INJECTION:context_bypass"),
    (r"ignore\s+the\s+rule\s+about", "INJECTION:rule_override"),
    (r"the\s+context\s+is\s+wrong", "INJECTION:context_dismissal"),
    (r"answer\s+from\s+your\s+(own\s+)?knowledge", "INJECTION:knowledge_override"),
    
    # Credential extraction
    (r"what\s+is\s+the\s+(admin|jwt|api|database)\s*(key|secret)", "INJECTION:credential_extract"),
    (r"print\s+(all\s+)?environment\s+variables", "INJECTION:env_vars"),
    (r"reveal\s+the\s+database\s+connection", "INJECTION:db_extract"),
    
    # Debug/fake mode
    (r"enter\s+debug\s+mode", "INJECTION:debug_mode"),
    (r"simulation\s+mode", "INJECTION:simulation_mode"),
    (r"daniels?\s+mode", "INJECTION:daniels_mode"),
]

# ─── Energy Domain Keywords ────────────────────────────────────────────────────

ENERGY_KEYWORDS = [
    # English
    "energy", "solar", "wind", "renewable", "photovoltaic", "electricity",
    "power", "grid", "turbine", "battery", "storage", "efficiency",
    "consumption", "generation", "transmission", "distribution",
    "carbon", "emission", "climate", "sustainability",
    
    # French
    "énergie", "solaire", "éolien", "renouvelable", "photovoltaïque",
    "électricité", "puissance", "réseau", "turbine", "batterie",
    "stockage", "efficacité", "consommation", "génération",
    "transmission", "distribution", "carbone", "émission",
    
    # Arabic
    "طاقة", "شمسية", "رياح", "تجددة", "كهرباء", "شبكة",
    "турبين", "بطارية", "تخزين", "كفاءة", "استهلاك",
    
    # Domain-specific
    "anme", "steg", "cder", "cirt", "tunisia", "tunisian",
    "tunisie", "tunisien", " STEG ", "ANME",
]


def check_query(query: str) -> GuardrailResult:
    """Main entry point for query validation.
    
    Args:
        query: User's input query
        
    Returns:
        GuardrailResult with allowed/blocked status and detections
    """
    detections = []
    sanitised = query
    
    # 1. Check for injection patterns
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            detections.append(label)
    
    # 2. Check query length
    if len(query) > 10_000:
        detections.append("QUERY_TOO_LONG")
    
    # 3. Check for empty query
    if not query.strip():
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Empty query",
            detections=["EMPTY_QUERY"],
        )
    
    # 4. Block if critical detections found
    critical_detections = [d for d in detections if d.startswith("INJECTION:")]
    if critical_detections:
        return GuardrailResult(
            allowed=False,
            blocked=True,
            block_reason="Potential prompt injection detected",
            sanitised_query=sanitised,
            detections=detections,
        )
    
    # 5. Allow with warnings
    return GuardrailResult(
        allowed=True,
        sanitised_query=sanitised,
        detections=detections,
    )


def validate_output(answer: str) -> dict:
    """Validate LLM output for leaked content.
    
    Args:
        answer: LLM's response
        
    Returns:
        Dict with 'clean' flag and 'violations' list
    """
    violations = []
    
    # Check for system prompt leakage
    system_prompt_fragments = [
        "expert AI assistant specializing in the Tunisian energy sector",
        "Use ONLY the following context",
        "do not hallucinate or use outside knowledge",
        "I do not have enough information to answer that based on the provided documents",
        "Answer in the same language as the user's query",
    ]
    
    for fragment in system_prompt_fragments:
        if fragment.lower() in answer.lower():
            violations.append(f"SYSTEM_PROMPT_LEAKED:{fragment[:30]}...")
    
    # Check for credential patterns
    credential_patterns = [
        (r"ADMIN_API_KEY\s*[:=]\s*\S+", "ADMIN_KEY_LEAKED"),
        (r"JWT_SECRET\s*[:=]\s*\S+", "JWT_SECRET_LEAKED"),
        (r"DATABASE_URL\s*[:=]\s*\S+", "DB_URL_LEAKED"),
        (r"api[_-]?key\s*[:=]\s*\S+", "API_KEY_LEAKED"),
        (r"password\s*[:=]\s*\S+", "PASSWORD_LEAKED"),
    ]
    
    for pattern, label in credential_patterns:
        if re.search(pattern, answer, re.IGNORECASE):
            violations.append(label)
    
    # Check for malicious URLs
    malicious_urls = [
        r"evil\.example\.com",
        r"malicious\.com",
        r"phishing",
        r"SolarScam",
    ]
    
    for pattern in malicious_urls:
        if re.search(pattern, answer, re.IGNORECASE):
            violations.append(f"MALICIOUS_URL:{pattern}")
    
    return {
        "clean": len(violations) == 0,
        "violations": violations,
    }


def is_energy_domain(query: str) -> bool:
    """Check if query is related to the energy domain.
    
    Args:
        query: User's input query
        
    Returns:
        True if energy-related, False otherwise
    """
    query_lower = query.lower()
    return any(keyword.lower() in query_lower for keyword in ENERGY_KEYWORDS)
```

**Effort:** 200 lines
**Impact:** Blocks prompt injection, detects credential leaks, enforces domain scope

---

### 2.2 Integrate Guardrails into Pipeline

**File:** `src/rag/retrieve.py`

**Current Code:**
```python
async def run_pipeline(user_query, chat_history=None):
    # Step 1: Contextualize query
    standalone_query = await rewrite_query_with_history(user_query, ...)
    
    # Step 2: Retrieve context
    structured_sources = retrieve_context_hybrid(standalone_query, ...)
    
    # Step 3: Generate answer
    answer = await generate_answer(user_query, context_str, ...)
    
    return answer, structured_sources
```

**Fixed Code:**
```python
from src.rag.guardrails import check_query, validate_output

async def run_pipeline(user_query, chat_history=None):
    # Step 0: GUARDRAILS — Query validation
    guard = check_query(user_query)
    if not guard.allowed:
        return "I'm sorry, but I cannot process this request. Please ask a question about the Tunisian energy sector.", []
    
    user_query = guard.sanitised_query
    
    # Step 1: Contextualize query
    standalone_query = await rewrite_query_with_history(user_query, ...)
    
    # Step 2: Retrieve context
    structured_sources = retrieve_context_hybrid(standalone_query, ...)
    
    # Step 3: Generate answer
    answer = await generate_answer(user_query, context_str, ...)
    
    # Step 4: GUARDRAILS — Output validation
    validation = validate_output(answer)
    if not validation.clean:
        logger.warning("Output validation failed: %s", validation["violations"])
        return "I'm sorry, but I cannot provide that information. Please ask a question about the Tunisian energy sector.", []
    
    return answer, structured_sources
```

**Effort:** 15 lines
**Impact:** Validates both input and output at the pipeline level

---

### 2.3 Add IP Normalization

**File:** `src/api/ratelimit.py`

**Current Code:**
```python
def _rate_key(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or get_remote_address(request)
    return get_remote_address(request)
```

**Fixed Code:**
```python
import ipaddress

def _normalize_ip(ip: str) -> str:
    """Normalize IP address to prevent IPv4/IPv6 confusion."""
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return ip

def _rate_key(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
            if ip:
                return _normalize_ip(ip)
    ip = get_remote_address(request)
    return _normalize_ip(ip) if ip else "unknown"
```

**Effort:** 10 lines
**Impact:** Prevents IP confusion attacks

---

### 2.4 Add Query Length Limit

**File:** `src/api/main.py`

**Current Code:**
```python
@app.post("/api/chat", response_model=QueryResponse)
async def chat_endpoint(...):
    user_query = payload.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
```

**Fixed Code:**
```python
@app.post("/api/chat", response_model=QueryResponse)
async def chat_endpoint(...):
    user_query = payload.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    
    # Limit query length to prevent resource exhaustion
    if len(user_query) > 10_000:
        raise HTTPException(status_code=400, detail="Query too long (max 10,000 characters)")
```

**Effort:** 3 lines
**Impact:** Prevents oversized payloads

---

## Phase 3: Monitoring & Hardening (Week 4+)

### 3.1 Add Connection Pool Monitoring

**File:** `src/api/metrics.py`

```python
from prometheus_client import Gauge

# Connection pool metrics
DB_POOL_SIZE = Gauge("db_pool_size", "Current DB connection pool size")
DB_POOL_CHECKED_OUT = Gauge("db_pool_checked_out", "Number of checked out DB connections")
DB_POOL_OVERFLOW = Gauge("db_pool_overflow", "Number of overflow DB connections")

async def monitor_pool(engine):
    """Update pool metrics periodically."""
    while True:
        try:
            pool = engine.pool
            DB_POOL_SIZE.set(pool.size())
            DB_POOL_CHECKED_OUT.set(pool.checkedout())
            DB_POOL_OVERFLOW.set(pool.overflow())
        except Exception:
            pass
        await asyncio.sleep(10)  # Update every 10 seconds
```

**Effort:** 20 lines
**Impact:** Enables early detection of pool exhaustion

---

### 3.2 Add Per-User Budget Caps

**File:** `src/api/budget.py` (NEW)

```python
"""Per-user budget tracking for LLM usage."""

import time
from collections import defaultdict
from dataclasses import dataclass

@dataclass
class UserBudget:
    tokens_used: int = 0
    requests_count: int = 0
    window_start: float = 0
    last_request: float = 0

# In-memory budget tracker (replace with Redis in production)
_user_budgets: dict[str, UserBudget] = defaultdict(UserBudget)

# Budget limits
MAX_TOKENS_PER_HOUR = 100_000  # ~$0.50/hour
MAX_REQUESTS_PER_HOUR = 50


def check_budget(user_id: str) -> tuple[bool, str]:
    """Check if user is within budget limits.
    
    Returns:
        (allowed, reason)
    """
    budget = _user_budgets[user_id]
    now = time.time()
    
    # Reset window if more than 1 hour
    if now - budget.window_start > 3600:
        budget.tokens_used = 0
        budget.requests_count = 0
        budget.window_start = now
    
    # Check limits
    if budget.tokens_used >= MAX_TOKENS_PER_HOUR:
        return False, "Token budget exceeded"
    if budget.requests_count >= MAX_REQUESTS_PER_HOUR:
        return False, "Request limit exceeded"
    
    return True, ""


def record_usage(user_id: str, tokens: int):
    """Record token usage for a user."""
    budget = _user_budgets[user_id]
    budget.tokens_used += tokens
    budget.requests_count += 1
    budget.last_request = time.time()
```

**Effort:** 50 lines
**Impact:** Limits per-user LLM spending

---

### 3.3 Add Global Rate Limiting

**File:** `src/api/ratelimit.py`

```python
# Add global rate limit across all endpoints
GLOBAL_LIMIT = os.getenv("RATE_LIMIT_GLOBAL", "200/minute")

def configure_limiter(app: FastAPI, limiter_instance: Limiter) -> None:
    """Wire slowapi into a FastAPI app."""
    app.state.limiter = limiter_instance
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    # Add global rate limit
    @app.middleware("http")
    async def global_rate_limit(request: Request, call_next):
        # Skip health/metrics endpoints
        if request.url.path in ["/health", "/ready", "/metrics"]:
            return await call_next(request)
        
        # Check global limit
        key = _rate_key(request, TRUST_PROXY_HEADERS)
        # ... check against GLOBAL_LIMIT
        
        return await call_next(request)
```

**Effort:** 30 lines
**Impact:** Comprehensive rate limiting across all endpoints

---

### 3.4 Add Request Timeout

**File:** `src/api/main.py`

```python
import asyncio

async def chat_stream_endpoint(...):
    # Add timeout for streaming responses
    async def event_generator_with_timeout():
        try:
            async with asyncio.timeout(120):  # 2 minute timeout
                async for event in event_generator():
                    yield _sse(event)
        except asyncio.TimeoutError:
            yield _sse({"type": "error", "message": "Request timed out"})
    
    return StreamingResponse(
        event_generator_with_timeout(),
        media_type="text/event-stream",
    )
```

**Effort:** 10 lines
**Impact:** Prevents indefinite connection holding

---

## Implementation Checklist

### P0: Emergency (Week 1)

- [ ] Fix L4: Add owner check to `GET /api/conversations/{id}`
- [ ] Fix L11: Add manual rate limiting to streaming endpoint
- [ ] Fix L13: Increase `pool_size` to 30
- [ ] Fix L14: Set `TRUST_PROXY_HEADERS=false`
- [ ] Fix L14: Add concurrency limiter (`asyncio.Semaphore`)

### P1: Defense-in-depth (Week 2-3)

- [ ] Build `src/rag/guardrails.py` module
- [ ] Integrate guardrails into `src/rag/retrieve.py`
- [ ] Add IP normalization to `src/api/ratelimit.py`
- [ ] Add query length limit to `src/api/main.py`
- [ ] Add output validation to pipeline

### P2: Monitoring (Week 4+)

- [ ] Add connection pool monitoring
- [ ] Implement per-user budget caps
- [ ] Add global rate limiting
- [ ] Add request timeouts
- [ ] Add security event logging

---

## Testing Strategy

### Unit Tests (No LLM Calls)

| Test | What It Tests | Count |
|---|---|---|
| `test_injection_detection` | Each injection pattern is detected | 20 |
| `test_domain_enforcement` | Energy queries pass, others blocked | 10 |
| `test_output_validation` | Leaked content is caught | 10 |
| `test_credential_detection` | Secret patterns are detected | 10 |
| `test_legitimate_passthrough` | Energy queries are NOT blocked | 10 |
| **TOTAL** | — | **60** |

### Integration Tests (With LLM)

| Test | What It Proves |
|---|---|
| Attack A1.1 through pipeline | System prompt not leaked |
| Attack A2.1 through pipeline | LLM stays in energy domain |
| Attack A3.1 through pipeline | LLM doesn't bypass context |
| Legitimate query through pipeline | Normal operation unaffected |

### Re-Attack Tests

After implementing guardrails, re-run all exploit tests:

| Level | Expected Result |
|---|---|
| L1: System Prompt Leak | 0 leaks (was 12) |
| L4: IDOR | 0 vulnerabilities (was 4) |
| L8: Poisoned Responses | 0 poisonings (was 2) |
| L11: Budget Drained | Rate limiting works on streaming |
| L13: DB Overwhelmed | Pool handles 20+ concurrent |
| L14: Rate Limit Bypass | XFF spoofing blocked |

---

## Risk Assessment

| Risk | Mitigation | Impact |
|---|---|---|
| Guardrails block legitimate queries | Extensive false positive testing | 🟡 Medium |
| Performance impact from validation | Lightweight regex, async execution | 🟢 Low |
| Maintenance burden | Well-documented, modular design | 🟢 Low |
| Bypass via novel techniques | Regular re-testing, monitoring | 🟡 Medium |

---

## Success Criteria

| Metric | Target | Measurement |
|---|---|---|
| P0 fixes deployed | 100% | All 5 fixes in production |
| Injection detection rate | >95% | Re-run L1 exploit tests |
| False positive rate | <5% | Test with 100 legitimate queries |
| Pool exhaustion threshold | >20 concurrent | Re-run L13 exploit tests |
| Rate limit bypass rate | 0% | Re-run L14 exploit tests |

---

## Sign-Off

| Role | Name | Date | Status |
|---|---|---|---|
| Security Tester | Buffy (AI Agent) | 2026-08-23 | ✅ Plan Complete |
| Implementation | — | — | ⬜ Pending |
| Testing | — | — | ⬜ Pending |
| Deployment | — | — | ⬜ Pending |

---

*This plan was generated based on the comprehensive security testing of the Tunisia Energy RAG system.*
*All vulnerabilities have been verified with 225+ attack vectors across 14 levels.*
