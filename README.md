# Tunisia Energy RAG

RAG pipeline for the Tunisian energy sector: PDF collection, Arabic/OCR ingestion,
LLM-based triage, ChromaDB vector retrieval, an async FastAPI backend, a React
frontend (Vite build served by Nginx), and an async SQLAlchemy/PostgreSQL layer for
conversations and the crowdsourced outage map.

## Quick start (Docker Compose)

`docker compose up` starts the full stack with **PostgreSQL** and **auto-seeding**:

| Service | Purpose | URL |
|---|---|---|
| `postgres` | PostgreSQL 16 (port `127.0.0.1:5433`, internal `postgres:5432`) | — |
| `db-seed` | One-shot seeder — runs `src/database/seed.py` once Postgres is healthy, then exits | — |
| `backend` | FastAPI (healthcheck on `/health`) | http://localhost:8000 |
| `frontend` | React SPA (Vite build → Nginx, proxies `/api` to backend) | http://localhost |
| `ngrok` | Public tunnel to the frontend (requires `NGROK_AUTHTOKEN`) | http://localhost:4040 |

### Environment

```bash
cp .env.example .env      # then fill in NGROK_AUTHTOKEN (and CUSTOM_API_KEY / OPENAI_BASE_URL)
docker compose up --build
```

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
  inserts data. In docker, `db-seed` applies migrations (`ensure_schema` →
  `alembic upgrade head`) then seeds, and the backend starts only after `db-seed`
  completes successfully. Data is persisted in the `postgres_data` named volume.
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
```

- Connect from the host (e.g. `psql`): `postgresql://postgres:postgres@localhost:5433/energie_tunisie`

### Service healthchecks

- `postgres` → `pg_isready -U postgres -d energie_tunisie`
- `backend` → GET `http://localhost:8000/health`
- `frontend` → GET `/health` through Nginx (proxied to the backend)
- `frontend` and `ngrok` only start once `backend` is healthy (`depends_on: condition: service_healthy`).

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

## Tests

See `tests/auto_test_config.md` for the fast / medium / full run commands and the
per-test timing baseline.

## Pipeline

1. **Collect** — `src/ingestion/collector.py` (SerpApi → DuckDuckGo hybrid) downloads domain PDFs.
2. **Triage** — `src/utils/triage.py` multi-gate LLM filtering (`data/raw` → `data/filtered` / `data/blacklisted`).
3. **Ingest** — `src/ingestion/ingest_chunks.py` extracts text (pdfplumber + EasyOCR for Arabic), chunks, and writes `data/processed/processed_chunks.json`.
4. **Embed & index** — sentence-transformers embeddings into ChromaDB (`data/chroma_db`).
5. **Serve** — `src/api/main.py` exposes `/api/chat`, `/api/chat/stream` (SSE), `/api/outages` and `/api/conversations`; the React SPA consumes them.
