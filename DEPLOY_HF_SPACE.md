# Deployment runbook — Hugging Face Docker Space

Single-container deployment of the Tunisia Energy RAG app on a **Hugging Face
Space (Docker SDK)**, with **Neon** as the Postgres database.

> Never put real credentials in this file — it is tracked by git. Secrets live
> only in the Space's **Settings → Variables and secrets** (and your local
> `.env`, which is gitignored).

---

## 1. Architecture

```
                 https://<user>-<space>.hf.space   (port 7860, public)
                                   │
                    ┌──────────────┴──────────────┐
                    │      one container (UID 1000) │
                    │                               │
                    │  nginx :7860                  │
                    │   ├── /            → React SPA (frontend/dist)
                    │   └── /api,/health,/ready → uvicorn 127.0.0.1:8000
                    │                               │
                    │  uvicorn  src.api.main:app    │
                    │   ├── ChromaDB  data/chroma_db (bundled in image)
                    │   └── Postgres  Neon (DATABASE_URL secret)
                    └───────────────────────────────┘
```

Key files (added for this deployment):

| File | Role |
|---|---|
| `Dockerfile` | Stage 1 builds the React app; stage 2 runs nginx + uvicorn as UID 1000, exposes 7860, pre-downloads models. |
| `entrypoint.sh` | `alembic upgrade head` → `python -m src.database.seed` → nginx + uvicorn. |
| `nginx.hf.conf` | Full nginx config: listens on 7860, proxies `/api` to uvicorn, writes only to `/tmp`. |
| `.dockerignore` | Drops the corpus; **keeps `data/chroma_db`**. |
| `README.md` | Space YAML frontmatter (`sdk: docker`, `app_port: 7860`). |

Compose (`docker-compose.yml`, `Dockerfile.backend`, `frontend/Dockerfile`)
remains for local development and is unchanged by this deployment.

---

## 2. Prerequisites

- [ ] Hugging Face account that is **allowed to create a Docker Space**. This is
      the single hardest requirement in this runbook — see §4.1. Verify it
      *before* writing any code changes; if the account is not eligible, the
      free tier offers no viable host at all (§4.2).
- [ ] A Neon project (free tier, no credit card) with the schema migrated and
      seeded (see §3).
- [ ] `git` and `git-lfs` installed locally.
- [ ] The app's own git repo (`tunisia-energy-rag/` is a standalone repo).

---

## 3. Neon database (one-time)

1. neon.com → create a project (region closest to your users; for Tunisia pick
   **AWS eu-central-1 / Frankfurt**).
2. Dashboard → **Connect** → choose **Direct connection** (or the `-pooler`
   host — see the note in §8) and copy the string, which looks like:

   ```
   postgresql://<role>:<password>@ep-xxxx.<region>.aws.neon.tech/neondb?sslmode=require
   ```

3. Convert it for the app (asyncpg + SQLAlchemy):

   | From | To |
   |---|---|
   | `postgresql://` | `postgresql+asyncpg://` |
   | `sslmode=require` | **`ssl=require`** |
   | drop `channel_binding` | (asyncpg does not accept it) |

   Result (this exact form goes in the `DATABASE_URL` secret):

   ```
   postgresql+asyncpg://<role>:<password>@ep-xxxx.<region>.aws.neon.tech/neondb?ssl=require
   ```

   URL-encode any special characters in the password (`@`→`%40`, `:`→`%3A`).

4. Apply the schema locally (or let the Space entrypoint do it on first boot):

   ```bash
   cd tunisia-energy-rag
   DATABASE_URL='postgresql+asyncpg://...?ssl=require' python -m alembic upgrade head
   DATABASE_URL='postgresql+asyncpg://...?ssl=require' python -m src.database.seed   # optional demo data
   ```

   Expected final revision: `0002_add_sources (head)`.

---

## 4. Create the Space

1. Go to **huggingface.co/new-space**.
2. Name it (e.g. `tunisia-energy-rag`), set **SDK = Docker**,
   **Hardware = CPU basic (free)**, visibility **Public**.
3. The Space repo starts with a `README.md`; your push (§6) will overwrite it
   with the one that already contains the correct frontmatter.

Equivalent CLI (from <https://huggingface.co/new-space/agents.md>):

```bash
hf auth login                                   # prints a URL + one-time code
hf repos create <user>/tunisia-energy-rag \
  --type space --space-sdk docker --public
```

The CLI is free and the docs it points at are worth reading, but note that
HF's own Spaces Overview currently states: *"Gradio and Docker Spaces run on
compute and **require a paid plan to create**"* — see §4.1. Use whichever route
you prefer; they create the same repo and the rest of this runbook is unchanged.

> **Why HF and not Render.** CPU basic hardware itself is free and ships
> **2 vCPU / 16 GB RAM**, far above this app's measured peak of ~940 MB (see the
> Memory section in `README.md`). Render's free *and* starter tiers are both
> 512 MB and cannot hold it: the container was OOM-killed at boot while loading
> the embedding model, and every request then answered 502 with no error frame.
> Trimming the in-memory BM25 corpus would still leave ~746 MB — over the wall.
>
> Two things to expect from the free tier **if you are eligible**: a Space
> **sleeps after 48 hours without visitors**, so the first request afterwards
> pays a cold start (the image is cached, so this is migrations plus the
> retrieval warm-up — tens of seconds, not a rebuild), and **disk is not
> persistent**, which is why both the Chroma index and the embedding model are
> baked into the image.

### 4.1 Verify the account may create a Docker Space (do this first)

CPU Basic is free *hardware*; creating the Space that would run on it is
**gated**. As of the docs' current wording, Gradio and Docker Spaces require
PRO / Team / Enterprise. Community measurement on a free account: Docker
refused, Gradio refused, Static created fine; new accounts report a free
CPU-basic quota of **0**. Some long-standing accounts still hold a
grandfathered allowance.

Check, cheapest first:

```bash
hf auth whoami                     # inspect the `canPay` / `isPro` flags
```

```bash
# definitive: the API reports the account's actual quota
curl -s https://huggingface.co/api/whoami-v2 \
  -H "Authorization: Bearer $HF_TOKEN"     # write-scoped token from /settings/tokens
```

`isPro: false` means a new Docker Space will very likely be refused. The only
authoritative test is an actual attempt — the create call either succeeds or
returns *"hosting Gradio and Docker Spaces on free cpu-basic requires a PRO
subscription."* The browser route makes this obvious: open `new-space` and see
whether **SDK = Docker** is selectable at all.

If you already own one qualifying Space, **do not delete and recreate it** —
creation is what's gated, so a rebuild from an existing Space keeps working.
Update with `git push`, not a fresh create.

### 4.2 If the account is not eligible

There is no free container host with enough RAM for this app:

| Host | Free tier | Fits ~940 MB? |
|---|---|---|
| HF Spaces CPU Basic | 16 GB, but creation gated | only if grandfathered |
| Render free / starter | 512 MB | no |
| Koyeb / Northflank free | 512 MB | no |
| Google Cloud Run free | 450k GiB-s/mo (~62 h at 2 GiB) | yes, **requires a credit card** |
| Oracle Always Free A1 | 12 GB (halved from 24 GB in June 2026) | yes, **requires a card** + capacity lottery |
| Google e2-micro (Always Free) | 1 GB | no |

Cloud Run and Oracle are the two options that fit, and both require a payment
method on file even at $0 spend. Without one of those, or an eligible HF
account, this app has no $0 host — the ~940 MB floor is set by the XLM-R
tokenizer (~250 MB), the ONNX embedder (~280 MB) and the Chroma corpus
(~195 MB), none of which can be shed without changing retrieval quality.

---

## 5. Secrets

Space → **Settings → Variables and secrets → New secret**. These are injected as
environment variables at **runtime**.

> `DATABASE_URL` must be the **asyncpg** form (`postgresql+asyncpg://...?ssl=require`).
> The entrypoint's `ensure_schema()` converts it for the sync inspection engine,
> so do not paste the `postgresql://...?sslmode=require` string from the Neon
> dashboard.

Generate the two local keys first:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"       # ADMIN_API_KEY
```

| Secret | Required | Value / notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://<role>:<password>@...neon.tech/neondb?ssl=require` |
| `CUSTOM_API_KEY` | yes | your Mistral/OpenAI-compatible key (chat + triage) |
| `OPENAI_BASE_URL` | recommended | provider base URL. The verified setup uses `https://router.bynara.id/v1` |
| `JWT_SECRET` | yes | generated above; without it, auth endpoints return 503 |
| `ADMIN_API_KEY` | yes | generated above; protects `/api/admin/*` |
| `CORS_ORIGINS` | recommended | `https://<user>-<space>.hf.space` |
| `RERANK_ENABLED` | optional | `false` (the default) skips the ~470 MB cross-encoder |
| `LLM_MODEL` / `LLM_MODELS` | recommended | the only models verified against this provider are `agnes-2.5-flash`, `laguna-s-2.1` and `combo/freemodels`. `deepseek-v4-flash` / `qwen3.8-27b` / `stepfun-3.7-flash` return 429 (per-model quota) and `glm-5.3-flash-free` / `nemotron-3-ultra` return 404, so leave them out or failover stalls on them |
| `SSE_HEARTBEAT_SECONDS` | optional | default `10`; keep-alive comments on the chat stream |
| `OUTAGE_TTL_HOURS` | optional | default `5` |
| `RATE_LIMIT_STORAGE_URI` | optional | default `memory://` (fine for one instance) |

Notes:
- Do **not** set `TRUST_PROXY_HEADERS=true` — nginx already forwards the real IP
  and spoofing protection should stay on.
- `CORS_ORIGINS` matters only for direct API calls; the SPA is same-origin via
  nginx.

---

## 6. Push and deploy

`data/chroma_db` is gitignored, so it must be force-added; HF stores the large
sqlite via LFS/Xet.

```bash
cd tunisia-energy-rag
git lfs install

git add -A
git add -f data/chroma_db
git commit -m "Add Hugging Face Space deployment (single-container nginx + uvicorn)"

git remote add space https://huggingface.co/spaces/<user>/<space>
git push space main
```

If the push is rejected for a large file, HF prints the exact remediation
command (usually `git lfs track "*.sqlite3"` then re-add) — follow its prompt.

The Space rebuilds automatically on every push. Watch progress in the
**Logs** tab.

---

## 7. Post-deploy verification

Run the automated smoke test first — it checks liveness, readiness, a Postgres
read and a **complete** chat stream, and exits non-zero on failure:

```bash
python scripts/verify_deployment.py https://<user>-<space>.hf.space --retries 20
```

`--retries 20` is worth using for a Space: after a push the container is still
building, and a free Space additionally sleeps after 48 h idle, so the first
request pays a cold start before it answers.

Then check the following by hand. Replace `<user>-<space>` below.

| # | Check | Command / action | Expected |
|---|---|---|---|
| 1 | Liveness | `curl -s https://<user>-<space>.hf.space/health` | `200` |
| 2 | Readiness | `curl -s https://<user>-<space>.hf.space/ready` | `200` + `{"checks":{"db":true,"chroma":true}}` |
| 3 | SPA loads | open `https://<user>-<space>.hf.space/` | React app renders |
| 4 | Chat (SSE) | ask "Quel est le rôle de l'ANME ?" | streamed answer + sources |
| 5 | Outage map | open `/map` | 12 seeded reports (from Neon) |
| 6 | Auth | register a user, then log in | `200`, token stored, user chip shown |
| 7 | Admin | open `/admin`, enter `ADMIN_API_KEY` | purge stats load |
| 8 | DB writes persist | submit an outage report, wait, reload | report still present |

The **first** request after a cold start is slow (embedding model + BM25 index
build); subsequent requests are fast.

If `/ready` returns `503`, the body lists which dependency failed
(`db` = Neon, `chroma` = the bundled index).

---

## 8. Operational constraints & gotchas

| Topic | Detail |
|---|---|
| Idle sleep | Free Spaces sleep after ~48 h without traffic; next request cold-starts (~1–2 min). |
| Non-persistent disk | Anything written to disk (admin-uploaded PDFs, ChromaDB changes) is lost on restart/rebuild. Conversations and outages are safe (Neon). |
| Port | Must be **7860** (`app_port` in `README.md`, nginx `listen`, `EXPOSE`). |
| User | Container runs as **UID 1000**; nginx writes pid/temp under `/tmp`, models under `$HOME`. |
| Image size | torch + easyocr + pre-downloaded models → multi-GB image; the first build is slow. |
| Neon pooler | The `-pooler` host (pgbouncer, transaction mode) needs asyncpg's prepared-statement cache off — already set via `statement_cache_size=0` in `src/database/connection.py`. If you see `prepared statement ... already exists`, use the **direct** host instead. |
| Neon suspend | Free compute suspends after ~5 min idle; `pool_pre_ping=True` handles reconnects. |
| `ssl` vs `sslmode` | asyncpg accepts only `ssl=require`; `sslmode` / `channel_binding` raise `TypeError`. |
| Chroma must ship | `src/rag/retrieve.py` opens the collection **at import**; if `data/chroma_db` is missing the backend crashes on boot. Never exclude it in `.dockerignore`. |
| Fresh-DB migrations | `0002_add_sources_table.py` no longer creates the `sourcestatus` enum twice (fixed). If you see `type "sourcestatus" already exists`, the fix was reverted. |

---

## 9. Redeploy / update

```bash
cd tunisia-energy-rag
git add -A && git commit -m "..."      # or make changes
git push space main
```
Migrations run automatically on container start. To change secrets, edit them in
Settings — the Space restarts and picks them up.

**Manual controls:** Space → *Settings → Factory reboot* to rebuild from
scratch, or the ⋮ menu → *Restart* for a clean process.

---

## 10. Security cleanup

- [ ] The Neon password was shared during setup → **Reset it** (Neon → Project →
      Roles → Reset password) and update the `DATABASE_URL` secret + local `.env`.
- [ ] Rotate `CUSTOM_API_KEY` if it was ever committed or pasted publicly.
- [ ] Keep `JWT_SECRET` / `ADMIN_API_KEY` only in Space secrets; never in git.
- [ ] Confirm `CORS_ORIGINS` is your real Space URL (not `*`) in production.
- [ ] Consider `SECURE_HSTS=true` and a tuned `SECURITY_CSP` once custom domains
      are in place.
