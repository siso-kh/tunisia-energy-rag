# Auto Test Configuration

> Test timing baseline + run scenarios for the Tunisia Energy RAG suite.
> Baseline measured: **2026-08-15** (warm embedding model, local machine).
> Refresh the numbers any time with:
> ```bash
> python -m pytest --durations=12 -q
> ```

---

## 1. Test Timing Baseline (per-test average time)

| Test | File | Avg time | LLM calls | Notes |
|---|---|---|---|---|
| `test_api_chat_endpoint_with_history` | `tests/test_integration.py` | **53.0s** | 2 | Slowest — query rewrite + generation over HTTP. Most flaky (external API). |
| `test_run_pipeline_end_to_end` | `tests/test_integration.py` | **20.2s** | 2 | Full pipeline with history (thread-pool retrieval + LLM). |
| `test_api_chat_endpoint_full_flow` | `tests/test_integration.py` | **14.1s** | 1 | Full HTTP round-trip. |
| `test_parallel_async_generation` | `tests/test_integration.py` | **6.0s** | 3 (concurrent) | Validates the AsyncOpenAI client concurrency. |
| `test_llm_refusal_on_out_of_context_query` | `tests/test_generation.py` | **4.6s** | 1 | Hallucination guardrail. |
| `test_llm_answers_with_valid_context` | `tests/test_generation.py` | **4.0s** | 1 | Grounded-answer check. |
| `test_retrieval_speed_and_content` | `tests/test_retrieval.py` | **0.05s** | 0 | ChromaDB query; hard <1s assertion. |
| `test_retrieval_to_prompt_chain` | `tests/test_integration.py` | **0.02s** | 0 | Retrieval → prompt formatting. |
| `test_api_health_endpoint` | `tests/test_integration.py` | **0.01s** | 0 | HTTP health contract. |
| `test_api_chat_rejects_empty_query` | `tests/test_integration.py` | **<0.01s** | 0 | 400 validation guard. |
| `get_optimized_history` unit tests (×6) | `tests/test_token_manager.py` | **<0.01s each** | 0 | Token-budget truncation logic (pure function). |
| Database tests (×17) | `tests/test_database.py` | **~0.06s each** | 0 | SQLAlchemy async layer: models, CRUD, cascades, enums, to-one loading, `get_db` (in-memory SQLite). |
| Seed tests (×8) | `tests/test_seed.py` | **~0.9s each** | 0 | DB seeding: populate, idempotency, reset, data quality (SQLite file). |
| API+DB tests (×10) | `tests/test_api_db.py` | **~5s each** | 0 | Outage CRUD, conversations, SSE stream contract (SQLite overrides, stubbed LLM). |

**Suite totals (2026-08-15 re-run):** **~1:04 warm** (full run, 51 tests) · ~3:00 first cold run (embedding model load) · ~20s model-load overhead on top of every run.

---

## 2. Why tracking test times matters

### Short-term (why we run it on every code change)
- **Immediate regression feedback.** The RAG stack is wiring-heavy (ChromaDB ↔ embeddings ↔ async LLM ↔ FastAPI). We already hit this: the async conversion broke sync callers, and a shared-client bug produced flaky failures. A quick run after *any* code change catches these in minutes.
- **Fast vs slow loop.** The **fast run** costs ~10s of test time — cheap enough to run after every edit. The **medium run** (~15s) validates LLM behavior without the two slowest HTTP tests.
- **Cost control.** Every LLM test costs tokens/time on the external API. Knowing each test's cost lets you pick the cheapest run that still covers the change.

### Long-term (why we keep the baseline)
- **Performance trends.** If `test_retrieval_speed_and_content` creeps from 0.05s to seconds, the vector DB or embedding path is degrading. If LLM tests double in time, the API provider or prompt grew.
- **Flakiness tracking.** The LLM tests (especially `with_history` at 53s) depend on an external provider and can fail transiently (`APIConnectionError`). A baseline makes real regressions distinguishable from known flakes.
- **CI budgets.** The full run (~2 min) sets a natural CI limit. If it ever exceeds ~4 min, split or optimize the heavy tests.
- **Optimization targets.** The table makes it obvious where time goes: 4 of 10 tests consume ~97% of the runtime — they are the candidates for mocking the LLM in CI.

---

## 3. Run Scenarios

### ⚡ FAST RUN — no LLM, ~0.1s test time (+~20s model load)
**When:** after every code change — the default "did I break something?" check.
**Covers:** HTTP health/validation, ChromaDB retrieval, prompt formatting, token-budget logic, async DB layer. No tokens spent.
> **Note:** `-k` filters the *whole* pytest session, so the integration file is filtered
> in its own invocation while the remaining files run unfiltered.
```bash
python -m pytest tests/test_integration.py -k "health or empty or retrieval" -q && python -m pytest tests/test_retrieval.py tests/test_token_manager.py tests/test_database.py tests/test_seed.py tests/test_api_db.py -q
```
**Tests (45):** `test_api_health_endpoint` · `test_api_chat_rejects_empty_query` · `test_retrieval_to_prompt_chain` · `test_retrieval_speed_and_content` · `test_token_manager.py` (×6) · `test_database.py` (×17) · `test_seed.py` (×8) · `test_api_db.py` (×10)

### 🚀 MEDIUM RUN — adds LLM unit tests, ~15s test time
**When:** after changes to prompts, the LLM client, or retrieval logic — validates LLM behavior (refusal guardrail + grounded answers) without the two slowest HTTP tests.
```bash
python -m pytest tests/test_generation.py tests/test_retrieval.py tests/test_integration.py tests/test_token_manager.py tests/test_database.py tests/test_seed.py tests/test_api_db.py -k "not parallel and not pipeline and not chat_endpoint"
```
**Tests (47):** all 45 fast tests + `test_llm_refusal_on_out_of_context_query` · `test_llm_answers_with_valid_context`

### 🏁 FULL RUN — everything, ~1:00 warm / ~3:00 cold
**When:** before committing / pushing, or after structural changes (imports, client init, Docker). The only run exercising the complete HTTP + thread-pool + LLM pipeline, including the 53s history test.
```bash
python -m pytest
```
**Tests (51):** the full suite (see `testpaths = tests` in `pytest.ini`).

> **Tip:** use `python -m pytest -v` for per-test visibility, and `python -m pytest --durations=12 -q` to refresh this baseline table.

---

## 4. Frontend test suite (Vitest)

> Baseline measured: **2026-08-15** (warm runs **~8s**; cold runs add ~30-50s transform/env setup).
> Refresh with `cd frontend && npx vitest run --coverage`.

| Test file | Tests | Avg time | Covers |
|---|---|---|---|
| `src/store/chatStore.test.ts` | ×9 | **~10ms each** | Store: `addUserMessage`, `appendToken`, `finalizeAnswer`, stuck-generating guard, `reset` |
| `src/services/chat.test.ts` | ×11 | **~5ms each** | SSE parser: multi/split/CRLF frames, malformed JSON, non-200 errors, abort propagation, payload contract |
| `src/components/chat/ChatComponents.test.tsx` | ×7 | **~130ms each** | RTL: ChatInput (trim/empty/busy) + MessageBubble (markdown, citations, streaming cursor) |
| `src/lib/outage-stats.test.ts` | ×18 | **~2ms each** | Pure helpers for the TunisiaMap wiring: `normalizeRegion` (accents/whitespace), `countOutagesByRegion`, `countOutagesByStatus`, `totalStatusCount`, `statusSegments` (donut arcs: fractions, clockwise offsets, zero-skip), `STATUS_COLORS`, `badgeText` (99+ cap) |
| `src/components/map/TunisiaMap.test.tsx` | ×11 | **~40ms each** | SVG renders; count badges + live "N signalements" labels; accent-insensitive matching (`gabès` → Gabès); 99+ cap; zero-count nodes keep static labels; **click-to-filter** (`onNodeClick`, selection ring, `aria-pressed`, keyboard Enter) |
| `src/components/sidebar/CarteTab.test.tsx` | ×6 | **~100ms each** | Filter interaction: status filter re-queries + narrows TunisiaMap counts and Leaflet markers (ALL/PENDING/RESOLVED); **governorate filter**: click node → narrowed markers, toggle off, ✕ chip clears |

**Commands** (run from `frontend/`):

| Scenario | Command | Notes |
|---|---|---|
| ⚡ Fast | `npm test` | **62 tests**, ~8s warm — run after any frontend change |
| 🔍 With coverage | `npm run test:coverage` | Adds v8 coverage report (chatStore + MessageBubble + outage-stats at ~100% lines) |
| 👀 Watch mode | `npm run test:watch` | Iterative dev loop |

**Why it matters:** the SSE parser (`services/chat.ts`) is the highest-risk frontend code — a frame-splitting bug silently freezes the chat with no error. The store tests lock down the streaming state machine (idle → searching → generating → done/error), and the RTL tests guard the RTL UI against regressions when i18n strings change.

### 🧨 STRESS CHECK — huge payloads, ~50s
**When:** after changes to the token-budget strategy (`token_manager.py`) or the chat history handling. Sends ~36k-token pasted messages through `/api/chat` and asserts the endpoint still returns 200 (no context-window overflow).
```bash
python -m pytest tests/stress_checks.py -v
```
**Tests (2):** `test_huge_message_in_middle_of_history` · `test_huge_newest_message_is_dropped` (excluded from the default suite — filename does not match `test_*.py`).
