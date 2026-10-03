import asyncio
import logging
import os
import re
import sys
import time
import chromadb
from chromadb.utils import embedding_functions
from openai import AsyncOpenAI
from dotenv import load_dotenv
from fastapi.concurrency import run_in_threadpool
from typing import Any, Dict, List, Optional, Tuple

from src.rag.hybrid import retrieve_context_hybrid
from src.rag.guardrails import (
    check_query, validate_output, get_safe_response,
    classify_domain, filter_domain_violations,
    assess_confidence, verify_claims, mask_sensitive_content,
)
from src.utils.token_manager import get_optimized_history

logger = logging.getLogger(__name__)

# Lazy import to avoid circular imports at module load time
_llm_tokens = None
_llm_requests = None


def _get_llm_metrics():
    """Lazy-load Prometheus counters to avoid import-time side effects."""
    global _llm_tokens, _llm_requests
    if _llm_tokens is None:
        from src.api.metrics import LLM_TOKENS as _t, LLM_REQUESTS as _r
        _llm_tokens = _t
        _llm_requests = _r
    return _llm_tokens, _llm_requests

# Windows consoles default to cp1252 and raise UnicodeEncodeError when the
# pipeline logs Arabic standalone queries. Force UTF-8 with lossy fallback so
# console output can never crash the request handling.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Token budget reserved for chat history when building LLM prompts.
# Keeps the conversation bounded so the system prompt + retrieved RAG
# context always fit in the model's context window.
HISTORY_TOKEN_BUDGET = 1500

# ==========================================
# 1. Configuration & Initialization
# ==========================================

load_dotenv()
API_KEY = os.getenv("CUSTOM_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")

CHROMA_PATH = "data/chroma_db"

# The vectors in this collection are un-normalised (measured L2 norms
# 1.26-6.61) and Chroma ranks by squared L2, which is magnitude-sensitive. So
# the query must be embedded by the same convention that wrote the index --
# chromadb's SentenceTransformerEmbeddingFunction, via hybrid._dense_query's
# query_texts branch. That is what ONNX_EMBEDDER_ENABLED=false restores;
# querying this collection with the ONNX embedder's unit-length vectors ranks
# by vector magnitude instead of meaning.
#
# Kept overridable so a normalised cosine collection (see
# scripts/rebuild_index_onnx.py) can be selected without a code change.
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "tunisia_energy_rag")

# ---------------------------------------------------------------------------
# Model Fallback Pool — round-robin with automatic dead-model detection
# ---------------------------------------------------------------------------
# When a model times out or returns an error it is marked "dead" for
# DEAD_TTL seconds.  On the next LLM call the pool skips dead models and
# tries the next one.  After DEAD_TTL the model is retried once.
#
# The primary model is configurable via LLM_MODEL env var (or the first
# entry in FALLBACK_MODELS).  The fallback list is ordered by tested
# speed on router.bynara.id.
import time as _time
from openai import APITimeoutError, APIStatusError, APIError

# Ordered by measured reliability against the live provider (6 streaming calls
# each, same payload the pipeline sends):
#   agnes-2.5-flash  6/6     laguna-s-2.1   6/6     combo/freemodels 5/6
#   deepseek-v4-flash, qwen3.8-27b, stepfun-3.7-flash -> 429 per-model quota
#   glm-5.3-flash-free, nemotron-3-ultra             -> 404 not served
#
# The five unusable entries were previously first in the list, so most requests
# spent their time walking dead models and then failing over. They are gone;
# override with LLM_MODELS if your provider changes.
_DEFAULT_MODELS = [
    "agnes-2.5-flash",
    "laguna-s-2.1",
    "combo/freemodels",
]

# Allow override via env: comma-separated list.
_env_models = os.getenv("LLM_MODELS", "")
_primary = os.getenv("LLM_MODEL", "")
FALLBACK_MODELS: List[str] = (
    [m.strip() for m in _env_models.split(",") if m.strip()]
    if _env_models
    else ([_primary] + [m for m in _DEFAULT_MODELS if m != _primary] if _primary else _DEFAULT_MODELS)
)

DEAD_TTL: float = float(os.getenv("LLM_DEAD_TTL", "120"))  # seconds before retrying a dead model
# Quota/rate-limit failures are not fixed by waiting two minutes, so they get a
# much longer cooldown than ordinary transient errors.
QUOTA_TTL: float = float(os.getenv("LLM_QUOTA_TTL", "900"))

# Some router endpoints reject a fraction of otherwise valid requests (measured
# at ~17% for combo/freemodels, with no HTTP status attached). Retrying the
# same model once converts those hard failures into successes; persistent
# errors are still quarantined and failed over as before.
SAME_MODEL_RETRIES: int = int(os.getenv("LLM_SAME_MODEL_RETRIES", "1"))

# Per-model timeouts (seconds). LLM_CALL_TIMEOUT bounds non-streaming calls
# (e.g. query rewriting); LLM_STREAM_TIMEOUT bounds the time-to-first-token
# for streaming calls, so it can stay tight while long answers still stream.
# Tunable via env so a slow provider can be accommodated without a rebuild.
LLM_CALL_TIMEOUT: float = float(os.getenv("LLM_CALL_TIMEOUT", "20"))
LLM_STREAM_TIMEOUT: float = float(os.getenv("LLM_STREAM_TIMEOUT", "60"))


class ModelFallbackPool:
    """Thread-safe (asyncio-safe) model pool with automatic failover.

    Usage::

        pool = ModelFallbackPool(API_KEY, BASE_URL, FALLBACK_MODELS)
        resp = await pool.chat(messages=[...])
        async for chunk in pool.chat_stream(messages=[...]):
            ...
    """

    def __init__(self, api_key: str, base_url: str, models: List[str]):
        self._api_key = api_key
        self._base_url = base_url
        self._models = list(models)
        self._client: Optional[AsyncOpenAI] = None
        self._dead: Dict[str, float] = {}  # model -> timestamp when it died
        self._disabled: set = set()  # models the provider does not serve at all
        self._current_idx = 0  # round-robin pointer

    # -- client management ---------------------------------------------------

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None or self._client.is_closed:
            # 60s timeout: without this the client can hang indefinitely on a slow
            # or unresponsive provider, which is what caused the 5-minute
            # streaming deadlock observed in production.
            #
            # max_retries=1: the SDK otherwise retries internally with
            # exponential backoff. Multiplied across a failover pool of dead
            # models, those hidden retries dominated the end-to-end latency.
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=60,
                max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
            )
        return self._client

    # -- model selection ------------------------------------------------------

    def _is_alive(self, model: str) -> bool:
        if model in self._disabled:
            return False
        died_at = self._dead.get(model)
        if died_at is None:
            return True
        if _time.time() - died_at > DEAD_TTL:
            # Cooldown expired -> allow one retry
            del self._dead[model]
            return True
        return False

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """Is this failure worth retrying on the *same* model?

        True for timeouts, 5xx, and status-less ``APIError`` rejections from a
        router that intermittently refuses otherwise valid requests. False for
        anything that will not change on a second identical attempt (401/402/
        404/429), which must go straight to failover/quarantine.
        """
        if isinstance(exc, (APITimeoutError, asyncio.TimeoutError, ConnectionError)):
            return True
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None:
            return status >= 500 or status == 408
        # No status: a generic APIError such as the router's
        # "The model rejected this request."
        return isinstance(exc, APIError)

    def _quarantine(self, model: str, exc: BaseException) -> None:
        """React to a failure according to its cause.

        A single flat cooldown treated every error as equally transient, so a
        model the provider does not serve at all (HTTP 404) was retried every
        120 s forever, and a route with an exhausted per-model quota (402/429)
        came back after two minutes and failed again. Measured against the live
        provider, six of the eight configured models were in one of those two
        states, so most requests spent their time walking dead entries.
        """
        status = getattr(getattr(exc, "response", None), "status_code", None)
        text = f"{type(exc).__name__} {exc}".lower()

        if status == 404 or "model does not exist" in text or "not_found" in text:
            self._disabled.add(model)
            self._dead.pop(model, None)
            logger.warning("Model %s disabled: provider does not serve it.", model)
            return

        if status in (402, 429) or "payment required" in text or (
            "rate_limit" in text or "insufficient" in text or "quota" in text
        ):
            self._dead[model] = _time.time()
            logger.warning(
                "Model %s quota/rate limited; pausing for %.0fs",
                model, QUOTA_TTL,
            )
            return

        self._dead[model] = _time.time()
        logger.warning("Model %s marked dead for %.0fs", model, DEAD_TTL)

    def _mark_dead(self, model: str) -> None:
        self._dead[model] = _time.time()
        logger.warning("Model %s marked dead for %.0fs", model, DEAD_TTL)

    def _pick_model(self, preferred: Optional[str] = None) -> str:
        """Return the best available model, preferring *preferred* if alive."""
        usable = [m for m in self._models if m not in self._disabled]
        if not usable:
            # Every configured model was reported missing. Allow a full retry
            # rather than serving nothing, and surface it loudly.
            logger.warning("All models disabled by 404; resetting the pool.")
            self._disabled.clear()
            self._dead.clear()
            usable = list(self._models)

        if preferred and preferred in usable and self._is_alive(preferred):
            return preferred

        for _ in range(len(usable)):
            candidate = usable[self._current_idx % len(usable)]
            self._current_idx = (self._current_idx + 1) % len(usable)
            if self._is_alive(candidate):
                return candidate

        # Every remaining model is cooling down. Forget the cooldowns and use
        # the first one rather than failing the request outright.
        self._dead.clear()
        return usable[0]

    # -- public API -----------------------------------------------------------

    async def _call_with_hard_timeout(self, coro, timeout: float):
        """Run *coro* with a hard asyncio task timeout.

        httpx's read-timeout does not cancel connections that the server
        keeps open (the socket stays alive but idle). ``asyncio.wait_for``
        raises ``TimeoutError`` and lets us move on to the next model.
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            raise APITimeoutError(request=None)  # type: ignore[call-arg]

    async def chat(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        timeout: float = LLM_CALL_TIMEOUT,
        **kwargs,
    ) -> Tuple[Any, str]:
        """Non-streaming chat with automatic failover.

        Returns (response, model_used).
        """
        last_exc: Optional[Exception] = None
        tried: set = set()
        for _ in range(len(self._models)):
            model = self._pick_model(preferred)
            if model in tried:
                break  # avoid infinite loop
            tried.add(model)
            for attempt in range(SAME_MODEL_RETRIES + 1):
                try:
                    coro = self._get_client().chat.completions.create(
                        model=model, messages=messages,
                        timeout=timeout,
                        **kwargs,
                    )
                    resp = await self._call_with_hard_timeout(coro, timeout)
                    return resp, model
                except (APITimeoutError, APIStatusError, Exception) as exc:
                    last_exc = exc
                    if attempt < SAME_MODEL_RETRIES and self._is_retryable(exc):
                        logger.warning(
                            "Model %s transient failure (attempt %d/%d): %s — retrying",
                            model, attempt + 1, SAME_MODEL_RETRIES + 1, exc,
                        )
                        continue
                    self._quarantine(model, exc)
                    logger.warning("Model %s failed: %s — trying next", model, exc)
                    break
        # Every model failed
        raise last_exc  # type: ignore[misc]

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        timeout: float = LLM_STREAM_TIMEOUT,
        **kwargs,
    ) -> Tuple[Any, str]:
        """Streaming chat with automatic failover.

        Returns (stream, model_used).  Caller must iterate the stream.

        ``timeout`` is the time-to-first-token budget: the hard wrapper only
        covers establishing the stream, so a long answer is not cut off once
        tokens start arriving.
        """
        last_exc: Optional[Exception] = None
        tried: set = set()
        for _ in range(len(self._models)):
            model = self._pick_model(preferred)
            if model in tried:
                break
            tried.add(model)
            for attempt in range(SAME_MODEL_RETRIES + 1):
                try:
                    coro = self._get_client().chat.completions.create(
                        model=model, messages=messages, stream=True,
                        timeout=timeout,
                        **kwargs,
                    )
                    stream = await self._call_with_hard_timeout(coro, timeout)
                    return stream, model
                except (APITimeoutError, APIStatusError, Exception) as exc:
                    last_exc = exc
                    if attempt < SAME_MODEL_RETRIES and self._is_retryable(exc):
                        logger.warning(
                            "Model %s transient failure (attempt %d/%d): %s — retrying",
                            model, attempt + 1, SAME_MODEL_RETRIES + 1, exc,
                        )
                        continue
                    self._quarantine(model, exc)
                    logger.warning("Model %s failed (stream): %s — trying next", model, exc)
                    break
        raise last_exc  # type: ignore[misc]

    async def chat_stream_failover(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        timeout: float = LLM_STREAM_TIMEOUT,
        **kwargs,
    ):
        """Async-iterate a chat stream, failing over on a pre-token rejection.

        ``chat_stream`` can only fail over while *opening* the stream. The
        provider routinely accepts the request and then rejects it once
        iteration begins -- openai surfaces that as ``APIError: The model
        rejected this request`` from inside ``_streaming.__stream__``, long
        after ``chat_stream`` returned. That left the failover loop finished and
        the error propagating straight out of the SSE endpoint, so a single
        provider hiccup killed the answer while two healthy models sat unused in
        the pool. Measured live: the identical payload succeeded on 15/15 direct
        probes, so the rejection is transient and a retry is the right answer.

        Failover is only safe while nothing has been emitted. Once a content
        token reaches the client the answer is already partially written and
        re-running the model would duplicate text, so a later failure is raised
        instead. Usage-only chunks do not count as emitted: the consumer skips
        them, so nothing has reached the client yet.

        Yields ``(chunk, model_used)`` so per-model metrics stay attributable.
        """
        last_exc: Optional[Exception] = None
        tried: set = set()

        for _ in range(len(self._models)):
            model = self._pick_model(preferred)
            if model in tried:
                break
            tried.add(model)

            for attempt in range(SAME_MODEL_RETRIES + 1):
                try:
                    coro = self._get_client().chat.completions.create(
                        model=model, messages=messages, stream=True,
                        timeout=timeout, **kwargs,
                    )
                    stream = await self._call_with_hard_timeout(coro, timeout)
                except Exception as exc:  # noqa: BLE001 - opening the stream
                    last_exc = exc
                    if attempt < SAME_MODEL_RETRIES and self._is_retryable(exc):
                        logger.warning(
                            "Model %s could not open a stream (attempt %d/%d): %s"
                            " — retrying", model, attempt + 1,
                            SAME_MODEL_RETRIES + 1, exc,
                        )
                        continue
                    self._quarantine(model, exc)
                    break

                # The stream is open. Iterate it, watching for a rejection
                # that arrives before any content token.
                emitted = False
                try:
                    async for chunk in stream:
                        if (chunk.choices and chunk.choices[0].delta
                                and chunk.choices[0].delta.content):
                            emitted = True
                        yield chunk, model
                except Exception as exc:  # noqa: BLE001 - mid-stream failure
                    if emitted:
                        logger.error(
                            "Model %s failed after the answer had started (%s); "
                            "not retrying, the client already has a partial answer.",
                            model, exc,
                        )
                        raise
                    last_exc = exc
                    logger.warning(
                        "Model %s rejected mid-stream before any token (%s) — "
                        "failing over", model, str(exc)[:120],
                    )
                    self._quarantine(model, exc)
                    break
                return

        raise last_exc  # type: ignore[misc]

    # this prevents the event loop from blocking for minutes without returning.
    TIMEOUT: float = 60.0

    @property
    def alive_models(self) -> List[str]:
        """Models that are currently usable (not disabled, not cooling down)."""
        return [m for m in self._models if self._is_alive(m)]

    @property
    def disabled_models(self) -> List[str]:
        """Models the provider reported as non-existent; never retried again."""
        return sorted(self._disabled)


# Singleton pool instance
_llm_pool: Optional[ModelFallbackPool] = None


def get_llm_pool() -> ModelFallbackPool:
    global _llm_pool
    if _llm_pool is None:
        _llm_pool = ModelFallbackPool(API_KEY, BASE_URL, FALLBACK_MODELS)
    return _llm_pool

# Lazy initialization — avoids blocking the event loop at import time.
# SentenceTransformerEmbeddingFunction downloads / loads model weights
# which can take 10-15 s; deferring it lets uvicorn start immediately.
_emb_fn = None


def _get_emb_fn():
    global _emb_fn
    if _emb_fn is None:
        _emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _emb_fn


# ChromaDB client + collection are fast to initialise (<1 s) so keep them eager.
#
# The embedding function MUST be passed explicitly. Without it Chroma installs
# its own DefaultEmbeddingFunction (ONNX all-MiniLM-L6-v2, English-only) and
# downloads it at runtime. Two consequences, both observed in production:
#
#   * Memory: the collection's ONNX model and the app's multilingual model are
#     two separate instances. Retrieval peaked at ~1.3 GB, and a 2 GB container
#     was OOM-killed mid-request -- no traceback, no SSE error frame, and the
#     next request answered 502 while the instance restarted.
#   * Correctness: hybrid.retrieve_hybrid() calls collection.query(query_texts=...)
#     so the query was embedded by ONNX MiniLM while every stored vector came
#     from paraphrase-multilingual-MiniLM-L12-v2 -- comparing two unrelated
#     embedding spaces.
#
# Passing the shared instance means one model in memory and one consistent
# space for both retrieval and indexing.
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection(name=CHROMA_COLLECTION)

# ==========================================
# 2. Modular Pipeline Functions
# ==========================================

# Query rewriting costs one LLM round-trip before generation can start. It is
# only useful for follow-up questions that reference earlier context, so it is
# gated on a cheap pronoun/ellipsis check plus an env kill-switch.
REWRITE_ENABLED: bool = os.getenv("REWRITE_ENABLED", "true").strip().lower() not in (
    "0", "false", "no", "off",
)

# Standalone pronouns / deictics that only make sense with earlier context.
# Each alternative is anchored to word boundaries on both sides so it cannot
# fire inside an unrelated word ("il" inside "solaire", "on" inside "conso").
_FOLLOWUP_MARKERS = re.compile(
    r"\b(?:"
    # Pronouns and deictics
    r"leurs?|elles|ils|il|elle|on|celles?|ceux?|lui|"
    r"who|its?|they|them|this|that|these|those|"
    # Possessives: "son budget", "sa consommation", "my report"
    r"sa|son|ses|leurs|ma|mon|mes|ta|ton|tes|"
    r"my|your|its|our|their|"
    # Arabic pronouns / connectives
    r"\u0645\u0627|\u0647\u0645|\u0647\u0630\u0647\u0645|\u0639\u0646\u0647\u0627|\u0647\u0630\u0647|\u0648\u0647\u0645"
    r")\b"
    # Ellipsis shorthand: "et lui ?", "why ?"
    # NOTE: neither alternative may be left empty. An empty alternative makes
    # the whole group matchable at zero width, so re.search() succeeds on every
    # query and every question gets treated as a follow-up.
    r"|\.\.\.",
    flags=re.IGNORECASE,
)

# A question made of one or two words ("Why ?", "Et lui ?") cannot stand alone.
_TINY_QUESTION = re.compile(r"^\S+(?:\s+\S+)?\s*[?؟]\s*$")


def _needs_rewrite(user_query: str) -> bool:
    """Heuristic: does this query reference earlier conversation context?

    Returns True only when the query looks like a follow-up (pronoun,
    possessive, or deictic reference) or is too short to stand alone.
    Deliberately conservative in the *cheap* direction: a false positive costs
    one rewrite call, a false negative costs some retrieval recall.
    """
    q = (user_query or "").strip()
    if not q:
        return False
    if _FOLLOWUP_MARKERS.search(q):
        return True
    return bool(_TINY_QUESTION.match(q))

async def rewrite_query_with_history(user_query: str, chat_history: List[Dict[str, str]]) -> str:
    """
    Reformulates a follow-up user query into a standalone search query using chat context.

    This is a full LLM round-trip and runs *before* generation, so it directly
    adds to time-to-first-token. Most turns are not follow-ups ("What is the
    ANME?" needs no rewriting), so the rewrite is skipped when the query is
    already self-contained. Set REWRITE_ENABLED=false to disable it entirely.
    """
    if not chat_history:
        return user_query

    if not REWRITE_ENABLED or not _needs_rewrite(user_query):
        return user_query

    # Format recent history into a compact string (bounded by token budget).
    # run_pipeline already budgets the history; re-checking here is an idempotent
    # safety net for direct callers of this function.
    formatted_history = "\n".join(
        f"{msg['role']}: {msg['content']}"
        for msg in get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET)
    )
    
    prompt = f"""Given the following conversation history and a follow-up question, rephrase the follow-up question to be a self-contained search query.
Do NOT answer the question, only rephrase it to include necessary entities from context.

History:
{formatted_history}

Follow-up Question: {user_query}
Standalone Query:"""

    # LLM call with automatic failover across models
    pool = get_llm_pool()
    response, model_used = await pool.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    # Track token usage
    try:
        tokens, requests_ctr = _get_llm_metrics()
        if response.usage:
            tokens.labels(model=model_used, type="prompt").inc(response.usage.prompt_tokens)
            tokens.labels(model=model_used, type="completion").inc(response.usage.completion_tokens)
        requests_ctr.labels(model=model_used, endpoint="rewrite").inc()
    except Exception:
        pass  # never let metrics break the pipeline

    return response.choices[0].message.content.strip()


def retrieve_context(user_query: str, n_results: int = 5) -> str:
    """Fetches chunks from ChromaDB and formats them into a single string."""
    raw_query_vectors = _get_emb_fn()([user_query])
    
    formatted_query_vectors = [
        vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
        for vector in raw_query_vectors
    ]
    
    results = collection.query(
        query_embeddings=formatted_query_vectors,
        n_results=n_results
    )
    
    retrieved_chunks = results["documents"][0]
    return "\n\n---\n\n".join(retrieved_chunks)


# Month names (EN/FR) used to parse a best-effort publication date from
# filenames like "IRENA_Pan-Arab_Strategy_June-2014.pdf" or
# "DGE_Guide_deccarbonation_2026.pdf".
_MONTH_PATTERNS = {
    "january|janvier": "Jan",
    "february|fevrier|février": "Fév",
    "march|mars": "Mar",
    "april|avril": "Avr",
    "may|mai": "Mai",
    "june|juin": "Juin",
    "july|juillet": "Juil",
    "august|aout|août": "Août",
    "september|septembre": "Sep",
    "october|octobre": "Oct",
    "november|novembre": "Nov",
    "december|decembre|décembre": "Déc",
}


def extract_document_date(source_file: str) -> Optional[str]:
    """Best-effort publication date parsed from a document filename.

    Looks for a 4-digit year (19xx/20xx) and, when a month name appears near
    it, returns "Mon YYYY" (e.g. "Juin 2014"). Falls back to just the year,
    or ``None`` when no year is found.
    """
    if not source_file:
        return None
    year_match = re.search(r"(19|20)\d{2}", source_file)
    if not year_match:
        return None
    year = year_match.group(0)
    year_pos = year_match.start()
    # Prefer a month name within ~20 chars of the year (same token group).
    for pattern, label in _MONTH_PATTERNS.items():
        month_match = re.search(pattern, source_file, flags=re.IGNORECASE)
        if month_match and abs(month_match.start() - year_pos) <= 20:
            return f"{label} {year}"
    return year


def retrieve_context_structured(user_query: str, n_results: int = 5) -> List[Dict[str, Any]]:
    """Fetches chunks from ChromaDB along with metadata (source, page, etc.)."""
    raw_query_vectors = _get_emb_fn()([user_query])
    
    formatted_query_vectors = [
        vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
        for vector in raw_query_vectors
    ]
    
    results = collection.query(
        query_embeddings=formatted_query_vectors,
        n_results=n_results,
        include=["documents", "metadatas"]
    )
    
    documents = results["documents"][0]
    metadatas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else [{}] * len(documents)
    
    structured_sources = []
    for doc, meta in zip(documents, metadatas):
        source_file = meta.get("source", meta.get("file_name", "Unknown Document"))
        structured_sources.append({
            "content": doc,
            # Unified key: source_file (matches Message.sources in the DB layer)
            "source_file": source_file,
            "page": meta.get("page", meta.get("page_number", "N/A")),
            # Best-effort publication date parsed from the filename (nullable).
            "date": extract_document_date(source_file),
        })
        
    return structured_sources

def format_sources_for_prompt(sources: List[Dict[str, Any]]) -> str:
    """Helper function to format structured sources into a clean prompt context string.
    
    L3 FIX: Applies content masking and length limits to prevent document extraction.
    """
    formatted_chunks = []
    for idx, src in enumerate(sources, 1):
        # L3 FIX: Mask sensitive content
        content = mask_sensitive_content(src['content'])
        
        # L3 FIX: Limit chunk length to prevent extraction
        if len(content) > 1000:
            content = content[:1000] + "..."
        
        formatted_chunks.append(
            f"[Doc {idx} - Source: {src['source_file']} (Page {src['page']})]\n{content}"
        )
    return "\n\n---\n\n".join(formatted_chunks)

async def generate_answer_stream(
    user_query: str,
    context: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
):
    """Async generator yielding answer text chunks as they stream from the LLM.

    Same prompt construction as ``generate_answer`` but with ``stream=True``,
    so tokens arrive incrementally (used by the /api/chat/stream SSE endpoint).
    """
    system_prompt = (
        "You are an expert AI assistant specializing in the Tunisian energy sector. "
        "Use ONLY the following context to answer the user's question. "
        "If the answer is not contained in the context, say 'I do not have enough information to answer that based on the provided documents.' "
        "Do not hallucinate or use outside knowledge. Answer in the same language as the user's query.\n\n"
        f"Context:\n{context}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET):
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_query})

    # Streaming LLM call. chat_stream_failover, not chat_stream: the provider
    # often accepts the request and rejects it once iteration starts, which
    # chat_stream has already returned by and cannot recover from.
    pool = get_llm_pool()
    async for chunk, model_used in pool.chat_stream_failover(
        messages=messages,
        temperature=0.1,
    ):
        if not chunk.choices:
            # The last chunk may carry usage info (prompt_tokens, completion_tokens)
            if hasattr(chunk, "usage") and chunk.usage:
                try:
                    tokens, requests_ctr = _get_llm_metrics()
                    tokens.labels(model=model_used, type="prompt").inc(chunk.usage.prompt_tokens)
                    tokens.labels(model=model_used, type="completion").inc(chunk.usage.completion_tokens)
                    requests_ctr.labels(model=model_used, endpoint="generate").inc()
                except Exception:
                    pass
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


async def generate_answer(user_query: str, context: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    """Takes pre-fetched context and optional chat history to query the LLM."""
    system_prompt = (
        "You are an expert AI assistant specializing in the Tunisian energy sector. "
        "Use ONLY the following context to answer the user's question. "
        "If the answer is not contained in the context, say 'I do not have enough information to answer that based on the provided documents.' "
        "Do not hallucinate or use outside knowledge. Answer in the same language as the user's query.\n\n"
        f"Context:\n{context}"
    )

    # Build full message chain including past conversation
    messages = [{"role": "system", "content": system_prompt}]
    
    # Append the token-budgeted history (recent context prioritized).
    # Idempotent re-check: protects direct callers if history was not pre-budgeted.
    for msg in get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET):
        messages.append({"role": msg["role"], "content": msg["content"]})
            
    messages.append({"role": "user", "content": user_query})

    # LLM call with automatic failover across models
    pool = get_llm_pool()
    response, model_used = await pool.chat(
        messages=messages,
        temperature=0.1
    )

    # Track token usage
    try:
        tokens, requests_ctr = _get_llm_metrics()
        if response.usage:
            tokens.labels(model=model_used, type="prompt").inc(response.usage.prompt_tokens)
            tokens.labels(model=model_used, type="completion").inc(response.usage.completion_tokens)
        requests_ctr.labels(model=model_used, endpoint="generate").inc()
    except Exception:
        pass

    return response.choices[0].message.content


async def run_pipeline(
    user_query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Executes the full Retrieval-Augmented Generation flow with conversational memory.

    Returns a tuple of (answer, structured_sources).
    ChromaDB's synchronous file I/O is offloaded to a thread pool so it
    does not block the async event loop.
    
    Includes guardrails for:
    - L1: System prompt leak prevention (query sanitization)
    - L3: Document extraction prevention (content masking)
    - L6: Domain escape prevention (domain classification)
    - L7: Hallucination prevention (confidence scoring)
    - L8: Poisoned response prevention (output validation)
    """
    if chat_history is None:
        chat_history = []

    # Step 0: GUARDRAILS — Query validation (L1 fix)
    guard = check_query(user_query)
    if not guard.allowed:
        logger.warning("Query blocked: %s | detections=%s", guard.block_reason, guard.detections)
        return get_safe_response(), []
    
    user_query = guard.sanitised_query
    
    # L6 FIX: Domain classification
    domain_result = classify_domain(user_query)
    if domain_result["domain"] == "non_energy":
        logger.warning("Non-energy query blocked: %s", domain_result)
        return get_safe_response(), []

    # Step 1: Contextualize query using the token-optimized history
    optimized_history = get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET)
    logger.info("[1] Contextualizing query...")
    standalone_query = await rewrite_query_with_history(user_query, optimized_history)
    logger.debug("    Standalone query: '%s'", standalone_query)
    
    # Step 2: Hybrid search (vector + BM25, RRF-fused, optional rerank) with the
    # standalone query, run in a thread pool (ChromaDB + BM25 are sync I/O).
    logger.info("[2] Searching database (hybrid: vector + BM25)...")
    structured_sources = await run_in_threadpool(
        retrieve_context_hybrid, standalone_query, n_results=5
    )
    context_str = format_sources_for_prompt(structured_sources)
    
    # Step 3: Synthesize answer using the token-optimized history
    logger.info("[3] Synthesizing answer with Mistral Large...")
    answer = await generate_answer(user_query, context_str, optimized_history)
    
    # Step 4: GUARDRAILS — Output validation (L8 fix)
    validation = validate_output(answer)
    if not validation["clean"]:
        logger.warning("Output validation failed: %s", validation['violations'])
        return get_safe_response(), structured_sources
    
    # L6 FIX: Domain compliance filtering
    answer = filter_domain_violations(answer, user_query)
    
    # L7 FIX: Confidence assessment
    confidence = assess_confidence(answer, context_str)
    if confidence["level"] == "low":
        logger.warning("Low confidence response: %s", confidence)
        return (
            "I'm not confident I can answer this accurately based on the provided documents. "
            "Please rephrase your question or ask about a specific topic in the Tunisian energy sector."
        ), structured_sources
    
    # L7 FIX: Fact verification
    claim_violations = verify_claims(answer, context_str)
    if claim_violations:
        logger.warning("Fact verification failed: %s", claim_violations)
        return (
            "I cannot verify some claims in my response against the provided documents. "
            "Please ask a more specific question about the Tunisian energy sector."
        ), structured_sources
    
    return answer, structured_sources


# Last retrieval outcome, surfaced by /health. There is no shell on the Render
# instance, so this is the only way to tell "retrieval is slow" apart from
# "the process died during retrieval".
_LAST_RETRIEVAL: Dict[str, Any] = {
    "count": 0,
    "last_seconds": None,
    "last_rewrite_seconds": None,
    "last_sources": None,
    "last_error": None,
    "last_error_seconds": None,
}


def retrieval_stats() -> Dict[str, Any]:
    """Snapshot of the most recent retrieval (see ``_LAST_RETRIEVAL``)."""
    return dict(_LAST_RETRIEVAL)


async def stream_pipeline(
    user_query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
):
    """Async generator yielding SSE event dicts for the full RAG flow.

    Events:
      {"type": "status", "message": "contextualizing|searching|generating"}
      {"type": "sources", "sources": [...]}   (retrieved before generation)
      {"type": "token", "content": "..."}     (streamed answer fragments)
      {"type": "done", "answer": "...", "sources": [...]}

    Chat history is token-budgeted server-side (HISTORY_TOKEN_BUDGET), so the
    client can safely send the full session.
    
    Includes guardrails for:
    - L1: System prompt leak prevention (query sanitization)
    - L3: Document extraction prevention (content masking)
    - L6: Domain escape prevention (domain classification)
    - L7: Hallucination prevention (confidence scoring)
    - L8: Poisoned response prevention (output validation)
    """
    if chat_history is None:
        chat_history = []

    # Step 0: GUARDRAILS — Query validation (L1 fix)
    guard = check_query(user_query)
    if not guard.allowed:
        logger.warning("Query blocked: %s | detections=%s", guard.block_reason, guard.detections)
        safe_response = get_safe_response()
        yield {"type": "sources", "sources": []}
        yield {"type": "token", "content": safe_response}
        yield {"type": "done", "answer": safe_response, "sources": []}
        return
    
    user_query = guard.sanitised_query
    
    # L6 FIX: Domain classification
    domain_result = classify_domain(user_query)
    if domain_result["domain"] == "non_energy":
        logger.warning("Non-energy query blocked: %s", domain_result)
        safe_response = get_safe_response()
        yield {"type": "sources", "sources": []}
        yield {"type": "token", "content": safe_response}
        yield {"type": "done", "answer": safe_response, "sources": []}
        return

    optimized_history = get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET)

    yield {"type": "status", "message": "contextualizing"}
    _t0 = time.monotonic()
    standalone_query = await rewrite_query_with_history(user_query, optimized_history)

    yield {"type": "status", "message": "searching"}
    _t1 = time.monotonic()
    try:
        structured_sources = await run_in_threadpool(
            retrieve_context_hybrid, standalone_query, n_results=5
        )
    except BaseException as exc:
        # Without this the client sees "searching" and then a bare stream end,
        # with the reason visible only in a traceback nobody reads.
        _LAST_RETRIEVAL["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        _LAST_RETRIEVAL["last_error_seconds"] = round(time.monotonic() - _t1, 1)
        logger.exception(
            "Retrieval failed after %.1fs for query=%r", time.monotonic() - _t1,
            standalone_query[:120],
        )
        raise
    _LAST_RETRIEVAL["count"] += 1
    _LAST_RETRIEVAL["last_seconds"] = round(time.monotonic() - _t1, 1)
    _LAST_RETRIEVAL["last_rewrite_seconds"] = round(_t1 - _t0, 2)
    _LAST_RETRIEVAL["last_sources"] = len(structured_sources or [])
    logger.info(
        "Retrieval took %.1fs (rewrite %.2fs) -> %d sources",
        time.monotonic() - _t1, _t1 - _t0, len(structured_sources or []),
    )
    context_str = format_sources_for_prompt(structured_sources)

    # Sources are ready before generation starts -> send them early so the
    # frontend can render citations while the answer streams.
    yield {"type": "sources", "sources": structured_sources}

    yield {"type": "status", "message": "generating"}
    answer_parts: List[str] = []
    async for part in generate_answer_stream(user_query, context_str, optimized_history):
        answer_parts.append(part)
        yield {"type": "token", "content": part}

    answer = "".join(answer_parts)
    
    # GUARDRAILS — Output validation (L8 fix)
    validation = validate_output(answer)
    if not validation["clean"]:
        logger.warning("Output validation failed: %s", validation['violations'])
        safe_response = get_safe_response()
        yield {"type": "done", "answer": safe_response, "sources": structured_sources}
        return
    
    # L6 FIX: Domain compliance filtering
    answer = filter_domain_violations(answer, user_query)
    
    # L7 FIX: Confidence assessment
    confidence = assess_confidence(answer, context_str)
    if confidence["level"] == "low":
        logger.warning("Low confidence response: %s", confidence)
        safe_response = (
            "I'm not confident I can answer this accurately based on the provided documents. "
            "Please rephrase your question or ask about a specific topic in the Tunisian energy sector."
        )
        yield {"type": "done", "answer": safe_response, "sources": structured_sources}
        return
    
    # L7 FIX: Fact verification
    claim_violations = verify_claims(answer, context_str)
    if claim_violations:
        logger.warning("Fact verification failed: %s", claim_violations)
        safe_response = (
            "I cannot verify some claims in my response against the provided documents. "
            "Please ask a more specific question about the Tunisian energy sector."
        )
        yield {"type": "done", "answer": safe_response, "sources": structured_sources}
        return
    
    yield {"type": "done", "answer": answer, "sources": structured_sources}


# ==========================================
# 3. Execution Example
# ==========================================

if __name__ == "__main__":
    # Simulated conversation history
    sample_history = [
        {"role": "user", "content": "Quel est le rôle de l'ANME ?"},
        {"role": "assistant", "content": "L'ANME conçoit et met en œuvre la politique de l'État en matière de maîtrise de l'énergie."}
    ]
    
    # Follow-up question relying on prior context ("leur" -> ANME)
    follow_up_query = "Qui gère leur budget ?"
    
    answer, _sources = asyncio.run(run_pipeline(follow_up_query, chat_history=sample_history))
    print("========== FINAL ANSWER ==========\n")
    print(answer)