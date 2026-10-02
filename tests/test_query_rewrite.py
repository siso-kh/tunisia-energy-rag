"""Regression tests for the query-rewrite gate in src/rag/retrieve.py.

``rewrite_query_with_history`` is a full LLM round-trip that runs *before*
generation, so it lands directly on time-to-first-token. It is now gated on a
cheap heuristic that skips the rewrite for self-contained questions.

The important guard here is structural: the follow-up marker pattern must not
contain an empty alternative. When it did, the whole group matched at zero
width, ``re.search()`` succeeded on *every* string, and all queries were
classified as follow-ups -- which would have made the "optimisation" useless
and silently slower than before.
"""

import re

import pytest

from src.rag import retrieve


# ---------------------------------------------------------------------------
# Structural guards on the pattern itself
# ---------------------------------------------------------------------------

def test_marker_pattern_has_no_empty_alternative():
    """An empty alternative makes the regex match everything."""
    pattern = retrieve._FOLLOWUP_MARKERS.pattern
    # Strip the outer group, then check no top-level alternative is empty.
    inner = pattern[pattern.index("(?:") + 3: pattern.rindex(")")]
    for alternative in inner.split("|"):
        assert alternative != "", (
            "empty alternative in follow-up pattern makes it match every query"
        )


def test_marker_pattern_does_not_match_everything():
    """Direct behavioural guard against the zero-width-match regression."""
    for query in ("What is the ANME?", "Explique le photovoltaïque en Tunisie"):
        assert retrieve._FOLLOWUP_MARKERS.search(query) is None, (
            "marker matched a standalone question: %r" % query
        )


# ---------------------------------------------------------------------------
# Standalone questions must skip the rewrite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "What is the ANME?",
        "Quel est le role de la STEG ?",
        "Explique le mecanisme du photovoltaique en Tunisie",
        "Combien consomme la Tunisie en electricite ?",
        "What are the main solar potential zones?",
        "Quels sont les objectifs de la transition energetique ?",
    ],
)
def test_standalone_questions_do_not_need_a_rewrite(query):
    assert retrieve._needs_rewrite(query) is False


# ---------------------------------------------------------------------------
# Genuine follow-ups must still be rewritten
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "Quel est son budget ?",
        "Quel est le taux de sa consommation ?",
        "Et lui ?",
        "and then ?",
        "Why ?",
        "...",
        "What about their budget?",
        "How much does it cost?",
    ],
)
def test_follow_up_questions_do_need_a_rewrite(query):
    assert retrieve._needs_rewrite(query) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_query_is_not_a_follow_up():
    assert retrieve._needs_rewrite("") is False
    assert retrieve._needs_rewrite("   ") is False
    assert retrieve._needs_rewrite(None) is False


def test_substring_words_do_not_trigger_a_rewrite():
    """Word boundaries matter: 'il' inside 'solaire', 'on' inside 'consommation'."""
    for query in (
        "Details du solaire photovoltaique",
        "Consommation globale du pays",
        "Installation de panneaux",
    ):
        assert retrieve._needs_rewrite(query) is False, query


def test_arabic_possessives_are_detected():
    assert retrieve._needs_rewrite("ما هي ميزانيته؟") is True


# ---------------------------------------------------------------------------
# The rewrite call itself
# ---------------------------------------------------------------------------

def test_rewrite_is_skipped_without_history():
    """No history means nothing to resolve, so no LLM call is made."""
    result = _run(retrieve.rewrite_query_with_history("Quel est son budget ?", []))
    assert result == "Quel est son budget ?"


def test_rewrite_is_skipped_for_standalone_queries_even_with_history(monkeypatch):
    """This is the latency win: one fewer round-trip before generation."""
    _forbid_llm_calls(monkeypatch)
    history = [{"role": "user", "content": "Qui est l'ANME ?"},
               {"role": "assistant", "content": "L'ANME gere l'energie."}]
    query = "Explique le solaire photovoltaique en Tunisie"
    result = _run(retrieve.rewrite_query_with_history(query, history))
    assert result == query


def test_kill_switch_disables_rewriting_entirely(monkeypatch):
    """REWRITE_ENABLED=false must bypass the heuristic too."""
    _forbid_llm_calls(monkeypatch)
    monkeypatch.setattr(retrieve, "REWRITE_ENABLED", False)
    history = [{"role": "user", "content": "Qui est l'ANME ?"},
               {"role": "assistant", "content": "L'ANME gere l'energie."}]
    query = "Et lui ?"
    result = _run(retrieve.rewrite_query_with_history(query, history))
    assert result == query


def _forbid_llm_calls(monkeypatch):
    """Make any attempt to reach the provider an immediate, loud failure.

    These tests assert a *negative* (no LLM call). Without this guard a
    regression would quietly issue a real network request instead of failing.
    """
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(
                "the rewrite path made an LLM call; the skip did not apply"
            )

    monkeypatch.setattr(retrieve, "get_llm_pool", lambda: Forbidden())


def _run(coro):
    import asyncio

    return asyncio.run(coro)