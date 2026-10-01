# Minimised deployment — Render

A self-contained, additive deployment for the Tunisia Energy RAG app on
**Render** (container web service) with **Neon** as the database.

> **Isolation guarantee.** Nothing in this folder imports, wraps or edits the
> app. It does not modify `Dockerfile`, `entrypoint.sh`, `nginx.hf.conf`,
> `docker-compose.yml`, `requirements.txt` or any file under `src/`. It only
> *reads* the repo. You can delete `deploy/` at any time and the project returns
> to exactly its previous state.

| File | Role |
|---|---|
| `Dockerfile` | Three-stage minimized build: React SPA → Python builder → slim runtime (nginx + uvicorn). Build context is the **repo root**. |
| `requirements.runtime.txt` | Runtime-only dependency set (see §2). Does **not** replace `requirements.txt`. |
| `nginx.conf.template` | nginx config with `listen ${PORT}`; rendered at boot by the entrypoint. |
| `entrypoint.sh` | Binds nginx to `$PORT`, fetches Chroma if needed, migrates, seeds, runs both processes. |
| `render.yaml` | Render Blueprint (service, plan, region, secrets). |

---

## 1. How this differs from the original Dockerfile

The root `Dockerfile` targets a Hugging Face Docker Space: fixed port `7860`,
a `user` UID 1000, and it installs the full ingestion stack because HF builds
from a pushed git repo that contains everything.

This one targets Render:

| | root `Dockerfile` (HF) | `deploy/render/Dockerfile` |
|---|---|---|
| Port | hardcoded `7860` | `$PORT` (Render default `10000`) |
| User | UID 1000 (`user`) | root (Render's default) |
| Chroma index | must be in the repo | in context **or** fetched via `CHROMA_URL` |
| Compiler toolchain | shipped in the final image | builder stage only |
| Build context | repo root | repo root |

The port is the one thing that genuinely cannot be shared: Render requires the
service to bind `$PORT`, and nginx cannot read environment variables in
`listen`. So the config is a template rendered by the entrypoint with a
targeted `sed`:

```bash
sed "s/\${PORT}/${PORT}/g" nginx.conf.template > /etc/nginx/nginx.conf
```

`envsubst` is deliberately **not** used — it would also erase nginx's own
`$host`, `$uri` and `$remote_addr` variables.

---

## 2. What was dropped, and why

Derived by walking the real import graph from `src/api/main.py` and separating
**module-level** imports (executed at boot → must be installed) from
**function-level** imports (only executed when that code path runs).

The decisive finding: `src/ingestion/indexer.py:91` imports `ingest_chunks`
*lazily, inside a function*:

```python
from src.ingestion.ingest_chunks import process_pdf
```

`ingest_chunks.py` is the only module that pulls the OCR stack in at module
level (`easyocr` `:10`, `pdf2image` `:9`, `torch` `:11`, `numpy` `:7`,
`langchain_text_splitters` `:12`). Because that import never runs at boot, none
of those are needed to start the server.

### Dropped

| Removed | Evidence | Consequence |
|---|---|---|
| `langchain`, `langchain-community` | **zero imports** anywhere in `src/` | none — this is the single biggest win |
| `easyocr` | only `ingest_chunks.py:10` (module) and `indexer.py:44` (lazy) | admin PDF upload/ingest raises `ImportError` |
| `langchain-text-splitters` | `ingest_chunks.py:12`, `indexer.py:89` (both lazy) | same as above |
| `pdf2image` | `ingest_chunks.py:9`, `indexer.py:62` (both lazy) | same as above |
| `pytesseract` | **never imported** anywhere | none (was already vestigial) |
| `pypdf` | **never imported** anywhere | none |
| `bs4` | **never imported** anywhere | none |
| `ddgs` | `collector.py:8` only — `collector` is not on the API path | web-crawl scripts only |
| `arabic-reshaper`, `python-bidi` | not imported in `src/`; `python-bidi` only in `scripts/patch_arabic.py` | scripts only |
| `torchvision` | only needed by `easyocr` | none |
| apt `tesseract-ocr`, `poppler-utils` | only needed by `pytesseract`/`pdf2image`/`easyocr` | OCR only |
| apt `sqlite3` (CLI) | ChromaDB uses Python's built-in `sqlite3` | none |
| apt `build-essential` | moved to the builder stage | none — compilers stay out of the final image |

**Expected effect:** roughly **1.1–1.4 GB** for the slim image versus
**~2.3–2.9 GB** for the original — call it a 40–50% reduction. Measure it
rather than trust that estimate:

```bash
docker images tunisia-energy-rag
docker history tunisia-energy-rag
```

### Kept, and why

| Kept | Reason |
|---|---|
| `chromadb` | `src/rag/retrieve.py:6-7` imports it at module level and **opens the collection at import time** (`:263-264`). A missing index is a hard boot failure. |
| `sentence-transformers`, `torch`, `numpy` | the Chroma embedding function (`retrieve.py:256`) and `hybrid.py:199` need them; torch stays even though it is the largest single dependency |
| `rank-bm25` | `src/rag/hybrid.py:58` — the sparse half of hybrid search |
| `pdfplumber` | `src/utils/triage.py:6` is a **module-level** import and `triage` is imported at module level by `admin_ingest` → genuinely required at boot |
| `openai`, `tiktoken` | `retrieve.py:8`, `triage.py:10`, `token_manager.py:10` |
| `psycopg2-binary` | **this was missing and is a bug fix** — see §5 |

### Invisible to static analysis (still required)

These are loaded **by name at runtime**, so no import scan can see them. They
are all in `requirements.runtime.txt`:

- `asyncpg` — SQLAlchemy resolves the `postgresql+asyncpg` dialect dynamically.
- `uvicorn` — invoked as a console command, never imported.
- `python-multipart` — FastAPI imports it lazily for `UploadFile` parsing.
- `email-validator` — required by pydantic for `EmailStr`.
- `greenlet` — required by SQLAlchemy's async engine.
- `psycopg2-binary` — `ensure_schema()` inspects through a *sync* engine.

---

## 3. The ChromaDB index — the real blocker on Render

`src/rag/retrieve.py` calls `get_collection()` (not `get_or_create`) at import
time, so the 132 MB index **must** be present or the container dies on boot.

Render builds from **git**, and `data/chroma_db/` is gitignored. You cannot
simply commit it: `chroma.sqlite3` is **111 MB**, over GitHub's **100 MB
per-file limit**. So the index has to arrive some other way.

### Status: DONE

The index is already packed and published. Set this as `CHROMA_URL` (both as a
Render **build argument** so it bakes into the image, and as an env var for the
entrypoint's fallback path):

```
https://huggingface.co/datasets/sisokh/tunisia-energy-chroma/resolve/main/chroma_db.tar.gz
```

| | |
|---|---|
| Dataset | https://huggingface.co/datasets/sisokh/tunisia-energy-chroma (public) |
| Size | 76.3 MB (compressed from 132 MB) |
| SHA256 | `d604d19df6cd7b5e4c004b7e902ca740b61294570fd4a126f86fbb6430d0cf68` |
| Verified | re-downloaded and hashed against the local artifact — exact match |
| Contents | `chroma_db/chroma.sqlite3` + the HNSW segment dir, rooted at `chroma_db/` |

To republish after rebuilding the index:

```bash
# from the repo root — the archive MUST contain chroma_db/ at its top level
tar -czf deploy/render/.artifacts/chroma_db.tar.gz -C data chroma_db
# then upload it through the dataset's "Files" tab (or the hf CLI)
```

The dataset is public, so the Render build needs no token to download it.
Delete or replace it freely from the HF UI — nothing else depends on it.

**Alternatives**

- *Build on a machine that has the repo checked out* — any build whose context
  contains `data/chroma_db` needs no `CHROMA_URL` at all. That's the local
  `docker build`, or a VPS building from a clone.
- *Rebuild the index on the host* — not possible: the source corpus
  (`data/raw`, `data/filtered`) is gitignored too.

---

## 4. Deploy

### 4.1 Neon (already done — verified)

The database was checked directly: PostgreSQL 18.6 (`eu-central-1`), schema at
`0002_add_sources (head)`, seeded with 12 outage reports, 3 users,
5 conversations and 16 messages. `.env` now holds the pooler URL.

The app must connect with `ssl=require` (not `sslmode`) and the code already
disables asyncpg's prepared-statement cache, which is required by the
`-pooler` host.

### 4.2 Render

**Blueprint route**

1. Copy `render.yaml` to the repo root (Render only reads it from there), or
   create the service manually with the settings it lists.
2. Render Dashboard → **New → Blueprint** → select the repo.
3. Fill in every `sync: false` secret when prompted.

**Manual route**

1. **New → Web Service** → connect the repo.
2. Language **Docker**; Dockerfile path `deploy/render/Dockerfile`;
   Docker context `.` (root).
3. Region **Frankfurt**, branch `main`, plan **Standard**.
4. Health check path `/health`.
5. Add the environment variables from `render.yaml`.

```bash
# generate the two local secrets
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"       # ADMIN_API_KEY
```

### 4.3 Configure `IMAGE`

If you set `CHROMA_URL` as a **build argument** (Render: `dockerBuildArgs`),
the index is baked into the image. If you set it only at **runtime**, the
entrypoint downloads it on first boot instead. Baking it in is faster and is
what the Dockerfile is written for.

---

## 5. Bug this deployment fixes

`entrypoint.sh` (the original, HF-oriented one) runs
`python -m src.database.seed` — and `seed` calls
`src/database/schema.py:ensure_schema()`, which inspects the database through a
**synchronous** SQLAlchemy engine:

```python
engine = create_engine(_sync_url(url))   # needs psycopg2
```

`psycopg2` is **not in `requirements.txt`**. In the container the seed step
therefore raises, and because the call is wrapped in
`|| echo "WARNING: seeding skipped/failed"`, the failure is silent: the service
boots healthy with an **empty outage map and no demo users**. It only worked
locally because the developer's system Python happened to have `psycopg2`.

`requirements.runtime.txt` includes `psycopg2-binary`, so the seed actually
runs. If you deploy with the original `requirements.txt` instead, add it there
too.

---

## 6. Verify

Replace `<service>` with your Render hostname.

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Liveness | `curl -s https://<service>.onrender.com/health` | `200` |
| 2 | Readiness | `curl -s https://<service>.onrender.com/ready` | `200` + `{"checks":{"db":true,"chroma":true}}` |
| 3 | SPA | open `https://<service>.onrender.com/` | React app renders |
| 4 | Chat (SSE) | ask *"Quel est le rôle de l'ANME ?"* | streamed answer + sources |
| 5 | Outage map | open `/map` | 12 seeded reports (from Neon) |
| 6 | Auth | register, then log in | `200`, token stored |
| 7 | Persistence | submit an outage report, reload | report still present |

`/ready` returning `503` means a dependency is down; the body names which
(`db` = Neon, `chroma` = the index).

First request after a cold start is slow — the embedding model loads and the
BM25 index is built. On Render's free plan the service also spins down after
~15 minutes idle, which makes that cold start very visible.

---

## 7. Cost and sizing — read this before deploying

**512 MB will not fit.** The runtime holds CPU torch, the MiniLM embedding
model, ChromaDB and FastAPI in one process; expect roughly **1.0–1.4 GB RSS**
with reranking disabled. Render's:

| Plan | RAM | Price |
|---|---|---|
| Free | 512 MB | $0 (spins down) |
| Starter | 512 MB | $7/mo |
| **Standard** | **2 GB** | **$25/mo** |

So the first Render plan that reliably works is **Standard, $25/month**. For
comparison, Fly.io runs the same container always-on at 2 GB for roughly
**$10/mo**, and a 4 GB Hetzner VPS is about **€4/mo**. If the $25 is a
stumbling block, say so — the image in this folder is host-agnostic and only
the port wiring and `render.yaml` would change.

Further trimming, if you need it later:

- Set `RERANK_ENABLED=false` (already the default here) — saves ~470 MB of
  image and a few hundred MB of RAM.
- Drop the pre-downloaded embedding model and let it fetch on first boot —
  trades ~470 MB of image for a much slower cold start.
- If you do not need the chat/RAG feature at all, removing the `chromadb` +
  `sentence-transformers` + `torch` group would cut the image to a few hundred
  MB and fit the free tier — but that requires editing `src/rag/retrieve.py`,
  which this folder deliberately does not do.
