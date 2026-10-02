"""Meta-test: prove the new regression tests actually fail when a fix is reverted.

A regression test that cannot fail guards nothing. This mutates each production
behaviour back to its broken form and asserts that the corresponding suite goes
red. It is skipped by default (it is a meta-check, not a product test) and run
explicitly with:

    MUTATION_CHECK=1 python -m pytest tests/test_mutation_guards.py -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MUTATION_CHECK = os.environ.get("MUTATION_CHECK")

# (test file that must catch it, source edit that reintroduces the bug)
MUTATIONS = [
    (
        "tests/test_llm_pool.py",
        "src/rag/retrieve.py",
        "self._disabled.add(model)",
        "pass  # mutation: 404 no longer disables",
        "404 permanent disable removed",
    ),
    (
        "tests/test_retrieval_caching.py",
        "src/rag/hybrid.py",
        "@lru_cache(maxsize=1)\ndef _get_corpus():",
        "def _get_corpus():",
        "corpus cache removal",
    ),
    (
        "tests/test_triage_scoring.py",
        "src/utils/triage.py",
        '    raise TriageModelError(f"all triage models failed for gate 1: {last_exc}")',
        '    return 50.0',
        "gate1 silent fallback restored",
    ),
    (
        "tests/test_query_rewrite.py",
        "src/rag/retrieve.py",
        'r"|\\.\\.\\.",',
        'r"|\\.\\.\\.|",',
        "empty regex alternative restored",
    ),
    (
        "tests/test_indexer_chunking.py",
        "src/ingestion/indexer.py",
        "                        clean_text = raw_text.strip()",
        "                        clean_text = ''  # mutation: page dropped",
        "OCR text fallback removed",
    ),
    (
        "tests/test_llm_pool.py",
        "src/rag/retrieve.py",
        "        return isinstance(exc, APIError)",
        "        return False  # mutation: router rejections not retried",
        "transient retry disabled",
    ),
]


def _run_suite(suite):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "--no-header", "-x"],
        cwd=str(REPO), capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


@pytest.mark.skipif(
    not MUTATION_CHECK,
    reason="meta-check; run explicitly with MUTATION_CHECK=1",
)
@pytest.mark.parametrize("suite,source,old,new,label", MUTATIONS,
                         ids=[m[4] for m in MUTATIONS])
def test_suite_catches_the_reverted_fix(suite, source, old, new, label):
    path = REPO / source
    original = path.read_text(encoding="utf-8")
    assert old in original, "mutation anchor not found in %s: %r" % (source, old[:60])

    try:
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        assert _run_suite(suite) != 0, (
            "%s still passed with the fix reverted -- it guards nothing" % label
        )
    finally:
        path.write_text(original, encoding="utf-8")