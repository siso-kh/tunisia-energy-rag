# Tunisia Energy RAG — Product Completion Plan

> Status: draft plan (2026-08-16). Covers the gap between the current working prototype
> and a deployable, maintainable product.

---

## 1. Current state (what's already built)

| Area | Status |
|---|---|
| **RAG pipeline** | Collector → LLM triage → OCR/chunk ingest → ChromaDB embeddings → Mistral generation (async, token-budgeted, SSE streaming) ✅ |
| **Backend API** | FastAPI: chat (JSON + SSE), conversations, outage CRUD, admin purge stats/config, runtime settings, health. **71 tests** ✅ |
| **Frontend** | React 18 + Vite + TS: chat w/ citations dropdown, Tunisia SVG map + Leaflet, solar ROI calculator, 3-locale i18n (fr/ar/derja), `/admin` page. **87 tests** ✅ |
| **Data layer** | Async SQLAlchemy, Postgres (SQLite dev), seeder, outage TTL purge task, runtime-config settings table ✅ |
| **Infra** | docker-compose (postgres, db-seed, backend, frontend/nginx, ngrok), healthchecks ✅ |
| **Security** | Admin API-key (constant-time), no secrets committed, key in-memory only ✅ |
| **CI/CD** | ❌ Empty — `.github/workflows/` has no workflows |
| **Auth** | ❌ Single hardcoded "demo user" — no real accounts |

---

## 2. Phase 0 — Pre-launch blockers (P0, before any real user)

### 2.1 Real user authentication — *biggest gap*
Currently every conversation and outage report belongs to one `demo_user`.

- Email/password signup + login (Argon2 hashing, JWT or session cookies), or OAuth (Google/SSO).
- Wire `user_id` through all endpoints (conversations, outages, admin audit).
- Add `password_hash` to `User`, logout/refresh, password reset.
- **Affects:** models, service, API, frontend store + login pages, ~20 tests.

### 2.2 Rate limiting
`/api/chat*` (LLM cost), `/api/outages` (spam), and admin login (brute force).
`slowapi` / Redis-backed limiter per IP + per user.

### 2.3 Production CORS + security headers
`allow_origins=["*"]` in `src/api/main.py` is a dev default. Whitelist the real
domain, add HSTS/CSP/iframe headers, terminate TLS at a reverse proxy.

### 2.4 DB migrations (Alembic)
Tables are created ad-hoc by the seeder. Product needs versioned, reversible
migrations so schema changes deploy safely.

### 2.5 Error tracking (Sentry)
Errors are logged and sanitized today; Sentry gives real stack traces + prod alerting.

---

## 3. Phase 1 — Reliability & operations (P0/P1)

### 3.1 CI/CD pipeline
GitHub Actions: backend pytest (fast run) + frontend vitest/build on every PR;
auto-deploy to staging on merge, prod on tag. **Biggest product-readiness lever.**

### 3.2 Structured logging + metrics
JSON logs, Prometheus `/metrics` (request latency, LLM tokens, purge counts),
Grafana dashboard.

### 3.3 Postgres backups
Nightly `pg_dump` to object storage + retention, tested restore drill.

### 3.4 Health/readiness split
`/health` (liveness) vs `/ready` (DB + Chroma reachable) for proper orchestration.

---

## 4. Phase 2 — Product features (P1)

### 4.1 Outage moderation workflow
Today *anyone* can create reports and flip status to `VERIFIED`/`RESOLVED`.
Add: moderator role, approve/reject queue, duplicate detection (same region +
utility + window), report voting ("me too"). **Biggest trust feature for a
crowdsourced map.**

### 4.2 Outage notifications
Users subscribe to a region → Telegram/email/push when a report appears or
resolves. Turns the map into a service.

### 4.3 User profiles & cross-device history
Requires auth (2.1); then conversations list, rename, delete, resume from any device.

### 4.4 RAG quality program
The core product value is answer quality:
- Hybrid retrieval (vector + BM25 keyword for Arabic/French queries)
- Reranking (cross-encoder) on top-25 → top-5
- Evaluation set: golden Q/A pairs + `ragas`-style metrics in CI
- Scheduled corpus refresh (see Phase 3)

### 4.5 Usage quotas / cost guardrails
Per-user daily chat caps, admin-configurable (fits the existing `settings`
table + admin page).

---

## 5. Phase 3 — Data pipeline automation (P1)

### 5.1 Scheduled ingestion
Collector → triage → OCR → embed is currently manual. Package as a
containerized job (cron / GitHub Actions scheduled) running weekly, re-embedding
only changed docs (drive off `data/collection_log.json`).

### 5.2 Vector DB ops
Chroma backup + rebuild script, embedding-model version pinning, doc-source
tracking so stale docs can be dropped.

---

## 6. Phase 4 — Scale & performance (P2)

### 6.1 Redis cache
Cache hot chat responses (identical query+history hash), outage queries, public
config. Keeps LLM costs down at scale.

### 6.2 CDN for static assets
Nginx already caches hashed assets; a CDN in front for global latency.

### 6.3 Load testing
k6 / `locust` scenario: 100 concurrent chatters + map pollers; tune uvicorn
workers + Postgres pool. Use the test-time baseline in `tests/auto_test_config.md`
as the perf yardstick.

---

## 7. Phase 5 — Launch readiness (P2)

### 7.1 Legal & compliance
Privacy policy + terms (user-submitted location data on the outage map is
sensitive — GDPR), cookie consent, data-retention statement (aligns with the 5h
TTL story).

### 7.2 Landing page + docs
Explain the product, demo, screenshots; user guide in fr/ar.

### 7.3 Privacy-friendly analytics
Plausible/Umami (not GA) — chat usage, map views, retention.

### 7.4 Security review
Dependency audit (`pip-audit`, `npm audit`), OWASP pass on the API, pen-test the
admin surface.

### 7.5 Runbook + alerts
On-call doc: how to restart, restore a backup, hotfix a broken embedding model.

---

## 8. Suggested order & effort

```
Sprint 1  (P0)   Auth · Rate limiting · CORS/headers · Sentry
Sprint 2  (P0/P1) Alembic migrations · CI/CD · backups · health split
Sprint 3  (P1)   Outage moderation · notifications
Sprint 4  (P1)   RAG quality program (rerank + eval set) · scheduled ingestion
Sprint 5  (P2)   Redis · CDN · load test · landing page + docs
Sprint 6  (P2)   Legal · analytics · security audit · runbook
```

**Rough total: 6 focused sprints (~4–6 weeks solo, ~2–3 with a second dev).**
The two things that unblock everything else are **(1) real auth** and
**(2) CI/CD** — without them the rest is polish on a demo.

---

## 9. Open decision points

1. **Audience** — internal STEG/utility tool, or public-facing for Tunisian
   citizens? (Drives auth type, moderation strictness, GDPR scope, hosting.)
2. **Hosting** — VPS, Railway/Render/Fly, or a real cloud with k8s? (Drives
   CI/CD + backup design.)
3. **Monetization** — free public service, or paid tiers with quotas? (Drives
   usage-quota + billing work.)
4. **LLM budget** — monthly cap on the Mistral API? (Sets how urgent rate
   limiting + caching are.)
