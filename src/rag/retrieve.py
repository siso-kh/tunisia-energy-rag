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

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    """Returns the shared AsyncOpenAI client, transparently recreating it if it
    was closed by an event-loop shutdown (prevents "client has been closed"
    errors when the app runs across multiple event loops, e.g. in tests)."""
    global _client
    if _client is None or _client.is_closed:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client

emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)
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

    # Direct LLM call to contextualize the prompt
    response = await get_client().chat.completions.create(
        model="mistral-large",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    # Track token usage
    try:
        tokens, requests_ctr = _get_llm_metrics()
        if response.usage:
            tokens.labels(model="mistral-large", type="prompt").inc(response.usage.prompt_tokens)
            tokens.labels(model="mistral-large", type="completion").inc(response.usage.completion_tokens)
        requests_ctr.labels(model="mistral-large", endpoint="rewrite").inc()
    except Exception:
        pass  # never let metrics break the pipeline

    return response.choices[0].message.content.strip()


def retrieve_context(user_query: str, n_results: int = 5) -> str:
    """Fetches chunks from ChromaDB and formats them into a single string."""
    raw_query_vectors = emb_fn([user_query])
    
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
    raw_query_vectors = emb_fn([user_query])
    
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

    stream = await get_client().chat.completions.create(
        model="mistral-large",
        messages=messages,
        temperature=0.1,
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            # The last chunk may carry usage info (prompt_tokens, completion_tokens)
            if hasattr(chunk, "usage") and chunk.usage:
                try:
                    tokens, requests_ctr = _get_llm_metrics()
                    tokens.labels(model="mistral-large", type="prompt").inc(chunk.usage.prompt_tokens)
                    tokens.labels(model="mistral-large", type="completion").inc(chunk.usage.completion_tokens)
                    requests_ctr.labels(model="mistral-large", endpoint="generate").inc()
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

    response = await get_client().chat.completions.create(
        model="mistral-large",
        messages=messages,
        temperature=0.1
    )

    # Track token usage
    try:
        tokens, requests_ctr = _get_llm_metrics()
        if response.usage:
            tokens.labels(model="mistral-large", type="prompt").inc(response.usage.prompt_tokens)
            tokens.labels(model="mistral-large", type="completion").inc(response.usage.completion_tokens)
        requests_ctr.labels(model="mistral-large", endpoint="generate").inc()
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