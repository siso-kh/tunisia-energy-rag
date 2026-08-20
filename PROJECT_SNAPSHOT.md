# Project Snapshot — Tunisia Energy RAG
**Generated:** 2026-08-20 · **Branch:** main · **Last commit:** `8972cf7` (SSE progress panel + recursive crawl + cleanup)

---

## 1. Architecture & Tech Stack

### What this is
A RAG (Retrieval-Augmented Generation) platform for the Tunisian energy sector:
- **Chat** — users ask energy questions; the system retrieves relevant PDF chunks and generates sourced answers (French + Arabic)
- **Outage Map** — crowdsourced live outage map with animated SVG Tunisia map + Leaflet markers
- **Solar ROI Calculator** — client-side financial projections
- **Admin Panel** — document upload, outage purge, runtime config, URL source management with website crawling, real-time SSE progress tracking, and a dashboard log view

### Tech stack

| Layer | Tech | Notes |
|---|---|---|
| **Backend** | Python 3.11 · FastAPI (async) · uvicorn | `src/api/main.py` is the entrypoint |
| **RAG Pipeline** | ChromaDB (vector DB) · sentence-transformers · BM25 (rank_bm25) · RRF fusion · optional cross-encoder rerank | `src/rag/retrieve.py` + `src/rag/hybrid.py` |
| **LLM** | OpenAI-compatible client → Mistral Large (via custom base URL) | Async client, `CUSTOM_API_KEY` + `OPENAI_BASE_URL` in `.env` |
| **Document Ingestion** | pdfplumber + EasyOCR (Arabic) · LangChain text splitter · custom triage (2-gate LLM scoring) | `src/ingestion/` + `src/utils/triage.py` |
| **Database** | PostgreSQL 16 (async SQLAlchemy + asyncpg) · Alembic migrations | Local dev uses a local PG install on port **5432** (NOT Docker's 5433) |
| **Frontend** | React 18 · TypeScript · Vite · Tailwind CSS · Zustand · React-Leaflet · i18next (fr/ar) | `frontend/` directory |
| **Serving** | Nginx (Docker) reverse-proxies `/api` to backend | Docker Compose full stack |
| **Auth** | Email/password + JWT (bcrypt + HS256) | `src/api/auth.py` + `src/utils/security.py` |
| **Observability** | Structured JSON logs (python-json-logger) · Prometheus /metrics · Built-in HTML dashboard (Chart.js) | `src/api/logging_config.py`, `metrics.py`, `dashboard.py` |
| **Infra** | Docker Compose (postgres, backend, frontend/nginx, ngrok, pg-backup) · Prometheus + Grafana (optional) | `docker-compose.yml`, `config/prometheus.yml`, `config/grafana/` |

### Key file map
```
src/
├── api/
│   ├── main.py          # FastAPI app, ALL endpoints (chat, outages, admin, sources, auth router)
│   ├── auth.py           # register/login/me endpoints + JWT dependency
│   ├── dashboard.py      # Built-in HTML metrics dashboard (Chart.js)
│   ├── logging_config.py # Structured JSON logging (python-json-logger)
│   ├── metrics.py        # Prometheus metrics (request latency, LLM tokens, purge counts)
│   ├── ratelimit.py      # slowapi rate limiter config
│   └── security.py       # CORS + security headers middleware
├── database/
│   ├── connection.py     # AsyncEngine + session factory (reads DATABASE_URL from .env)
│   ├── models.py         # SQLAlchemy models (User, Conversation, Message, OutageReport, Setting, Source)
│   ├── service.py        # All DB CRUD operations
│   ├── seed.py           # Database seeder (demo data)
│   └── schema.py         # Alembic env + ensure_schema
├── ingestion/
│   ├── collector.py      # PDF downloader (SerpApi + DuckDuckGo fallback)
│   ├── ingest_chunks.py  # PDF → text extraction → chunking → processed_chunks.json
│   ├── indexer.py        # chunk + embed + upsert into ChromaDB (for admin upload)
│   ├── admin_ingest.py   # orchestrate upload/download → triage → index
│   └── research.py       # URL source management, recursive BFS crawl, download + ingest
├── rag/
│   ├── retrieve.py       # Async pipeline: rewrite query → retrieve → generate (SSE streaming)
│   └── hybrid.py         # BM25 + vector + RRF fusion + optional cross-encoder rerank
├── eval/
│   ├── evaluate.py       # Offline retrieval evaluation CLI
│   └── metrics.py        # recall@k, MRR metrics
└── utils/
    ├── security.py       # JWT encode/decode, bcrypt, auth_configured()
    ├── token_manager.py  # Token-budget history truncation (tiktoken)
    └── triage.py         # LLM-based PDF relevance scoring (2-gate)

frontend/src/
├── App.tsx               # Router + layout shell
├── pages/                # ChatPage, OutageMapPage, SolarROIPage, AdminPage
├── components/
│   ├── chat/             # ChatInput, MessageBubble, SourcesDropdown, ChatPanel
│   ├── map/              # TunisiaMap (animated SVG), OutageMap (Leaflet), ReportForm
│   ├── sidebar/          # CarteTab, AdminTab, TelemetryTab
│   ├── admin/            # ConfigEditor, DocumentUpload, SourcesManager, DashboardTab, ProgressPanel
│   ├── auth/             # AuthModal (login/register)
│   └── ui/               # Button, Spinner, ThemeToggle, LanguageSwitcher
├── services/             # api.ts (axios), auth.ts, chat.ts, admin.ts, outage.ts, token.ts
├── store/                # chatStore (Zustand), authStore (Zustand)
├── hooks/                # useChatStream (SSE consumption)
├── locales/              # fr.json, ar.json (i18n)
└── lib/                  # governorates.ts, outage-stats.ts, tunisia-geo.ts

tests/
├── test_integration.py    # HTTP endpoint tests (health, chat, pipeline)
├── test_generation.py     # LLM unit tests (refusal, grounded answer)
├── test_retrieval.py      # ChromaDB retrieval tests
├── test_api_db.py         # Outage CRUD, conversations, SSE, admin purge/config
├── test_auth.py           # Full auth suite (register, login, me, token lifecycle)
├── test_database.py       # SQLAlchemy model + CRUD tests
├── test_seed.py           # Seeder tests
├── test_ratelimit.py      # Rate limiting + security headers
├── test_migrations.py     # Alembic upgrade/downgrade
├── test_readiness.py      # /ready probe tests
├── test_hybrid.py         # Hybrid retrieval (BM25, RRF, rerank)
├── test_eval_metrics.py   # Recall@k, MRR metrics
├── test_admin_docs.py     # Admin document upload/ingestion
├── test_sources_api.py    # Source CRUD, research, ingest, crawl, bulk-delete
├── test_metrics.py        # /metrics endpoint, Prometheus format, logging config, normalization
├── test_token_manager.py  # Token budget truncation
└── stress_checks.py       # Huge payload stress tests (not in default suite)
```

## Standalone scripts
```
scripts/
├── backup_db.sh           # Manual pg_dump
├── restore_db.sh          # Confirmed restore drill
├── test_rag.py            # Legacy RAG test suite (10 queries, LLM + ChromaDB)
├── verify_unicode.py      # Arabic Unicode audit for processed chunks
├── patch_arabic.py        # Normalize Arabic Presentation Forms to logical Unicode
├── ocr_arabic_pdf.py      # EasyOCR-based PDF→text for Arabic documents
└── bench_tokens.py        # Benchmark token budget usage of conversations
```

## Config & monitoring
```
config/
├── prometheus.yml              # Prometheus scrape config (targets backend:8000)
└── grafana/
    ├── dashboards/
    │   ├── dashboards.yml      # Grafana provisioning: auto-load dashboards
    │   └── tunisia-energy-rag.json  # 12-panel Grafana dashboard
    └── datasources/
        └── datasources.yml     # Grafana provisioning: Prometheus datasource
```

## Root-level docs
```
System_Architecture.md    # Ingestion pipeline architecture documentation
COMPTE_RENDU_PROJET.md    # French project summary
PRODUCT_PLAN.md           # Sprint roadmap + feature tracking
PROJECT_SNAPSHOT.md       # This file
README.md                 # Setup, usage, API docs
```

## Data directory
```
data/
├── raw/                   # Downloaded PDFs (triage input)
├── filtered/              # Accepted PDFs (73 files → 11,826 chunks in ChromaDB)
├── blacklisted/           # Rejected PDFs
├── processed/             # processed_chunks.json (intermediate)
├── chroma_db/             # ChromaDB persistent storage
├── eval/golden_qa.json    # 6 golden queries for retrieval evaluation
├── scores.json            # Triage scores history
├── collection_log.json    # Download dedup log
└── triage_report.md       # Auto-generated triage output
```

---

## 2. Features 100% Completed & Working

### ✅ RAG Pipeline
- Full async pipeline: query rewrite → hybrid retrieval (vector + BM25 + RRF) → generation via Mistral Large
- SSE streaming (`/api/chat/stream`) consumed by React frontend
- Token-budget history truncation (tiktoken, 1500 token limit)
- Cross-encoder reranking (env-gated, graceful fallback)
- Evaluation harness: recall@5 improved from 0.83 → 1.00 on golden set

### ✅ Observability
- Structured JSON logging (python-json-logger → stdout, configurable via `LOG_LEVEL` env var)
- **Prometheus `/metrics` endpoint**: request latency histogram, request counters, LLM token usage, purge counts, ingestion results, active request gauge
- **Built-in HTML dashboard** (`GET /dashboard`): dark-themed Chart.js dashboard with 6 stat cards + 6 charts, auto-refreshes every 15s
- Request metrics middleware: records latency and count for every endpoint (excludes /metrics itself)
- LLM token tracking: prompt + completion tokens on every model call (rewrite, generate, triage)
- Ingestion counters: indexed/rejected/failed per document
- Prometheus + Grafana config files ready for Docker Compose deployment (`config/`)

### ✅ Backend API (168 tests, all green)
- Chat endpoints: `/api/chat` (JSON) + `/api/chat/stream` (SSE)
- Conversations: CRUD, per-user ownership, message persistence
- Outage reports: CRUD, status filtering, TTL auto-purge (5h default, 30min interval)
- Admin endpoints: purge stats, manual purge, runtime config, document upload/ingestion
- **Source management**: CRUD + crawl + research (download) + ingest (triage + ChromaDB) + bulk-delete
- **SSE progress streaming**: `/api/admin/sources/research/stream` and `/ingest/stream` — per-file progress events
- Auth: register/login/me, JWT, bcrypt, constant-time admin key check
- Rate limiting: per-endpoint (slowapi), env-tunable, in-memory (Redis-ready)
- Security headers: nosniff, DENY, Referrer-Policy, opt-in HSTS/CSP
- CORS: env-driven origins whitelist
- Readiness probe: `/ready` (DB + Chroma, 503 when degraded), `/health` (liveness)
- Outage TTL purge: background loop, admin-configurable TTL + interval via settings table

### ✅ Frontend (126 tests, all green)
- Chat interface with SSE streaming, SourcesDropdown (copy + expand full text)
- Animated Tunisia SVG map (24 governorates) + Leaflet outage map
- Click-to-filter: SVG node → Leaflet markers filtered by governorate
- Status filter (ALL/PENDING/RESOLVED) with donut chart segments
- Solar ROI calculator (Recharts)
- Auth modal (login/register tabs)
- **Admin panel** with two tabs:
  - **Sources tab**: add URL (single PDF) or crawl website (BFS with configurable depth), research (download), ingest (triage + ChromaDB)
  - **Dashboard tab**: master log of indexed/rejected/failed sources with stats cards, filter tabs, bulk clear
- **ProgressPanel**: real-time SSE progress bar, current filename, live stats during research/ingest
- i18n: French, Arabic (RTL) — Derja locale removed this session
- Theme: dark/light toggle
- Responsive layout with sidebar
- Sticky footer on admin page

### ✅ Database & Migrations
- PostgreSQL 16 (async SQLAlchemy + asyncpg)
- Alembic migrations: `0001_initial.py` (users, conversations, messages, outage_reports, settings), `0002_add_sources_table.py` (sources)
- Source model with SourceStatus enum (pending, downloading, downloaded, failed, ingesting, indexed, triage_rejected)
- Seeder: demo data, idempotent, `--reset` support
- Local dev + Docker use the same schema

### ✅ Ingestion Pipeline
- PDF collector (SerpApi → DuckDuckGo fallback)
- OCR for Arabic PDFs (EasyOCR + pdf2image + Poppler)
- Chunking (LangChain RecursiveCharacterTextSplitter, 1000/150)
- LLM triage: 2-gate scoring (metadata + text samples)
- Admin upload: file or URL → raw → triage → filtered/blacklisted → chunk + embed into ChromaDB
- **Recursive website crawl**: BFS with configurable depth (0-4), same-domain only, polite delay, PDF link extraction

### ✅ Infrastructure
- Docker Compose: postgres, db-seed, backend, frontend/nginx, ngrok, pg-backup
- Readiness-based healthchecks (Docker waits for DB + Chroma before starting frontend)
- Nightly pg_dump backups with retention (configurable)
- Manual backup/restore scripts
- `start_dev.bat` for local development

### ✅ Tests
- **Backend: 168 tests** (all green) — 157 existing + 11 new metrics/logging tests
- **Frontend: 126 tests** (vitest + React Testing Library, all green)
- Auto-test config with timing baseline (`tests/auto_test_config.md`)

---

## 3. Current API Endpoints

### Chat
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/chat` | JWT (optional) | RAG answer (JSON) |
| POST | `/api/chat/stream` | JWT (optional) | RAG answer (SSE stream) |

### Conversations
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/conversations` | JWT | List user's conversations |
| POST | `/api/conversations` | JWT | Create conversation |
| GET | `/api/conversations/{id}` | JWT | Get conversation messages |
| DELETE | `/api/conversations/{id}` | JWT | Delete conversation |

### Outages
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/outages` | No | List outage reports |
| POST | `/api/outages` | No | Create outage report |
| GET | `/api/outages/stats` | No | Outage statistics |
| DELETE | `/api/outages/{id}` | No | Delete report |

### Admin
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/admin/purge-stats` | Admin key | Purge statistics |
| POST | `/api/admin/purge` | Admin key | Manual purge |
| GET | `/api/admin/config` | Admin key | Runtime settings |
| PUT | `/api/admin/config` | Admin key | Update settings |
| POST | `/api/admin/documents/upload` | Admin key | Upload PDF → triage → index |
| POST | `/api/admin/documents/from-url` | Admin key | Ingest PDF from URL |
| GET | `/api/admin/sources` | Admin key | List all sources |
| POST | `/api/admin/sources` | Admin key | Add PDF URL |
| DELETE | `/api/admin/sources/{id}` | Admin key | Delete source |
| POST | `/api/admin/sources/crawl` | Admin key | Crawl website for PDFs |
| POST | `/api/admin/sources/research` | Admin key | Download all pending (JSON response) |
| POST | `/api/admin/sources/research/stream` | Admin key | Download all pending (SSE progress) |
| POST | `/api/admin/sources/ingest` | Admin key | Triage + index all downloaded (JSON) |
| POST | `/api/admin/sources/ingest/stream` | Admin key | Triage + index all downloaded (SSE progress) |
| POST | `/api/admin/sources/bulk-delete` | Admin key | Bulk delete by IDs or status |

### Auth
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register` | No | Create account |
| POST | `/api/auth/login` | No | Login → JWT |
| GET | `/api/auth/me` | JWT | Current user info |

### Observability
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/health` | No | Liveness (always 200) |
| GET | `/ready` | No | Readiness (DB + Chroma check) |
| GET | `/metrics` | No | Prometheus metrics (text/plain) |
| GET | `/dashboard` | No | Built-in HTML metrics dashboard (Chart.js) |

---

## 4. .env Variables Reference

```
# === Required ===
DATABASE_URL=postgresql+asyncpg://postgres:siso@localhost:5432/energie_tunisie
JWT_SECRET=<long random string>
CUSTOM_API_KEY=<LLM API key>
OPENAI_BASE_URL=<LLM base URL>
ADMIN_API_KEY=<long random string>

# === Optional ===
LOG_LEVEL=INFO|DEBUG|WARNING
RATE_LIMIT_ENABLED=true|false
RATE_LIMIT_CHAT=10/minute
RATE_LIMIT_AUTH=10/minute
RATE_LIMIT_DEFAULT=60/minute
CORS_ORIGINS=*
NGROK_AUTHTOKEN=<if using ngrok>
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAYS=7
```

---

## 5. Lessons Learned

### 🔴 Critical traps — DO NOT repeat

#### 1. The `uv` Python trap (caused the 500 + stale process confusion)
**What happened:** A backend process was started with `uv`'s Python (`AppData\\Roaming\\uv\\python\\...`), which is a **different Python environment** than the project's `.venv`. This caused:
- Different package versions
- Different environment variable loading behavior
- The process returned 500 instead of the expected 503 (suggesting different code or behavior)
- `nohup` background processes are hard to kill on Windows (the `taskkill //F` + `//PID` syntax is required, not `taskkill /F /PID`)

**Lesson:** Always start the backend with the project venv: `.venv/Scripts/python.exe -m uvicorn src.api.main:app --port 8000`. Never use `uv run` or a system Python for the dev server.

#### 2. Postgres credential mismatch (caused the 500)
**What happened:** `docker-compose.yml` defines `postgres:postgres` (Docker, port 5433), but local dev uses a **separate PostgreSQL 18 install** on port 5432 with password `siso`. No `DATABASE_URL` in `.env` → app used the default `postgres:postgres` → `asyncpg.InvalidPasswordError` → 500.

**Lesson:** Always set `DATABASE_URL` explicitly in `.env` for local dev. The default URL in `src/database/connection.py` is for Docker, not local. The `.env.example` still says `postgres:postgres` which is misleading for local dev.

#### 3. Arabic text crashes on Windows cp1252 console
**What happened:** `print()` of Arabic standalone queries raised `UnicodeEncodeError` on Windows cp1252 consoles. This was a latent bug found during the RAG quality evaluation.

**Fix applied:** `retrieve.py` now forces UTF-8 stdout. But any future `print()` of Arabic text should be wrapped in a try/except or use logging (which handles encoding better).

**Lesson:** Always use `PYTHONIOENCODING=utf-8` when running Python scripts that may output Arabic text on Windows. Or better, use `logging` instead of `print()`.

#### 4. `spawn_agents` JSON escaping trap (repeated 6+ times in conversation)
**What happened:** The `spawn_agents` tool was called with manually-escaped JSON strings (nested quotes, pipe chars, backslashes), which repeatedly failed parsing. Every instance was `JSON Parse error: Property name must be a string literal` or `Expected '}'` or `Unterminated string`.

**Lesson:** Always pass `spawn_agents` parameters as a proper JSON object, never as a stringified JSON. Break complex shell commands into simpler pieces if escaping is getting complex. Use `run_terminal_command` for shell commands instead of `spawn_agents` when possible.

#### 5. `str_replace` failures on multi-line code edits (repeated 3+ times)
**What happened:** `str_replace` tool calls with very long `oldString`/`newString` values failed with `Expected ']'` or `Unterminated string`. This happened when trying to edit locale JSON files, test files, and other large blocks.

**Lesson:** For large file changes, prefer `write_file` (complete file rewrite) or break the edit into smaller, more targeted `str_replace` calls with shorter strings. For JSON files like locales, regenerate the full file content rather than patching.

#### 6. `write_file` patch failure on `triage.py`
**What happened:** `write_file` applied a patch instead of a full write, and the patch failed because the old content didn't match exactly (trailing whitespace, encoding differences).

**Fix:** Created the file as `triage_new.py` then `mv` it into place.

**Lesson:** For files that need substantial changes, write to a temp filename and then `mv` into place to avoid patch-apply failures.

#### 7. `triage.py` side effects on import
**What happened:** `triage.py` ran `load_dotenv()`, created `.env` if missing, printed warnings, and created an OpenAI client at module-level. This meant importing it from the API would execute all of these side effects.

**Fix:** Refactored to lazy-loaded client (`get_client()`) and guarded side effects behind `if __name__ == "__main__"`.

**Lesson:** Any module that might be imported by the API should be side-effect-free at import time. Use lazy initialization for clients and guard file creation / env loading.

#### 8. Windows process management is painful
**What happened:** Multiple attempts to kill/start background processes timed out. `nohup ... &` in bash on Windows doesn't always background correctly. `netstat -ano` + `taskkill //PID` is the reliable pattern. PowerShell `Get-Process` is more reliable for finding process details.

**Lesson:** For background processes on Windows, use explicit PID tracking. Don't rely on `nohup &` for reliable backgrounding — consider running uvicorn in a separate terminal/pane instead.

#### 9. `source .env` doesn't work in Windows bash
**What happened:** `set -a; source .env 2>/dev/null` is a Linux pattern. In Git Bash on Windows, `source` may work but the env vars may not propagate to child Python processes correctly.

**Lesson:** For Windows, either use `.venv/Scripts/python.exe` directly (which picks up `.env` via `dotenv`), or use PowerShell with `$env:VAR = "value"` syntax.

#### 10. Frontend `accept` attribute blocks test file upload
**What happened:** A test trying to upload a `.txt` file to a `<input accept=".pdf">` was silently blocked by `userEvent.upload` — the button stayed disabled because no file was selected.

**Fix:** Use a `.pdf` filename in tests: `new File(['content'], 'test.pdf', { type: 'application/pdf' })`.

**Lesson:** When testing file uploads with `<input accept>`, always use the correct extension in the test file name.

#### 11. Duplicate i18n key causes "object instead of string" error
**What happened:** Both `fr.json` and `ar.json` had two `"status"` keys inside `admin.sources` — a string (column header) and an object (status labels). i18next silently uses the last duplicate key, so `t("admin.sources.status")` returned the object instead of the string.

**Fix:** Renamed the nested object to `"statusLabels"` and updated all component references.

**Lesson:** Always verify locale JSON files don't have duplicate keys at the same nesting level. Use a JSON linter or IDE that flags this.

#### 12. SSE streaming needs `fetch` not `axios`
**What happened:** `axios` doesn't support streaming responses natively. The SSE progress endpoints returned `text/event-stream` but `axios` tried to parse the whole response as JSON.

**Fix:** Used native `fetch()` with `ReadableStream` for SSE endpoints, keeping `axios` for regular JSON endpoints.

**Lesson:** For SSE streaming, always use native `fetch()` with `resp.body.getReader()`. `axios` is for JSON/REST only.

#### 14. WSL2 Docker networking: localhost ports don't reach Windows
**What happened:** Docker containers running inside WSL2 with `-p 9090:9090` bind ports inside WSL2's network namespace, but Windows' `localhost` doesn't forward to WSL2 ports. Even with `networkingMode=mirrored` in `.wslconfig`, Docker containers don't get the mirrored treatment. `netsh interface portproxy` also didn't work reliably.

**Fix:** Created a built-in HTML dashboard (`GET /dashboard`) served directly from the FastAPI backend at port 8000 — no Docker/Prometheus/Grafana networking required. For production, install Docker Desktop for Windows (which handles port forwarding), or deploy Prometheus/Grafana as separate containers outside WSL2.

**Lesson:** Don't rely on WSL2 port forwarding for Docker containers. Either use Docker Desktop for Windows, or serve dashboards from the app itself. The `/metrics` endpoint (Prometheus text format) works fine for external scraping; the visualization just needs to reach it.

#### 15. ORM objects are not thread-safe
**What happened:** The research/ingest functions modified SQLAlchemy ORM objects (`source.status = ...`) inside `run_in_threadpool()`, which caused "another operation is in progress" errors in async tests.

**Fix:** Refactored to plain-data functions (`download_pdf_from_source()` returns a dict, API endpoint applies it to ORM objects in the async context).

**Lesson:** Never pass ORM objects into thread pool workers. Return plain data dicts from thread pool functions and apply them to ORM objects in the async event loop.

---

## 6. Running the App Locally

### Local dev (recommended)
```bash
# Terminal 1: Backend
cd <project-root>
.venv/Scripts/python.exe -m uvicorn src.api.main:app --port 8000

# Terminal 2: Frontend
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### Key .env variables (local dev)
```
DATABASE_URL=postgresql+asyncpg://postgres:siso@localhost:5432/energie_tunisie
JWT_SECRET=<set to any long random string>
CUSTOM_API_KEY=<your LLM API key>
OPENAI_BASE_URL=<your LLM base URL>
ADMIN_API_KEY=<any long random string for admin panel>
```

### Running tests
```bash
# Backend (168 tests)
python -m pytest tests/ -q

# Frontend (126 tests)
cd frontend && npm test

# Metrics dashboard
open http://localhost:8000/dashboard
# Raw Prometheus metrics
open http://localhost:8000/metrics
```

---

## 7. Commit Status

**Last commit:** `8972cf7` — "Add real-time SSE progress panel for research/ingest + recursive crawl + cleanup"

**Uncommitted changes this session:**
- New: `src/api/dashboard.py`, `src/api/logging_config.py`, `src/api/metrics.py`, `tests/test_metrics.py`
- New: `config/prometheus.yml`, `config/grafana/dashboards/`, `config/grafana/datasources/`
- Modified: `src/api/main.py`, `src/rag/retrieve.py`, `src/ingestion/research.py`, `requirements.txt`, `docker-compose.yml`, `PRODUCT_PLAN.md`, `PROJECT_SNAPSHOT.md`

**User's standing rule: never commit unless they explicitly ask.**

---

## 8. Next Steps for Next Chat

### Next steps (from PRODUCT_PLAN.md — focus: AI guardrails + testing)

**Sprint 1 — AI Guardrails:**
1. **Hallucination detection** — verify answers reference retrieved chunks; score groundedness
2. **Prompt injection defense** — sanitize queries, detect role-play/jailbreak attempts
3. **Content filtering** — refuse out-of-scope topics (non-energy), block harmful content
4. **Cost guardrails** — per-user daily chat cap, token budget limits, dashboard alerts
5. **Answer quality scoring** — auto-score relevance/citations, track over time

**Sprint 2 — AI Testing:**
6. **Expand golden set** — grow from 6 to 30+ queries (Arabic, French, multi-turn, edge cases)
7. **Automated eval pipeline** — run eval on ingestion, track recall@5/MRR over time
8. **Adversarial test suite** — injection attempts, language mixing, out-of-scope, long queries
9. **Retrieval regression tests** — verify new docs are retrievable, embedding consistency
10. **Prompt regression tests** — snapshot testing, token efficiency, language consistency

**Sprint 3 — Product + Ops:**
11. Outage moderation, usage quotas, user profiles
12. CI/CD, Sentry, Redis cache, load testing

### Context for next chat
- The project is fully functional: chat, map, calculator, admin panel with sources management + dashboard
- **Observability is complete**: structured JSON logs, Prometheus /metrics, built-in HTML dashboard at `http://localhost:8000/dashboard`
- All **294 tests** pass (168 backend + 126 frontend)
- The server runs on port 8000 (backend) + 5173 (frontend dev)
- PostgreSQL is local on port 5432 (password: `siso`)
- The LLM is accessed via OpenAI-compatible API (Mistral Large via BYNA router)
- 17 STEG documents (2,491 chunks) are indexed in ChromaDB
- The admin can crawl websites, download PDFs, triage them, and index accepted ones — all with real-time progress
- **Current focus: AI guardrails and testing (Sprint 1 + 2 of PRODUCT_PLAN.md)**
