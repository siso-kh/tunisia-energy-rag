"""Unit tests for the token budget history truncation utility.

These tests are pure-function tests (no LLM, no ChromaDB) and run in
milliseconds — safe for the FAST run scenario.
"""

from src.utils.token_manager import get_optimized_history, tokenizer


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def _msg_tokens(msg: dict) -> int:
    """Tokens the utility would attribute to a message (content + role + buffer)."""
    return len(tokenizer.encode(f"{msg['role']}: {msg['content']}")) + 4


def test_empty_history_returns_empty():
    assert get_optimized_history([]) == []


def test_none_history_returns_empty():
    assert get_optimized_history(None) == []


def test_zero_budget_returns_empty():
    history = [_msg("user", "Bonjour")]
    assert get_optimized_history(history, max_tokens=0) == []


def test_small_history_kept_in_chronological_order():
    history = [
        _msg("user", "Quel est le role de l'ANME ?"),
        _msg("assistant", "L'ANME concoit la politique energetique."),
        _msg("user", "Et la STEG ?"),
    ]
    result = get_optimized_history(history, max_tokens=2000)
    assert result == history  # all kept, order preserved


def test_truncates_newest_first_and_preserves_order():
    history = [_msg("user", f"Question numero {i} ?") for i in range(5)]
    # Budget sized to fit exactly the last 3 messages
    budget = sum(_msg_tokens(m) for m in history[-3:])
    result = get_optimized_history(history, max_tokens=budget)
    assert result == history[-3:], f"Expected newest 3 messages, got: {result}"


def test_single_oversized_message_is_dropped():
    """A pasted wall of text must be excluded rather than blow the context window."""
    history = [_msg("user", "x" * 20000)]
    assert get_optimized_history(history, max_tokens=2000) == []
