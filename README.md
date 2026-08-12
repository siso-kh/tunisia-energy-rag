# Tunisia Energy RAG

RAG pipeline for the Tunisian energy sector: PDF collection, Arabic/OCR ingestion,
LLM-based triage, ChromaDB vector retrieval, an async FastAPI backend, a Streamlit
frontend, and an async SQLAlchemy/PostgreSQL layer for conversations and the
crowdsourced outage map.

## Quick start (Docker Compose)

`docker compose up` starts the full stack with **PostgreSQL** and **auto-seeding**:

| Service | Purpose | URL |
|---|---|---|
| `postgres` | PostgreSQL 16 (port `127.0.0.1:5433`, internal `postgres:5432`) | — |
| `db-seed` | One-shot seeder — runs `src/database/seed.py` once Postgres is healthy, then exits | — |
| `backend` | FastAPI (healthcheck on `/health`) | http://localhost:8000 |
| `frontend` | Streamlit UI | http://localhost:8501 |
| `ngrok` | Public tunnel to the frontend (requires `NGROK_AUTHTOKEN`) | http://localhost:4040 |

### Environment

```bash
cp .env.example .env      # then fill in NGROK_AUTHTOKEN (and CUSTOM_API_KEY / OPENAI_BASE_URL)
docker compose up --build
```

### Database

- Connection string: `postgresql+asyncpg://postgres:postgres@postgres:5432/energie_tunisie`
  (the `DATABASE_URL` env var is set automatically for `backend` and `db-seed`).
- Tables are created by the seeder; data is persisted in the `postgres_data` named volume.
- Reseed from scratch:

```bash
docker compose run --rm db-seed python -m src.database.seed --reset
```

- Connect from the host (e.g. `psql`): `postgresql://postgres:postgres@localhost:5433/energie_tunisie`

### Service healthchecks

- `postgres` → `pg_isready -U postgres -d energie_tunisie`
- `backend` → GET `http://localhost:8000/health`
- `frontend` and `ngrok` only start once `backend` is healthy (`depends_on: condition: service_healthy`).

## Local development (no Docker)

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.database.seed --url sqlite+aiosqlite:///./dev.db   # SQLite instead of Postgres
uvicorn src.api.main:app --reload
streamlit run src/ui/ui.py
```

## Tests

See `tests/auto_test_config.md` for the fast / medium / full run commands and the
per-test timing baseline.

## Pipeline

1. **Collect** — `src/ingestion/collector.py` (SerpApi → DuckDuckGo hybrid) downloads domain PDFs.
2. **Triage** — `src/utils/triage.py` multi-gate LLM filtering (`data/raw` → `data/filtered` / `data/blacklisted`).
3. **Ingest** — `src/ingestion/ingest_chunks.py` extracts text (pdfplumber + EasyOCR for Arabic), chunks, and writes `data/processed/processed_chunks.json`.
4. **Embed & index** — sentence-transformers embeddings into ChromaDB (`data/chroma_db`).
5. **Serve** — `src/api/main.py` `/api/chat` runs retrieval + LLM generation with a token-budgeted chat history.
