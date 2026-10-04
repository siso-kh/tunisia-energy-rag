# Tunisia Energy RAG ⚡

A retrieval-augmented generation (RAG) assistant that answers questions about
Tunisia's energy sector from a corpus of official PDF documents — in French,
Arabic and English.

## What it does

- **Grounded answers** — every reply ships the source documents it drew from, and
  questions outside the corpus are refused rather than invented.
- **Arabic-aware ingestion** — `pdfplumber` text extraction with an EasyOCR
  fallback for scanned and Arabic documents.
- **Hybrid retrieval** — BM25 keyword search blended with multilingual vector
  embeddings in ChromaDB, then reranked by a multilingual cross-encoder.
- **Crowdsourced outage map** — users report and browse live energy outages by region.
- **Admin console** — upload PDFs or ingest a URL; an LLM triages each document
  into the index or the blacklist.

## Stack

| Layer | Technology |
|---|---|
| Ingestion & triage | pdfplumber, EasyOCR, LLM triage |
| Retrieval | ChromaDB, BM25, multilingual sentence-transformers, cross-encoder rerank |
| Backend | FastAPI (async), SQLAlchemy, PostgreSQL, Alembic |
| Frontend | React + Vite, served by Nginx |

> **Deployment:** [`DEPLOY_HF_SPACE.md`](DEPLOY_HF_SPACE.md) is the runbook for the
> Hugging Face Docker Space (secrets, push commands, post-deploy verification).

## Quick start (Docker Compose)

Brings up the entire stack — PostgreSQL included — and migrates + seeds the
database on first boot:

```bash
cp .env.example .env      # fill in CUSTOM_API_KEY, JWT_SECRET, ADMIN_API_KEY, NGROK_AUTHTOKEN
docker compose up --build
```

Then open **http://localhost**.

| Service | What it does | URL |
|---|---|---|
| `frontend` | React SPA — chat, outage map, solar ROI calculator | [http://localhost](http://localhost) |
| `backend` | FastAPI API — health check at `/health` | [http://localhost:8000](http://localhost:8000) |
| `ngrok` | Public tunnel to the frontend (needs `NGROK_AUTHTOKEN`) | [http://localhost:4040](http://localhost:4040) |
| `postgres` | PostgreSQL 16 — host port `5433`, internal `5432` | not web-facing |
| `db-seed` | One-shot job — migrates and seeds once Postgres is healthy, then exits | — |

To share the running app publicly over a tunnel, see
[`DEPLOY_NGROK.md`](DEPLOY_NGROK.md). The free ngrok tier is also the $0
fallback when no cloud host is available — the tunnel forwards bytes, so the
app runs on your machine and the ~940 MB memory ceiling simply doesn't apply.
See [`DEPLOY_HF_SPACE.md`](DEPLOY_HF_SPACE.md) §4.2 for the host comparison.

### Environment

`docker compose` reads `.env`. These four must be set before the first start:

| Variable | Why |
|---|---|
| `CUSTOM_API_KEY` | LLM credential used for answering and triage |
| `JWT_SECRET` | Signs user session tokens |
| `ADMIN_API_KEY` | Guards the `/api/admin/*` endpoints |
| `NGROK_AUTHTOKEN` | Lets the `ngrok` service start — the stack refuses to boot without it |

`OPENAI_BASE_URL` is optional; set it only for a non-default OpenAI-compatible
endpoint. See [`.env.example`](.env.example) for every option.

The React frontend lives in [`frontend/`](frontend/): chat (SSE streaming from
`/api/chat/stream`), the outage map (`/api/outages`), and a solar ROI calculator.
Local dev:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to localhost:8000
```

### Database

- Connection string: `postgresql+asyncpg://postgres:postgres@postgres:5432/energie_tunisie`
  (the `DATABASE_URL` env var is set automatically for `backend` and `db-seed`).
- The schema is owned by **Alembic migrations** (`alembic/versions/`); the seeder only
  inserts data. In docker, `db-seed` runs `alembic upgrade head` (async engine) then
  seeds, and the backend starts only after `db-seed` completes successfully. This
  mirrors the HF Space entrypoint; `src.database.seed`'s `ensure_schema` uses a *sync*
  engine (needs `psycopg2`) and cannot create the schema itself. Data is persisted in
  the `postgres_data` named volume.
- Local dev uses the **same Postgres** (`start_dev.bat` targets `localhost:5433`) —
  one dialect everywhere. The in-memory SQLite inside `pytest` is the disposable
  test lab and never holds real data.
- Reseed from scratch:

```bash
docker compose run --rm db-seed python -m src.database.seed --reset

# local (must point at the right DATABASE_URL, e.g. the docker Postgres)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/energie_tunisie \
  python -m src.database.seed --reset
```

### Migrations (Alembic)

```bash
alembic upgrade head      # apply pending migrations (uses DATABASE_URL / .env)
alembic -x url="postgresql+asyncpg://postgres:postgres@localhost:5433/energie_tunisie" upgrade head

alembic current           # which revision is this DB at?
alembic history           # the migration chain

alembic revision --autogenerate -m "describe change"   # draft a migration from the models
# REVIEW the generated file, then: alembic upgrade head
alembic downgrade -1      # revert the last migration
```

Notes:
- `alembic/versions/0001_initial.py` is the hand-written baseline matching the models.
  Databases created before Alembic (via the old `create_all`) are **adopted**
  automatically: `ensure_schema` stamps them at `head` instead of re-creating them.
- `schema.sql` is a **generated reference snapshot** of the Postgres schema
  (`python -m alembic upgrade head --sql > schema.sql`) — do not edit it by hand;
  the migrations are authoritative.

- Connect from the host (e.g. `psql`): `postgresql://postgres:postgres@localhost:5433/energie_tunisie`

### Service healthchecks (liveness vs readiness)

- `postgres` → `pg_isready -U postgres -d energie_tunisie`
- `backend` → GET `http://localhost:8000/ready` (readiness: also pings Postgres + ChromaDB)
- `frontend` → GET `/ready` through Nginx (proxied to the backend)
- `frontend` and `ngrok` only start once `backend` is healthy (`depends_on: condition: service_healthy`).

The two probes serve different jobs:

| Endpoint | Meaning | Returns |
|---|---|---|
| `GET /health` | **Liveness** — the process is up and serving | `200` always (even if DB/Chroma are down) |
| `GET /ready` | **Readiness** — Postgres and ChromaDB both respond | `200` + `{"checks":{"db":true,"chroma":true}}`, else `503` listing what failed |

Use `/ready` for orchestration decisions (Docker healthchecks, load balancers) so traffic never
reaches a backend whose dependencies are down; keep `/health` for uptime/liveness monitors.

### Backups (Postgres)

A `pg-backup` compose service dumps the database on an interval into `./backups/`
(custom-format `pg_dump`, retention via `find -mtime`).

```bash
docker compose up -d pg-backup   # runs inside the stack (default: every 24h, keep 7 days)
# tune via .env:
#   BACKUP_INTERVAL_HOURS=12
#   BACKUP_RETENTION_DAYS=14
```

Manual one-shot backup / restore drill:

```bash
bash scripts/backup_db.sh                  # -> backups/energie_tunisie_<ts>.dump
bash scripts/restore_db.sh backups/energie_tunisie_<ts>.dump   # prompts before restoring
```

> `backups/` is gitignored. Run the restore drill on a throwaway environment first so a
> bad backup never silently becomes "production restored".

## Local development (no Docker)

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.database.seed --url sqlite+aiosqlite:///./dev.db   # SQLite instead of Postgres
uvicorn src.api.main:app --reload
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## User accounts (email/password + JWT)

Users can create an account and log in; the React header shows a login button
(and a user chip + logout once authenticated). Chat and outage reporting work
anonymously too — anonymous sessions fall back to a shared demo user, while
logged-in users own their conversations and reports.

```bash
# .env
JWT_SECRET=generate-a-long-random-string   # e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`
JWT_EXPIRE_MINUTES=10080                   # token lifetime in minutes (default 7 days)
```

- **Endpoints**: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.
- **Auth transport**: `Authorization: Bearer <jwt>` header, attached automatically by the
  frontend axios interceptor. The token is kept in `localStorage` (dev trade-off — XSS can
  read it; switch to an httpOnly cookie before public exposure).
- **Passwords** are bcrypt-hashed (72-byte limit enforced at validation).
- **No `JWT_SECRET` configured** → auth endpoints refuse with `503` (no weak default).
- **No oracle**: wrong password and unknown email return identical `401`s; invalid/expired
  tokens all return the same `401` body.
- Emails are normalized (lowercased) and unique; duplicates return `409`.

On databases created before auth landed, the backend adds the `users` columns
(`email`, `password_hash`, `display_name`) automatically at startup.

## Admin & Security

### Admin API key

The admin endpoints (`GET /api/admin/purge-stats`, `POST /api/admin/purge`) are
protected by an API key sent in the `X-Admin-Key` header:

```bash
# .env
ADMIN_API_KEY=generate-a-long-random-string  # e.g. `openssl rand -hex 32`
```

- **No key configured** → admin endpoints refuse with `503` (safer than a weak default).
- **Missing / wrong key** → `401`, identical response whether the header is absent or
  incorrect (no oracle for attackers).
- Comparison is **constant-time** (`hmac.compare_digest`) to prevent timing attacks.
- Header names are case-insensitive (`x-admin-key` works too); empty/whitespace keys are rejected.

The key is **never committed**: it lives only in `.env` (gitignored) or the runtime
environment. The React **Admin tab** (right sidebar) asks for this key before showing
purge stats or the "Purger maintenant" button; the key is kept **in memory only** and
never written to `localStorage`.

### Admin document ingestion (upload PDF / ingest URL)

Admins can add documents straight from the **/admin** page (or the API):

| Endpoint | What it does |
|---|---|
| `POST /api/admin/documents/upload` | Multipart PDF file upload (`X-Admin-Key` header) |
| `POST /api/admin/documents/from-url` | JSON `{"url": "https://…/doc.pdf"}` download (`X-Admin-Key` header) |

Both run the **full auto-ingestion flow** for one document:

```
upload/download -> data/raw/ -> LLM triage (2 gates) -> data/filtered/ or data/blacklisted/
                                        |
                                        +-> (if accepted) chunk + embed -> ChromaDB (searchable)
```

- Validation: `%PDF` magic-byte check, 50 MB cap (`MAX_UPLOAD_BYTES`), filename
  sanitization, http/https URLs only.
- The LLM triage (`src/utils/triage.py`, sync client) and the ChromaDB index
  (`src/ingestion/indexer.py`) run in a thread pool so the async event loop is
  never blocked.
- Re-uploading a file replaces its old chunks in ChromaDB (delete-by-source
  then upsert) instead of duplicating them.
- Without `CUSTOM_API_KEY` the triage falls back to its conservative default
  (everything is blacklisted) — set the key to actually accept documents.

### Outage report retention

Crowdsourced outage reports are deleted once they are older than `OUTAGE_TTL_HOURS`
(default **5 hours**). A background task purges expired reports at startup and then
every `OUTAGE_PURGE_INTERVAL_MINUTES` (default **30 min**):

```bash
OUTAGE_TTL_HOURS=5
OUTAGE_PURGE_INTERVAL_MINUTES=30
```

Purge activity is viewable (with the admin key) at `GET /api/admin/purge-stats`
(last runs, timestamps, counts) and a purge can be triggered manually via
`POST /api/admin/purge`. Recent run history is kept in memory (last 50 runs).

### Rate limiting

Every endpoint is rate-limited per client (`src/api/ratelimit.py`, slowapi).
Defaults (env-tunable, see `.env.example`):

| Endpoint | Default limit | Why |
|---|---|---|
| `/api/chat*` | `10/minute` (`RATE_LIMIT_CHAT`) | LLM cost guard |
| `/api/auth/register`, `/api/auth/login` | `10/minute` (`RATE_LIMIT_AUTH`) | brute-force protection |
| `POST /api/outages` | `10/minute` (`RATE_LIMIT_OUTAGE_CREATE`) | report spam |
| admin endpoints | `30/minute` (`RATE_LIMIT_ADMIN`) | abuse protection |
| everything else | `60/minute` (`RATE_LIMIT_DEFAULT`) | — |

Exceeding a limit returns **HTTP 429** with a `Retry-After` header (the frontend
shows a friendly message). Storage is in-memory by default
(`RATE_LIMIT_STORAGE_URI=memory://`); use a Redis URL in multi-instance
production so limits are shared. Behind nginx/ngrok set
`TRUST_PROXY_HEADERS=true` so limits key on the real client IP.

### Security headers & CORS

- Always-on response headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy`, `Permissions-Policy`.
- Opt-in via env: `SECURE_HSTS=true` (HTTPS only) and `SECURITY_CSP=...` for a
  Content-Security-Policy (tune it so Leaflet tiles / fonts still load).
- CORS origins come from `CORS_ORIGINS` (comma-separated, default `*` for dev).
  In production set it to your real domain(s).

### Security notes

- `.env` is gitignored; keep real keys (API keys, DB passwords, `NGROK_AUTHTOKEN`) out of version control.
- Before exposing the app publicly, review the CORS origins and consider HTTPS
  (`SECURE_HSTS`), plus a Redis-backed rate-limit storage for multi-instance deployments.

## RAG quality (hybrid retrieval + evaluation)

Retrieval is now **hybrid**: dense vector search (ChromaDB) fused with sparse
BM25 keyword search via Reciprocal Rank Fusion, so queries that rely on exact
French/Arabic terms (e.g. "autoconsommation", "ترشيد") hit even when the
embeddings drift. Optionally reranked with a multilingual cross-encoder:

```bash
RERANK_ENABLED=false          # disable reranking (default: true)
RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1   # override
RERANK_TOP_K=10               # candidates passed to the reranker
```

The cross-encoder downloads on first use (~470 MB, cached by HuggingFace);
any reranker failure degrades gracefully to the fused order.

Golden retrieval evaluation (grounded in the actual corpus, no LLM calls):

```bash
python -m src.eval.evaluate --retriever vector --k 5   # baseline
python -m src.eval.evaluate --retriever hybrid --k 5   # hybrid (default)
```

Baseline (6 golden queries, k=5): vector recall@5 **0.83** → hybrid **1.00**.
The golden set lives in `data/eval/golden_qa.json` — extend it as the corpus grows.

## Memory

The deployed image embeds queries through **ONNX Runtime**, not
sentence-transformers. Loading `paraphrase-multilingual-MiniLM-L12-v2` the
usual way costs ~830 MB resident (188 MB importing torch, ~555 MB of fp32
weights) and was enough to OOM-kill the container at boot, after which every
request answered 502 with no error frame — the process was gone before the
pipeline could report anything.

`scripts/fetch_onnx_embedder.py` bakes an ONNX build of the same weights into
the image at build time and verifies it against the fp32 model (cosine
similarity), failing the build on drift. The vectors land in the same space,
so **the existing Chroma index stays valid and must not be rebuilt**.

| build | size | cosine vs fp32 | top-5 agreement |
|---|---|---|---|
| fp32 (sentence-transformers) | 470 MB | 1.000 | 5/5 (reference) |
| `model_O4` (default) | 235 MB | 1.000 | 4.8/5 |
| `model_qint8_*` | 118 MB | 0.89–0.99 | 3.7/5 |

Measured resident memory of a warm app, through a full retrieval:

```
~135 MB  Python + FastAPI + chromadb + openai
~250 MB  tokenizer (XLM-R vocabulary)
~280 MB  ONNX embedder (model_O4)
~195 MB  Chroma corpus held for BM25 fusion
~160 MB  BM25 index
------
~940 MB  peak   (was ~1700 MB before this change)
```

`standard` (2 GB) is therefore the smallest Render plan that fits. On 512 MB
(`free` / `starter`) there is no configuration of this app that survives
startup.

To regenerate the embedder locally (writes to `models/onnx`, git-ignored):

```bash
python scripts/fetch_onnx_embedder.py --out models/onnx
```

If the model is missing, retrieval falls back to the in-process model and
logs the fallback on `/health` (`embedder: sentence-transformers`).

## Tests

See `tests/auto_test_config.md` for the fast / medium / full run commands and the
per-test timing baseline.

## Pipeline

1. **Collect** — `src/ingestion/collector.py` (SerpApi → DuckDuckGo hybrid) downloads domain PDFs.
2. **Triage** — `src/utils/triage.py` multi-gate LLM filtering (`data/raw` → `data/filtered` / `data/blacklisted`).
3. **Ingest** — `src/ingestion/ingest_chunks.py` extracts text (pdfplumber + EasyOCR for Arabic), chunks, and writes `data/processed/processed_chunks.json`.
4. **Embed & index** — sentence-transformers embeddings into ChromaDB (`data/chroma_db`).
5. **Serve** — `src/api/main.py` exposes `/api/chat`, `/api/chat/stream` (SSE), `/api/outages` and `/api/conversations`; the React SPA consumes them.

## Reports

The LaTeX deliverables live in [`docs/rapports/`](docs/rapports). Each is kept
as a `.tex` source next to its rendered `.pdf`; only the final version of each
document is tracked.

| Document | Source | Pages | Covers |
|---|---|---|---|
| Rapport de stage — ATER | [`rapport_stage.tex`](docs/rapports/rapport_stage.tex) · [PDF](docs/rapports/rapport_stage.pdf) | 57 | Internship report: ATER context, RAG architecture, ingestion pipeline, administration & security, evaluation, deployment |
| Exemples de tests du chatbot | [`rapport_tests_final.tex`](docs/rapports/rapport_tests_final.tex) · [PDF](docs/rapports/rapport_tests_final.pdf) | 13 | Real request/response samples from the running system, incl. Arabic queries and prompt-injection probes |

### Rebuilding

Both sources resolve their figures from the top-level `image/` directory via
`\graphicspath`, so compile them from their own folder:

```bash
cd docs/rapports
pdflatex rapport_stage.tex          # two passes to settle the TOC / page refs
pdflatex rapport_stage.tex

xelatex rapport_tests_final.tex     # two passes as well
xelatex rapport_tests_final.tex
```

`rapport_tests_final.tex` needs **XeLaTeX or LuaLaTeX** — it uses `fontspec`
and renders Arabic text via `polyglossia` (Traditional Arabic). Intermediates
(`*.aux`, `*.log`, `*.toc`, `*.out`, `*.lof`, `*.lot`) are git-ignored.
