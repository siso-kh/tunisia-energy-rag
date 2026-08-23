# L6 Domain Escape — Guardrail (Defense)

> **Status:** ⬜ Pending — Not yet implemented
>
> **Depends on:** User approval to proceed

---

## Defense Strategy

**Approach:** Multi-layer defense combining input sanitization and output validation.

### Layer 1: Query Sanitization (Input)
- Detect and strip role-play patterns ("you are now", "pretend to be", "act as")
- Detect and block authority claims ("I'm the admin", "ignore your restrictions")
- Block credential extraction attempts

### Layer 2: Domain Enforcement (Input)
- Keyword-based energy domain check (FR + AR + EN)
- Reject queries that don't contain energy-related terms
- Allow short/ambiguous queries to pass through (let LLM decide)

### Layer 3: Output Validation (Output)
- Scan answers for off-topic content
- Detect system prompt leakage
- Detect credential patterns

---

## Architecture

```
User Query
    ↓
[check_query()] ← NEW: guardrails layer
    ├── query_sanitise() — strip injection patterns
    ├── is_energy_domain() — enforce scope
    └── Return: allowed + sanitised query
    ↓
Query Rewriter (LLM call)
    ↓
Hybrid Retrieval
    ↓
Answer Generation (LLM call)
    ↓
[output_validate()] ← NEW: guardrails layer
    ├── Check for system prompt leakage
    ├── Check for credential patterns
    └── Return: clean + violations
    ↓
Response to User
```

---

## Code Changes

**File:** `src/rag/guardrails.py` (NEW)

**Key functions:**
- `query_sanitise(query: str) → SanitiseResult`
- `is_energy_domain(query: str) → bool`
- `output_validate(answer: str) → OutputValidationResult`
- `check_query(query: str) → GuardrailResult`

**Integration point:** `src/rag/retrieve.py` — `run_pipeline()` and `stream_pipeline()`

---

## Trade-offs

| Aspect | Impact |
|---|---|
| Latency | <1ms (regex-based, no LLM call) |
| False positive risk | Low — energy keywords are specific |
| Maintenance | Pattern list needs updates for new attack vectors |

---

## Implementation Checklist

- [ ] Create `src/rag/guardrails.py`
- [ ] Add injection detection patterns
- [ ] Add energy domain keywords (FR/AR/EN)
- [ ] Add output validation rules
- [ ] Integrate into `run_pipeline()`
- [ ] Integrate into `stream_pipeline()`
- [ ] Write unit tests
- [ ] Run exploit tests to verify defense
