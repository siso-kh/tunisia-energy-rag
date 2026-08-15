"""
Integration tests for the Tunisia Energy RAG prototype.

These tests exercise real components together:
- FastAPI HTTP layer (via TestClient)
- ChromaDB vector retrieval
- LLM generation (async OpenAI client) - requires a reachable backend
- The full RAG pipeline with chat history
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.rag.retrieve import (
    generate_answer,
    run_pipeline,
    retrieve_context_structured,
    format_sources_for_prompt,
)

pytestmark = pytest.mark.integration


# ==========================================
# FastAPI HTTP layer
# ==========================================

def test_api_health_endpoint():
    """GET /health must return the healthy status payload."""
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "api": "online"}


def test_api_chat_rejects_empty_query():
    """A blank query must be rejected with HTTP 400 before any retrieval/LLM work."""
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"query": "   "})
    assert response.status_code == 400


def test_api_chat_endpoint_full_flow():
    """
    End-to-end HTTP flow: POST /api/chat -> query rewrite (no history) ->
    structured retrieval -> prompt formatting -> async generation.
    Verifies the complete response envelope (query, sources, answer).
    """
    payload = {
        "query": "Quel est le role de l'ANME dans la transition energetique ?",
        "chat_history": [],
    }
    with TestClient(app) as client:
        response = client.post("/api/chat", json=payload)

    assert response.status_code == 200, f"API error: {response.text}"
    data = response.json()

    # Response envelope
    assert data["query"] == payload["query"]
    assert isinstance(data["answer"], str) and len(data["answer"]) > 10

    # Structured sources must be non-empty and well-formed (unified key: source_file)
    assert isinstance(data["sources"], list) and len(data["sources"]) > 0
    for source in data["sources"]:
        assert "source_file" in source and source["source_file"]
        assert "content" in source and len(source["content"]) > 0
        assert "page" in source


def test_api_chat_endpoint_with_history():
    """
    End-to-end HTTP flow WITH chat history: verifies the async
    query-rewriting path (follow-up query -> standalone query).
    """
    payload = {
        "query": "Qui gere leur budget ?",
        "chat_history": [
            {"role": "user", "content": "Quel est le role de l'ANME ?"},
            {
                "role": "assistant",
                "content": "L'ANME concoit et met en oeuvre la politique de l'Etat en matiere de maitrise de l'energie.",
            },
        ],
    }
    with TestClient(app) as client:
        response = client.post("/api/chat", json=payload)

    assert response.status_code == 200, f"API error: {response.text}"
    data = response.json()
    assert isinstance(data["answer"], str) and len(data["answer"]) > 10


# ==========================================
# Retrieval -> Prompt chain (no LLM)
# ==========================================

def test_retrieval_to_prompt_chain():
    """
    Integration between ChromaDB structured retrieval and prompt formatting:
    sources must round-trip into a context string with doc headers.
    """
    sources = retrieve_context_structured("autoconsommation", n_results=3)
    assert len(sources) >= 1, "ChromaDB returned no sources"

    context = format_sources_for_prompt(sources)
    assert "[Doc 1 - Source:" in context
    assert "(Page" in context
    # Every source's content must appear in the formatted context
    for source in sources:
        assert source["content"] in context


# ==========================================
# Async LLM pipeline (real backend)
# ==========================================

def test_parallel_async_generation():
    """
    Verifies the AsyncOpenAI client handles multiple concurrent requests
    inside a single event loop (the reason for the async conversion).
    """
    context = (
        "La STEG gere l'infrastructure electrique tunisienne. "
        "L'ANME concoit la politique de maitrise de l'energie. "
        "Le FTE finance la transition energetique."
    )
    questions = [
        "Qui est la STEG ?",
        "Quel est le role de l'ANME ?",
        "Qu'est-ce que le FTE ?",
    ]

    async def _gather():
        return await asyncio.gather(
            *(generate_answer(q, context) for q in questions)
        )

    results = asyncio.run(_gather())

    assert len(results) == len(questions)
    for question, answer in zip(questions, results):
        assert isinstance(answer, str) and len(answer) > 0, f"Empty answer for: {question}"


def test_run_pipeline_end_to_end():
    """
    Full RAG pipeline (async): query rewriting + retrieval + generation,
    including a follow-up query that depends on prior conversation context.
    """
    history = [
        {"role": "user", "content": "Quel est le role de l'ANME ?"},
        {
            "role": "assistant",
            "content": "L'ANME concoit et met en oeuvre la politique de l'Etat en matiere de maitrise de l'energie.",
        },
    ]

    async def _run():
        return await run_pipeline("Qui gere leur budget ?", chat_history=history)

    answer, sources = asyncio.run(_run())
    assert isinstance(answer, str) and len(answer) > 10
    assert isinstance(sources, list) and len(sources) > 0


