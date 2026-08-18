import asyncio
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
from src.utils.token_manager import get_optimized_history

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
    """Helper function to format structured sources into a clean prompt context string."""
    formatted_chunks = []
    for idx, src in enumerate(sources, 1):
        formatted_chunks.append(
            f"[Doc {idx} - Source: {src['source_file']} (Page {src['page']})]\n{src['content']}"
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
    
    return response.choices[0].message.content


async def run_pipeline(
    user_query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Executes the full Retrieval-Augmented Generation flow with conversational memory.

    Returns a tuple of (answer, structured_sources).
    ChromaDB's synchronous file I/O is offloaded to a thread pool so it
    does not block the async event loop.
    """
    if chat_history is None:
        chat_history = []

    # Step 1: Contextualize query using the token-optimized history
    optimized_history = get_optimized_history(chat_history, max_tokens=HISTORY_TOKEN_BUDGET)
    print("\n[1] Contextualizing query...")
    standalone_query = await rewrite_query_with_history(user_query, optimized_history)
    print(f"    Standalone query: '{standalone_query}'")
    
    # Step 2: Hybrid search (vector + BM25, RRF-fused, optional rerank) with the
    # standalone query, run in a thread pool (ChromaDB + BM25 are sync I/O).
    print("[2] Searching database (hybrid: vector + BM25)...")
    structured_sources = await run_in_threadpool(
        retrieve_context_hybrid, standalone_query, n_results=5
    )
    context_str = format_sources_for_prompt(structured_sources)
    
    # Step 3: Synthesize answer using the token-optimized history
    print("[3] Synthesizing answer with Mistral Large...\n")
    answer = await generate_answer(user_query, context_str, optimized_history)
    
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
    """
    if chat_history is None:
        chat_history = []

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