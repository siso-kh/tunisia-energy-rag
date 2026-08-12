"""Token budget utilities for chat history management.

A fixed message count (e.g. ``chat_history[-4:]``) is dangerous in a RAG
pipeline: a user pasting a massive block of text can consume the entire LLM
context window, leaving no room for the retrieved ChromaDB documents or the
system prompt (resulting in 400 Bad Request errors). This module replaces the
fixed count with a dynamic token-budget strategy.
"""

import tiktoken
from typing import List, Dict

# cl100k_base is a fast proxy for modern LLM tokenization
tokenizer = tiktoken.get_encoding("cl100k_base")


def get_optimized_history(chat_history: List[Dict[str, str]], max_tokens: int = 2000) -> List[Dict[str, str]]:
    """
    Dynamically truncates conversation history to stay within a strict token budget.

    Iterates from newest to oldest (prioritizing recent context) and stops as soon
    as adding the next message would exceed ``max_tokens``. The returned list
    preserves chronological order (oldest -> newest).

    Args:
        chat_history: List of messages as {"role": ..., "content": ...}.
        max_tokens: Maximum total tokens allowed for the returned history.

    Returns:
        The token-budgeted history in chronological order (possibly empty).

    Note:
        If a single message (e.g. a pasted wall of text) exceeds ``max_tokens``,
        it is deliberately excluded and the function may return an empty history:
        protecting the context window takes precedence over keeping context.
    """
    if not chat_history:
        return []

    selected_history = []
    current_tokens = 0

    # Iterate from newest to oldest to prioritize recent context
    for msg in reversed(chat_history):
        # Calculate tokens (adding a small buffer for structural formatting)
        msg_content = f"{msg['role']}: {msg['content']}"
        msg_tokens = len(tokenizer.encode(msg_content)) + 4

        # If adding this message exceeds our budget, stop.
        if current_tokens + msg_tokens > max_tokens:
            break

        # Insert at the beginning to maintain chronological order
        selected_history.insert(0, msg)
        current_tokens += msg_tokens

    return selected_history
