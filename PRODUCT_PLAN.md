# Tunisia Energy RAG — Product Plan

> Updated 2026-08-20. Simplified plan focused on AI quality, guardrails, and product features.

---

## 1. What's Built

| Area | Status |
|---|---|
| **RAG pipeline** | Hybrid retrieval (vector + BM25 + RRF + rerank) → Mistral Large generation (SSE streaming) ✅ |
| **Backend API** | 35 endpoints, 168 tests, structured JSON logs, Prometheus /metrics ✅ |
| **Frontend** | React 18 + Vite, chat, outage map, admin panel, 126 tests ✅ |
| **Data layer** | PostgreSQL 16, async SQLAlchemy, Alembic migrations, seeder ✅ |
| **Observability** | JSON logs, Prometheus metrics, built-in HTML dashboard at `/dashboard` ✅ |
| **Ingestion** | PDF triage (2-gate LLM scoring), recursive BFS crawl, SSE progress ✅ |
| **Security** | JWT auth, rate limiting, CORS, security headers ✅ |
| **Tests** | 168 backend + 126 frontend = **294 total** ✅ |

---

## 2. AI Guardrails (P1 — Current Focus)

These protect users from bad LLM outputs and control costs.

### 2.1 Hallucination detection — ⬜
- **Groundedness check**: after generation, verify answer references retrieved chunks
- Score each answer 0–1 based on claim-support ratio
- Flag low-score answers for review; optionally refuse to show them
- Metric: % of answers grounded in source documents

### 2.2 Prompt injection defense — ⬜
- Sanitize user queries before sending to LLM (strip system-prompt-like instructions)
- Validate that generated answers don't leak system prompt content
- Rate-limit follow-up queries to prevent prompt-stuffing attacks
- Test: adversarial query suite (injection attempts, role-play, language-switching)

### 2.3 Content filtering — ⬜
- Refuse answers on topics outside energy sector scope (guardrails topic filter)
- Detect and block harmful/offensive content in both input and output
- Fallback message: "This question is outside the scope of energy sector data."
- Configurable via admin settings table

### 2.4 Cost guardrails — ⬜
- Per-user daily chat cap (admin-configurable, default 50/day)
- Per-query token budget: refuse if estimated context > 6000 tokens
- Track total LLM tokens/day in metrics + admin dashboard
- Alert threshold: daily token budget warning at 80%
- Monitor via `/dashboard` LLM tokens chart

### 2.5 Answer quality scoring — ⬜
- Auto-score every generated answer on: relevance, citation count, length
- Store scores in a new `answer_scores` table (query, score, tokens, latency)
- Dashboard panel: average quality score over time
- Regression alert: quality drops below baseline

---

## 3. AI Testing (P1 — Current Focus)

Systematic evaluation to catch quality regressions before users do.

### 3.1 Expand golden evaluation set — ⬜
- Current: 6 queries in `data/eval/golden_qa.json`
- Target: 30+ queries covering:
  - Tunisian energy policy (ANME, STEG, solar, wind)
  - Technical documents (efficiency standards, grid data)
  - Arabic-language queries
  - Follow-up / multi-turn queries
  - Edge cases (ambiguous, out-of-scope, mixed languages)

### 3.2 Automated eval pipeline — ⬜
- Run eval suite on every ingestion (or weekly)
- Track recall@5, MRR, answer quality over time
- Store results in `data/eval/results.json` with timestamps
- Compare against baseline; fail CI if recall drops >5%

### 3.3 Adversarial test suite — ⬜
- Injection attempts: "Ignore previous instructions and..."
- Language mixing: French/Arabic/English in one query
- Out-of-scope: "What's the weather?" → should refuse gracefully
- Very long queries (500+ words)
- Empty/whitespace-only queries
- Unicode edge cases (RTL, Arabic diacritics, emoji)

### 3.4 Retrieval regression tests — ⬜
- After each ingestion batch, verify recall@5 doesn't degrade
- Check that new documents are actually retrievable (index + query test)
- Verify embedding model consistency (same model = same vectors)

### 3.5 Prompt regression tests — ⬜
- Snapshot test: same query → similar answer structure (not exact match)
- Token efficiency: track prompt + completion tokens per query type
- Language consistency: French query → French answer, Arabic → Arabic

---

## 4. Product Features (P1)

### 4.1 Outage moderation workflow
Approve/reject queue for crowdsource reports, duplicate detection
(same governorate + time window). Biggest trust feature for crowdsourced map.

### 4.2 Usage quotas
Per-user daily chat caps, admin-configurable. Fits the existing `settings` table.

### 4.3 User profiles & cross-device history
Conversations list, rename, delete, resume from any device. Auth is done.

### 4.4 Scheduled ingestion cron
`src/ingestion/collector.py` already works; wrap in background task for
nightly corpus refresh.

---

## 5. Production Hardening (P2)

### 5.1 CI/CD pipeline
GitHub Actions: pytest + vitest on every PR. PostgreSQL service container.

### 5.2 Sentry error tracking
`sentry-sdk[fastapi]` for backend + React error boundary for frontend.

### 5.3 Redis cache
Shared rate limit storage, response caching for repeated queries.

### 5.4 Load testing
`locust` or `k6`: 100 concurrent chatters + map pollers.

### 5.5 Landing page + docs
Public-facing page with screenshots; API docs from OpenAPI.

---

## 6. Sprint Roadmap

```
Sprint 1 (now)  AI Guardrails: hallucination detection, prompt injection, content filter
Sprint 2        AI Testing: golden set expansion, eval pipeline, adversarial suite
Sprint 3        Cost guardrails: usage caps, token budgets, quality scoring
Sprint 4        Product: outage moderation, usage quotas, user profiles
Sprint 5        Ops: CI/CD, Sentry, Redis cache, load testing
Sprint 6        Launch: landing page, docs, security audit
```

---

## 7. Open Questions

1. **LLM budget** — monthly cap on Mistral API? Sets urgency of cost guardrails.
2. **Audience** — internal STEG tool or public citizens? Drives moderation strictness.
3. **Hosting** — VPS, Railway/Render, or cloud? Drives CI/CD and backup design.
