# Deployment runbook — ngrok tunnel (backup / $0 path)

Run the app **on your own machine** and expose it publicly through an ngrok
tunnel. This is the fallback for when a cloud host isn't available or isn't
paid for: **no hosting gate, no 512 MB ceiling, no credit card.**

> Nothing in `DEPLOY_HF_SPACE.md` is needed for this. Use whichever runbook
> matches your situation; they are independent.
> Never put real credentials in this file — it is tracked by git. Secrets live
> in your local `.env` (gitignored) and in your ngrok dashboard.

---

## 1. Why this works when the cloud options don't

Every free cloud container tier this app could use is either RAM-limited or
gated behind a paid plan (see the table in `DEPLOY_HF_SPACE.md` §4.2). A tunnel
inverts that: **the compute is your machine**, which has the RAM. The measured
peak of the app is ~940 MB (see the Memory section in `README.md`) — comfortable
on any dev laptop, and impossible on the free tiers above.

The tunnel service only forwards bytes, so its own free-tier limits apply to
*bandwidth*, not memory.

```
   https://<subdomain>.ngrok-free.app        (public, TLS terminated by ngrok)
                        │
              ┌─────────┴──────────┐
              │  ngrok agent       │  (container, docker-compose service)
              └─────────┬──────────┘
                        │  http://frontend:80
              ┌─────────┴──────────┐
              │  nginx (frontend)  │  serves the React SPA
              │   └── /api → backend:8000   (uvicorn/FastAPI, SSE unbuffered)
              └─────────┬──────────┘
                        │
        ┌───────────────┴────────────────┐
        │ Postgres (local container)      │
        │ ChromaDB data/chroma_db (host)  │
        └─────────────────────────────────┘
```

Good news: **the tunnel is already wired up.** `docker-compose.yml` has an
`ngrok` service pointing at `frontend:80`, and the SSE-critical
`proxy_buffering off` is already set in `frontend/nginx.conf`. There is nothing
to build.

---

## 2. Requirements

- [ ] Docker + Docker Compose running locally.
- [ ] A free ngrok account and its auth token (below).
- [ ] ~1.5 GB free RAM while the stack runs.
- [ ] The app's `.env` filled in (`cp .env.example .env`) — `CUSTOM_API_KEY`,
      `JWT_SECRET` and `ADMIN_API_KEY` are required.
- [ ] ~1.5 GB of the ngrok free monthly bandwidth budget (§7).

---

## 3. Get an ngrok token (free)

1. Sign up at <https://dashboard.ngrok.com/sign-up>.
2. **Get a token** — dashboard → *Authtokens* → *Add authtoken*.
3. Put it in `.env` (this file is gitignored; uncomment the existing entry):

   ```
   NGROK_AUTHTOKEN=<paste your token>
   ```

The free plan includes HTTPS with an automatic TLS certificate, so the public
URL is HTTPS with no extra setup.

---

## 4. Start the stack

```bash
cp .env.example .env      # fill in NGROK_AUTHTOKEN + the required keys
docker compose up -d --build
```

That starts Postgres, the backend, the frontend, and the tunnel together. The
first build takes a while (React build + Python image). Watch it come up:

```bash
docker compose logs -f backend      # first import loads the embedding model
docker compose ps
```

Then read the public URL out of the ngrok log:

```bash
docker compose logs ngrok | grep -o 'https://[a-z0-9-]*\.ngrok-free\.app'
```

- **ngrok's own dashboard:** <http://localhost:4040>
- **Local, without the tunnel:** <http://localhost>

To run only the tunnel and the app (skip Prometheus and Grafana):

```bash
docker compose up -d postgres db-seed backend frontend ngrok
```

---

## 5. Verify

Run the same smoke test used for the HF deployment. It checks liveness,
readiness, a Postgres read, and a **complete** chat stream, and exits non-zero
on failure:

```bash
python scripts/verify_deployment.py https://<subdomain>.ngrok-free.app --retries 20
```

There is no cold start here — your machine is already warm — so
`--retries 5` is normally enough. Use a large `--retries` only while the stack
is still building.

Then confirm by hand:

| # | Check | Expected |
|---|---|---|
| 1 | Liveness | `curl -s https://<subdomain>.ngrok-free.app/health` → `200` |
| 2 | Readiness | `.../ready` → `200`, `{"checks":{"db":true,"chroma":true}}` |
| 3 | SPA loads | open the ngrok URL in a browser → React app renders |
| 4 | Chat (SSE) | ask "Quel est le rôle de l'ANME ?" → streamed answer + sources |
| 5 | Outage map | open `/map` | seeded reports appear |
| 6 | Auth | register a user, then log in | `200`, token stored, user chip shown |
| 7 | Admin | open `/admin`, enter `ADMIN_API_KEY` | purge stats load |

If the browser shows an ngrok warning page, that is the free-tier interstitial
(§7) — click *Visit* once and it remembers the domain for 7 days.

---

## 6. Day-to-day operation

```bash
docker compose ps                     # status of every service
docker compose logs -f ngrok          # tunnel URL + connection errors
docker compose logs -f backend        # app errors, retrieval timings
docker compose restart backend         # restart just the API
docker compose up -d --build          # rebuild after a code change
docker compose down                   # stop (keeps the DB volume)
```

The public URL is **stable** for your account: the free plan includes one
assigned development domain, so it does not change on restart. Anything that
changes the *port* the stack listens on will change the URL.

To take the app offline without stopping anything, stop just the tunnel:

```bash
docker compose stop ngrok
```

---

## 7. Free-tier limits and what they mean in practice

The ngrok free plan, as documented:

| Resource | Free limit | Practical meaning here |
|---|---|---|
| Data transfer out | **1 GB / month** | The binding constraint. A chat response is tens of KB, so a demo or a small class is fine; heavy or public use will exhaust it. |
| HTTP requests | 20,000 / month | Each chat turn is a handful of requests (SSE stream + status polls). Thousands of conversations are within budget. |
| Online endpoints | 3 | One is enough. |
| Development domains | 1 | Your URL is fixed for the account. |
| Concurrent agents | 3 | One agent. |
| Endpoint session timeout | **none** | Endpoints stay online indefinitely — run the agent as a background service, not an interactive terminal. |

Additional caveats:

- **The interstitial page.** Free accounts see a "site served by ngrok"
  warning in front of HTML browser traffic. It does not affect API access. To
  remove it, send `ngrok-skip-browser-warning: 1` or use a non-standard
  `User-Agent`; otherwise click *Visit* once (7-day cookie).
- **Your machine must be on and awake.** The tunnel dies if the laptop sleeps,
  closes the lid, or loses network. There is no cold-start fallback — this is
  a demo path, not a production one.
- **Bandwidth resets monthly**; check the *Usage* page in the dashboard.
- Only outbound traffic is metered; responses going to your machine are the
  billed direction.

---

## 8. Security notes

An ngrok URL makes a **development stack public**. Before sharing it:

- [ ] Keep `CORS_ORIGINS=*` only for local dev. Set it to the real ngrok origin
      if you want a locked-down CORS policy (the SPA itself is same-origin
      through nginx, so `*` is not required for normal use).
- [ ] Keep `TRUST_PROXY_HEADERS=false`. ngrok forwards the real client IP;
      trusting `X-Forwarded-For` blindly would let callers spoof it and defeat
      the rate limiter.
- [ ] Change `JWT_SECRET` / `ADMIN_API_KEY` if the defaults from
      `.env.example` are still in place — they are placeholders, and
      `/api/admin/*` is protected by that key.
- [ ] Remember `.env` holds a real `CUSTOM_API_KEY`; never commit it.
- [ ] Consider a password on the Space/tunnel if you need a private demo —
      ngrok free does not include access control, so treat the URL as public.
- [ ] Stop the tunnel when you are done: `docker compose stop ngrok`.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| URL returns 502 | The frontend container isn't up yet, or nginx can't reach `backend`. Check `docker compose logs frontend backend`. |
| Chat streams nothing, then errors | SSE is being buffered somewhere in the chain. `frontend/nginx.conf` already sets `proxy_buffering off` and `X-Accel-Buffering no`; make sure that config is the one in use. |
| `502` from ngrok only, local works | Backend still importing the embedding model (30–60 s cold start). Wait for `/health`, then retry. |
| `ERR_NGROK_401` | `NGROK_AUTHTOKEN` missing or wrong. Restart: `docker compose up -d --force-recreate ngrok`. |
| `ERR_NGROK_1058` / agent not connected | Port `4040` is taken by another ngrok agent. `docker compose stop ngrok` on the other project. |
| URL changed unexpectedly | A different tunnel took the dev domain, or you're on a random URL. `docker compose logs ngrok` shows the bound URL. |
| Bandwidth exhausted | Free 1 GB/month spent. Check the *Usage* page; waits for the next cycle. |
| Connection dies after a while | Laptop sleep / network change. `docker compose restart ngrok`. |
| `failed to register listener` | Another agent holds the free dev domain (only one per account). Stop the other agent first. |

---

## 10. When to use this instead of a real host

Use the tunnel for demos, course submissions, a quick share with reviewers, or
any time hosting is unavailable. Do **not** rely on it for production: it needs
your machine powered on, it is capped at 1 GB/month, and it exposes a
development stack to the internet.

If you later want a hosted deployment, `DEPLOY_HF_SPACE.md` covers the Hugging
Face Docker Space route — everything in this repository (the ONNX embedder,
`scripts/verify_deployment.py`, the nginx configs) is reusable as-is, since the
tunnel and the Space share the same proxy chain.