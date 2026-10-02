import asyncio
import logging
import os
import re
import sys
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
from openai import APITimeoutError, APIStatusError

_DEFAULT_MODELS = [
    "combo/freemodels",
    "deepseek-v4-flash",
    "qwen3.8-27b",
    "agnes-2.5-flash",
    "glm-5.3-flash-free",
    "laguna-s-2.1",
    "stepfun-3.7-flash",
    "nemotron-3-ultra",
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


class ModelFallbackPool:
    """Thread-safe (asyncio-safe) model pool with automatic failover.

    Usage::

        pool = ModelFallbackPool(API_KEY, BASE_URL, FALLBACK_MODELS)
        resp = await pool.chat(model="deepseek-v4-flash", messages=[...])
        async for chunk in pool.chat_stream(model="deepseek-v4-flash", messages=[...]):
            ...
    """

    def __init__(self, api_key: str, base_url: str, models: List[str]):
        self._api_key = api_key
        self._base_url = base_url
        self._models = list(models)
        self._client: Optional[AsyncOpenAI] = None
        self._dead: Dict[str, float] = {}  # model -> timestamp when it died
        self._current_idx = 0  # round-robin pointer

    # -- client management ---------------------------------------------------

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None or self._client.is_closed:
            # 60s timeout: without this the client can hang indefinitely on a slow\r
            # or unresponsive provider, which is what caused the 5-minute\r
            # streaming deadlock observed in production.\r
            self._client = AsyncOpenAI(\r
                api_key=self._api_key,\r
                base_url=self._base_url,\r
                timeout=60,\r
            )\r
        return self._client

    # -- model selection ------------------------------------------------------

    def _is_alive(self, model: str) -> bool:
        died_at = self._dead.get(model)
        if died_at is None:
            return True
        if _time.time() - died_at > DEAD_TTL:
            # Cooldown expired → allow one retry
            del self._dead[model]
            return True
        return False

    def _mark_dead(self, model: str) -> None:
        self._dead[model] = _time.time()
        logger.warning("Model %s marked dead for %.0fs", model, DEAD_TTL)

    def _pick_model(self, preferred: Optional[str] = None) -> str:
        """Return the best available model, preferring *preferred* if alive."""
        if preferred and self._is_alive(preferred):
            return preferred
        # Round-robin through the list
        for _ in range(len(self._models)):
            candidate = self._models[self._current_idx]
            self._current_idx = (self._current_idx + 1) % len(self._models)
            if self._is_alive(candidate):
                return candidate
        # All dead — reset and try the first one anyway (worst case)
        self._dead.clear()
        return self._models[0]

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
        timeout: float = 15,
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
                self._mark_dead(model)
                logger.warning("Model %s failed: %s — trying next", model, exc)
        # Every model failed
        raise last_exc  # type: ignore[misc]

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        timeout: float = 15,
        **kwargs,
    ) -> Tuple[Any, str]:
        """Streaming chat with automatic failover.

        Returns (stream, model_used).  Caller must iterate the stream.
        """
        last_exc: Optional[Exception] = None
        tried: set = set()
        for _ in range(len(self._models)):
            model = self._pick_model(preferred)
            if model in tried:
                break
            tried.add(model)
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
                self._mark_dead(model)
                logger.warning("Model %s failed (stream): %s — trying next", model, exc)
        raise last_exc  # type: ignore[misc]

    # Hard per-request timeout: 60s. If the LLM endpoint is slow or hung,\n    # this prevents the event loop from blocking for minutes without returning.\n    TIMEOUT: float = 60.0\n\n    @property\n    def alive_models(self) -> List[str]:
        """Models that are currently marked alive."""
        return [m for m in self._models if self._is_alive(m)]


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
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection(name="tunisia_energy_rag")

# ==========================================
# 2. Modular Pipeline Functions
# ==========================================

async def rewrite_query_with_history(user_query: str, chat_history: List[Dict[str, str]]) -> str:
    """
    Reformulates a follow-up user query into a standalone search query using chat context.
    """
    if not chat_history:
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

    # Streaming LLM call with automatic failover
    pool = get_llm_pool()
    stream, model_used = await pool.chat_stream(
        messages=messages,
        temperature=0.1,
    )
    async for chunk in stream:
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
    standalone_query = await rewrite_query_with_history(user_query, optimized_history)

    yield {"type": "status", "message": "searching"}
    structured_sources = await run_in_threadpool(
        retrieve_context_hybrid, standalone_query, n_results=5
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