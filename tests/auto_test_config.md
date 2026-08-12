# Auto Test Configuration

> Test timing baseline + run scenarios for the Tunisia Energy RAG suite.
> Baseline measured: **2026-08-12** (warm embedding model, local machine).
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
| `get_optimized_history` unit tests (×5) | `tests/test_token_manager.py` | **<0.01s each** | 0 | Token-budget truncation logic (pure function). |

**Suite totals:** ~2:00 warm (full run) · ~3:30 first cold run (embedding model load) · ~20s model-load overhead on top of every run.

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
**Covers:** HTTP health/validation, ChromaDB retrieval, prompt formatting, token-budget logic. No tokens spent.
```bash
python -m pytest tests/test_integration.py -k "health or empty or retrieval" tests/test_retrieval.py tests/test_token_manager.py
```
**Tests (9):** `test_api_health_endpoint` · `test_api_chat_rejects_empty_query` · `test_retrieval_to_prompt_chain` · `test_retrieval_speed_and_content` · `test_token_manager.py` (×5)

### 🚀 MEDIUM RUN — adds LLM unit tests, ~15s test time
**When:** after changes to prompts, the LLM client, or retrieval logic — validates LLM behavior (refusal guardrail + grounded answers) without the two slowest HTTP tests.
```bash
python -m pytest tests/test_generation.py tests/test_retrieval.py tests/test_integration.py tests/test_token_manager.py -k "not parallel and not pipeline and not chat_endpoint"
```
**Tests (11):** all 9 fast tests + `test_llm_refusal_on_out_of_context_query` · `test_llm_answers_with_valid_context`

### 🏁 FULL RUN — everything, ~2:00 warm / ~3:30 cold
**When:** before committing / pushing, or after structural changes (imports, client init, Docker). The only run exercising the complete HTTP + thread-pool + LLM pipeline, including the 53s history test.
```bash
python -m pytest
```
**Tests (15):** the full suite (see `testpaths = tests` in `pytest.ini`).

> **Tip:** use `python -m pytest -v` for per-test visibility, and `python -m pytest --durations=12 -q` to refresh this baseline table.
