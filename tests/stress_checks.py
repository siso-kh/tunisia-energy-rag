"""Stress check: huge pasted messages through the /api/chat endpoint.

Validates the token-budget strategy end-to-end: a user pasting a massive
block of text into the chat history must NOT overflow the LLM context window
(the previous 400 Bad Request failure mode).

Not auto-collected by the default suite (filename intentionally does not
match test_*.py). Run it explicitly with:

    python -m pytest tests/stress_checks.py -v
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.rag.retrieve import HISTORY_TOKEN_BUDGET
from src.utils.token_manager import get_optimized_history, tokenizer

pytestmark = pytest.mark.stress

# ~68 chars x 2000 -> roughly 40k+ tokens, far beyond any context window
HUGE_TEXT = "Voici un document technique sur l'energie renouvelable en Tunisie. " * 2000

PRIOR_HISTORY = [
    {"role": "user", "content": "Quel est le role de l'ANME ?"},
    {"role": "assistant", "content": "L'ANME concoit et met en oeuvre la politique energetique."},
]

QUERY = "Qui gere leur budget ?"


def _check_budget_evidence(history):
    """Prove the raw payload would overflow and that the budget fixes it."""
    raw_tokens = len(tokenizer.encode(json.dumps(history, ensure_ascii=False)))
    budgeted = get_optimized_history(history, max_tokens=HISTORY_TOKEN_BUDGET)
    budgeted_tokens = len(tokenizer.encode(json.dumps(budgeted, ensure_ascii=False)))
    print(f"\n  raw history tokens   : {raw_tokens}")
    print(f"  budgeted history     : {len(budgeted)} msgs / ~{budgeted_tokens} tokens")
    assert raw_tokens > 10000, "Test payload is too small to be meaningful"
    assert len(budgeted) <= len(history)
    return budgeted


def _run_chat(history):
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"query": QUERY, "chat_history": history})
    return response


def test_huge_message_in_middle_of_history():
    """Huge paste buried mid-history: budget must drop it, keep newer context."""
    history = PRIOR_HISTORY + [
        {"role": "user", "content": HUGE_TEXT},
        {"role": "assistant", "content": "Analyse tres interessante."},
    ]
    budgeted = _check_budget_evidence(history)
    assert all("energie renouvelable" not in m["content"] for m in budgeted), (
        "Oversized message must be excluded from the LLM context"
    )

    response = _run_chat(history)
    assert response.status_code == 200, f"Endpoint failed: {response.text[:300]}"
    data = response.json()
    assert len(data["answer"]) > 10
    assert len(data["sources"]) > 0
    print(f"  -> HTTP 200 | answer {len(data['answer'])} chars | {len(data['sources'])} sources")


def test_huge_newest_message_is_dropped():
    """The exact dangerous scenario: the pasted wall of text is the newest message."""
    history = PRIOR_HISTORY + [{"role": "user", "content": HUGE_TEXT}]
    budgeted = _check_budget_evidence(history)
    assert budgeted == [], "An oversized newest message must be excluded entirely"

    response = _run_chat(history)
    assert response.status_code == 200, f"Endpoint failed: {response.text[:300]}"
    data = response.json()
    assert len(data["answer"]) > 10
    assert len(data["sources"]) > 0
    print(f"  -> HTTP 200 | answer {len(data['answer'])} chars | {len(data['sources'])} sources")
