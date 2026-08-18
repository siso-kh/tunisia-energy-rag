# Tunisia Energy RAG — Product Completion Plan

> Status: draft plan (2026-08-16). Covers the gap between the current working prototype
> and a deployable, maintainable product.

---

## 1. Current state (what's already built)

| Area | Status |
|---|---|
| **RAG pipeline** | Collector → LLM triage → OCR/chunk ingest → ChromaDB embeddings → Mistral generation (async, token-budgeted, SSE streaming) ✅ |
| **Backend API** | FastAPI: chat (JSON + SSE), conversations, outage CRUD, admin purge stats/config, runtime settings, liveness (`/health`) + readiness (`/ready`). **112 tests** ✅ |
| **Frontend** | React 18 + Vite + TS: chat w/ citations dropdown, Tunisia SVG map + Leaflet, solar ROI calculator, 2-locale i18n (fr/ar), `/admin` page. **108 tests** ✅ |
| **Data layer** | Async SQLAlchemy, Postgres everywhere (dev = prod), Alembic migrations, seeder, outage TTL purge task, runtime-config settings table, nightly pg_dump backups ✅ |
| **Infra** | docker-compose (postgres, db-seed, pg-backup, backend, frontend/nginx, ngrok), readiness-based healthchecks ✅ |
| **Security** | Real auth (JWT), slowapi rate limiting, env-driven CORS + security headers, Admin API-key (constant-time), no secrets committed ✅ |
| **CI/CD** | ❌ Empty — `.github/workflows/` has no workflows |
| **Auth** | ✅ Email/password + JWT (register/login/me), per-user conversation + outage ownership |
| **Sentry** | ❌ Not integrated — the last unchecked P0 item |

---

## 2. Phase 0 — Pre-launch blockers (P0, before any real user)

### 2.1 Real user authentication — ✅ DONE
Email/password signup + login (bcrypt hashing, JWT), `/me`, per-user conversation +
outage ownership; frontend auth store + login/register modal. Remaining: logout/refresh,
password reset.

### 2.2 Rate limiting — ✅ DONE
slowapi per-IP limits on chat/outages/auth/admin (env-tunable, Redis-backed storage
optional), 429 + `Retry-After`, friendly frontend 429 messages.

### 2.3 Production CORS + security headers — ✅ DONE
Env-driven `CORS_ORIGINS` whitelist + security-headers middleware (nosniff, DENY,
Referrer-Policy, opt-in HSTS/CSP). TLS still terminates at the reverse proxy.

### 2.4 DB migrations (Alembic) — ✅ DONE
Versioned, reversible migrations (async env, initial revision, adopt-aware
`ensure_schema`, generated `schema.sql` reference). Dev runs the same Postgres as prod.

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

### 3.3 Postgres backups — ✅ DONE (local)
Nightly `pg_dump` via the `pg-backup` compose service into `./backups/` with
retention, plus `scripts/backup_db.sh` / `scripts/restore_db.sh`. Remaining: ship
backups off-host (object storage) once hosting is chosen.

### 3.4 Health/readiness split — ✅ DONE
`/health` (liveness, always 200) vs `/ready` (DB + Chroma reachable, 503 with a
per-dependency `checks` map when degraded); Docker healthchecks now gate on `/ready`.

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

### 4.4 RAG quality program — ✅ DONE (retrieval half)
- Hybrid retrieval (vector + BM25, RRF fusion) — `src/rag/hybrid.py`, wired into the pipeline ✅
- Cross-encoder reranking (multilingual, env-gated, lazy, graceful fallback) ✅
- Golden Q/A set (`data/eval/golden_qa.json`) + offline eval harness (`src/eval/`):
  recall@5 **0.83 → 1.00**, MRR **0.700 → 1.000** on 6 queries ✅
- **Remaining:** scheduled corpus refresh (Phase 3 §5.1) and growing the golden
  set / adding `ragas`-style groundedness metrics in CI once a deploy exists.

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
Sprint 1  (P0)   Auth ✅ · Rate limiting ✅ · CORS/headers ✅ · Sentry ⬜
Sprint 2  (P0/P1) Alembic migrations ✅ · CI/CD ⬜ · backups ✅ · health split ✅
Sprint 3  (P1)   Outage moderation · notifications
Sprint 4  (P1)   RAG quality program (rerank + eval set) · scheduled ingestion
Sprint 5  (P2)   Redis · CDN · load test · landing page + docs
Sprint 6  (P2)   Legal · analytics · security audit · runbook
```

**Next up:** finish Sprint 1 with **Sentry**, then **CI/CD** (the remaining
unblocker — now that migrations exist, CI can verify `alembic upgrade head` on
a fresh DB on every PR).

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
