# Project Snapshot — Tunisia Energy RAG
**Generated:** 2026-08-18 · **Branch:** main · **Last commit:** `21a12ae` (rate limiting, security headers, Alembic schema)

---

## 1. Architecture & Tech Stack

### What this is
A RAG (Retrieval-Augmented Generation) platform for the Tunisian energy sector:
- **Chat** — users ask energy questions; the system retrieves relevant PDF chunks and generates sourced answers (French + Arabic)
- **Outage Map** — crowdsourced live outage map with animated SVG Tunisia map + Leaflet markers
- **Solar ROI Calculator** — client-side financial projections
- **Admin Panel** — document upload, outage purge, runtime config, PDF ingestion (upload/link → triage → chunk + embed into ChromaDB)

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
| **Infra** | Docker Compose (postgres, backend, frontend/nginx, ngrok, pg-backup) | `docker-compose.yml` |

### Key file map
```
src/
├── api/
│   ├── main.py          # FastAPI app, all endpoints (chat, outages, admin, auth router)
│   ├── auth.py           # register/login/me endpoints + JWT dependency
│   ├── ratelimit.py      # slowapi rate limiter config
│   └── security.py       # CORS + security headers middleware
├── database/
│   ├── connection.py     # AsyncEngine + session factory (reads DATABASE_URL from .env)
│   ├── models.py         # SQLAlchemy models (User, Conversation, Message, OutageReport, Setting)
│   ├── service.py        # All DB CRUD operations
│   ├── seed.py           # Database seeder (demo data)
│   └── schema.py         # Alembic env + ensure_schema
├── ingestion/
│   ├── collector.py      # PDF downloader (SerpApi + DuckDuckGo fallback)
│   ├── ingest_chunks.py  # PDF → text extraction → chunking → processed_chunks.json
│   ├── indexer.py        # chunk + embed + upsert into ChromaDB (for admin upload)
│   └── admin_ingest.py   # orchestrate upload/download → triage → index
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
│   ├── admin/            # ConfigEditor, DocumentUpload
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

### ✅ Backend API (144 tests, all green)
- Chat endpoints: `/api/chat` (JSON) + `/api/chat/stream` (SSE)
- Conversations: CRUD, per-user ownership, message persistence
- Outage reports: CRUD, status filtering, TTL auto-purge (5h default, 30min interval)
- Admin endpoints: purge stats, manual purge, runtime config (settings table), document upload/ingestion
- Auth: register/login/me, JWT, bcrypt, constant-time admin key check
- Rate limiting: per-endpoint (slowapi), env-tunable, in-memory (Redis-ready)
- Security headers: nosniff, DENY, Referrer-Policy, opt-in HSTS/CSP
- CORS: env-driven origins whitelist
- Readiness probe: `/ready` (DB + Chroma, 503 when degraded), `/health` (liveness)
- Outage TTL purge: background loop, admin-configurable TTL + interval via settings table

### ✅ Frontend (118 tests, all green)
- Chat interface with SSE streaming, SourcesDropdown (copy + expand full text)
- Animated Tunisia SVG map (24 governorates) + Leaflet outage map
- Click-to-filter: SVG node → Leaflet markers filtered by governorate
- Status filter (ALL/PENDING/RESOLVED) with donut chart segments
- Solar ROI calculator (Recharts)
- Auth modal (login/register tabs)
- Admin panel: purge stats, "Purge now" button, config editor, document upload (file/link)
- i18n: French, Arabic (RTL) — Derja locale removed this session
- Theme: dark/light toggle
- Responsive layout with sidebar

### ✅ Database & Migrations
- PostgreSQL 16 (async SQLAlchemy + asyncpg)
- Alembic migrations: `0001_initial.py` (users, conversations, messages, outage_reports, settings)
- Seeder: demo data, idempotent, `--reset` support
- Local dev + Docker use the same schema

### ✅ Ingestion Pipeline
- PDF collector (SerpApi → DuckDuckGo fallback)
- OCR for Arabic PDFs (EasyOCR + pdf2image + Poppler)
- Chunking (LangChain RecursiveCharacterTextSplitter, 1000/150)
- LLM triage: 2-gate scoring (metadata + text samples)
- Admin upload: file or URL → raw → triage → filtered/blacklisted → chunk + embed into ChromaDB

### ✅ Infrastructure
- Docker Compose: postgres, db-seed, backend, frontend/nginx, ngrok, pg-backup
- Readiness-based healthchecks (Docker waits for DB + Chroma before starting frontend)
- Nightly pg_dump backups with retention (configurable)
- Manual backup/restore scripts
- `start_dev.bat` for local development

### ✅ Tests
- **Backend: 144 tests** (fast: 138, medium: 140, full: 144)
- **Frontend: 118 tests** (vitest + React Testing Library)
- Auto-test config with timing baseline (`tests/auto_test_config.md`)

---

## 3. Immediate Task & Current Status

### What we just fixed (this session)
**Registration was returning 500.** Root cause chain:
1. `.env` was missing `JWT_SECRET` → auth endpoints returned 503 (this was fixed by the user adding it)
2. `.env` was also missing `DATABASE_URL` → app fell back to `postgres:postgres@localhost:5432/energie_tunisie`
3. **Local PostgreSQL 18** (installed at `C:/Program Files/PostgreSQL/18`) was running on port 5432 with password **`siso`** — not `postgres`
4. The app had been started with **`uv`'s Python** (separate from the project venv), which was a stale process

### What was done to fix it
1. Created `DATABASE_URL=postgresql+asyncpg://postgres:siso@localhost:5432/energie_tunisie` in `.env`
2. Created the `energie_tunisie` database (it didn't exist — the local PG only had `sgel_db` and `sgel_db_test`)
3. Ran `alembic upgrade head` → all tables created
4. Killed the stale uv-python process on :8000, restarted with the project venv (`.venv/Scripts/python.exe -m uvicorn src.api.main:app --port 8000`)
5. Verified: register → 201 + JWT, login → 200 + JWT, auth suite 20/20

### What's NOT committed yet (all these changes are uncommitted)
The `git status` shows 15 modified files + 14 new files. These include:
- `src/api/auth.py` (auth endpoints)
- `src/api/security.py` (CORS + headers)
- `src/api/ratelimit.py` (rate limiting)
- `src/rag/hybrid.py` (hybrid retrieval)
- `src/eval/` (evaluation harness)
- `src/ingestion/indexer.py` + `admin_ingest.py` (admin document upload)
- `tests/test_readiness.py`, `test_hybrid.py`, `test_eval_metrics.py`, `test_admin_docs.py`
- `frontend/` locale updates, AdminPage, DocumentUpload, etc. (derja removed)
- `docker-compose.yml` (pg-backup, healthchecks)
- `README.md`, `PRODUCT_PLAN.md`, `tests/auto_test_config.md`
- File reorganization: 4 scripts moved to `scripts/`, stale files deleted

**User's standing rule: never commit unless they explicitly ask.**

### What's NOT done yet per PRODUCT_PLAN.md
- ❌ Sentry error tracking (P0 — user said skip for now, solo dev)
- ❌ CI/CD pipeline (P0 — user said skip for now, solo dev)
- ❌ Outage moderation workflow (P1 — approve/reject queue, duplicate detection)
- ❌ Outage notifications (P1 — Telegram/email/push)
- ❌ Usage quotas / cost guardrails (P1)
- ❌ Scheduled ingestion (P1 — collector → triage → OCR → embed as cron)
- ❌ Vector DB ops (P1 — Chroma backup/rebuild scripts)
- ❌ Redis cache (P2)
- ❌ CDN (P2)
- ❌ Load testing (P2)
- ❌ Legal/compliance (P2)
- ❌ Landing page + docs (P2)
- ❌ Privacy-friendly analytics (P2)
- ❌ Security audit (P2)
- ❌ Runbook (P2)

---

## 4. Lessons Learned

### 🔴 Critical traps — DO NOT repeat

#### 1. The `uv` Python trap (caused the 500 + stale process confusion)
**What happened:** A backend process was started with `uv`'s Python (`AppData\Roaming\uv\python\...`), which is a **different Python environment** than the project's `.venv`. This caused:
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

---

## 5. Running the App Locally

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
# Backend fast (138 tests, ~10s + 20s model load)
python -m pytest tests/test_integration.py -k "health or empty or retrieval" -q && python -m pytest tests/test_retrieval.py tests/test_token_manager.py tests/test_database.py tests/test_seed.py tests/test_api_db.py tests/test_auth.py tests/test_ratelimit.py tests/test_migrations.py tests/test_readiness.py tests/test_hybrid.py tests/test_eval_metrics.py tests/test_admin_docs.py -q

# Frontend (118 tests)
cd frontend && npm test
```

---

## 6. Commit Status

**Last commit:** `21a12ae` — "Add rate limiting, security headers, and Alembic-managed schema"

**Uncommitted changes (15 modified + 14 new files):**
- Modified: `.gitignore`, `PRODUCT_PLAN.md`, `README.md`, `docker-compose.yml`, `frontend/nginx.conf`, `frontend/src/locales/*`, `frontend/src/pages/AdminPage.tsx`, `frontend/src/services/admin.ts`, `requirements.txt`, `src/api/main.py`, `src/rag/retrieve.py`, `src/utils/triage.py`, `tests/auto_test_config.md`
- New: `COMPTE_RENDU_PROJET.md`, `data/eval/`, `frontend/src/components/admin/DocumentUpload.*`, `scripts/`, `src/eval/`, `src/ingestion/admin_ingest.py`, `src/ingestion/indexer.py`, `src/rag/hybrid.py`, `tests/test_admin_docs.py`, `tests/test_eval_metrics.py`, `tests/test_hybrid.py`, `tests/test_readiness.py`

**User's standing rule: never commit unless they explicitly ask.**
