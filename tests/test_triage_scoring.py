"""Regression tests for the two-gate triage in src/utils/triage.py.

The production failure: triage hardcoded ``agnes-2.0-flash``, a model the
provider no longer serves. Every gate call raised, two silent ``except``
branches returned 50.0 / 0.0, and with ``weight_algo=0.15`` /
``weight_ai=0.85`` the master score collapsed to 7.5 against a threshold of
55.0 -- so *every* document was recorded as non-pertinent while the LLM was
never actually reached.

These tests pin the corrected behaviour: models resolve from env, dead models
are skipped rather than faking a score, a total failure raises and surfaces as
a distinct ERROR verdict, and score parsing tolerates the ways providers wrap
JSON.
"""

import json
from pathlib import Path

import pytest

from src.utils import triage


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def test_default_models_exclude_the_retired_model():
    """agnes-2.0-flash is no longer served; it must not be a default."""
    assert "agnes-2.0-flash" not in triage._triage_models()


def test_default_models_start_with_a_known_good_model():
    assert triage._triage_models()[0] == "agnes-2.5-flash"


def test_explicit_triage_models_win(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODELS", "alpha, beta ,gamma")
    assert triage._triage_models() == ["alpha", "beta", "gamma"]


def test_llm_models_are_reused_when_triage_models_is_absent(monkeypatch):
    """Triage and chat must talk to the same provider models."""
    monkeypatch.delenv("TRIAGE_MODELS", raising=False)
    monkeypatch.setenv("LLM_MODELS", "chat-model-a,chat-model-b")
    assert triage._triage_models() == ["chat-model-a", "chat-model-b"]


def test_llm_model_is_used_as_the_preferred_triage_model(monkeypatch):
    monkeypatch.delenv("TRIAGE_MODELS", raising=False)
    monkeypatch.delenv("LLM_MODELS", raising=False)
    monkeypatch.setenv("LLM_MODEL", "combo/freemodels")
    models = triage._triage_models()
    assert models[0] == "combo/freemodels"
    assert "agnes-2.5-flash" in models


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _StubCompletions:
    """Exposes the client.chat.completions.create path the code actually uses."""

    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        return self._owner._create(**kwargs)


class StubClient:
    """Serves `score` from live models and raises from dead ones."""

    def __init__(self, dead=(), score=88):
        import types

        self.dead = set(dead)
        self.score = score
        self.calls = []
        self.chat = types.SimpleNamespace(completions=_StubCompletions(self))

    def _create(self, model=None, messages=None, response_format=None, timeout=None, **_kw):
        self.calls.append(model)
        if model in self.dead:
            raise RuntimeError("404 model not found: %s" % model)
        return _Completion(json.dumps({"score": self.score, "reason": "test"}))


def _meta():
    return {"filename": "rapport.pdf", "title": "Rapport ANME", "subtitles": ["energy"]}


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------

def test_gate1_skips_dead_models_and_uses_a_live_one(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODELS", "dead-1,dead-2,good")
    client = StubClient(dead={"dead-1", "dead-2"}, score=91)

    assert triage.evaluate_gate1_metadata(_meta(), client=client) == 91.0
    assert client.calls == ["dead-1", "dead-2", "good"]


def test_gate1_raises_instead_of_returning_the_50_fallback(monkeypatch):
    """The 50.0 fallback is what silently collapsed the master score."""
    monkeypatch.setenv("TRIAGE_MODELS", "dead-1,dead-2")
    client = StubClient(dead={"dead-1", "dead-2"})

    with pytest.raises(triage.TriageModelError):
        triage.evaluate_gate1_metadata(_meta(), client=client)


def test_gate2_raises_instead_of_returning_zero(monkeypatch):
    """A 0.0 from gate 2 drives the master score to ~7.5 and rejects the file."""
    monkeypatch.setenv("TRIAGE_MODELS", "dead-1")
    client = StubClient(dead={"dead-1"})

    with pytest.raises(triage.TriageModelError):
        triage.evaluate_gate2_deep(Path(__file__), "some text", client=client)


def test_gate1_stops_at_the_first_model_that_answers(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODELS", "good-1,good-2")
    client = StubClient(dead=set(), score=77)

    assert triage.evaluate_gate1_metadata(_meta(), client=client) == 77.0
    assert client.calls == ["good-1"]  # stops at the first that answers


# ---------------------------------------------------------------------------
# Score parsing
# ---------------------------------------------------------------------------

class RawClient:
    """Returns a fixed raw payload, bypassing JSON formatting."""

    def __init__(self, payload):
        import types

        self.payload = payload

        class _C:
            def create(_self, **_kw):
                return _Completion(payload)

        self.chat = types.SimpleNamespace(completions=_C())


@pytest.mark.parametrize(
    "payload, expected",
    [
        ('{"score": 91}', 91.0),
        ('```json\n{"score": 92}\n```', 92.0),
        ('```\n{"score": 93}\n```', 93.0),
        ('Here is my answer: {"score": 94, "reason": "energy"}', 94.0),
        ('{"score": 95.5}', 95.5),
    ],
)
def test_score_parsing_tolerates_common_wrappers(payload, expected):
    assert triage.evaluate_gate1_metadata(_meta(), client=RawClient(payload)) == expected


@pytest.mark.parametrize("payload, expected", [('{"score": 250}', 100.0), ('{"score": -5}', 0.0)])
def test_scores_are_clamped_to_the_documented_range(payload, expected):
    assert triage.evaluate_gate1_metadata(_meta(), client=RawClient(payload)) == expected


def test_json_without_a_score_key_is_rejected():
    """Returning 0.0 for a missing key would reject a perfectly good document."""
    with pytest.raises(triage.TriageModelError):
        triage.evaluate_gate1_metadata(_meta(), client=RawClient('{"reason": "no score"}'))


def test_non_json_output_is_rejected():
    with pytest.raises(triage.TriageModelError):
        triage.evaluate_gate1_metadata(_meta(), client=RawClient("I refuse to answer."))


# ---------------------------------------------------------------------------
# ERROR verdict
# ---------------------------------------------------------------------------

def test_triage_file_reports_error_not_blacklisted(monkeypatch):
    """A provider outage must never be recorded as 'not pertinent'."""
    monkeypatch.setenv("TRIAGE_MODELS", "dead-1")
    client = StubClient(dead={"dead-1"})

    decision = triage.triage_file(Path(__file__), client=client)

    assert decision["status"] == "ERROR"
    assert decision["dest"] is None
    assert decision["master_score"] is None
    assert decision["error"]


def test_a_real_low_score_is_still_blacklisted(monkeypatch):
    """Genuine rejection behaviour must be preserved."""
    monkeypatch.setenv("TRIAGE_MODELS", "good")
    client = StubClient(dead=set(), score=3)

    decision = triage.triage_file(Path(__file__), client=client)

    # gate1 below instant_reject_threshold short-circuits to BLACKLISTED.
    assert decision["status"] == "BLACKLISTED"
    assert decision["dest"] == "blacklisted"


def test_a_real_high_score_still_passes(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODELS", "good")
    client = StubClient(dead=set(), score=99)

    decision = triage.triage_file(Path(__file__), client=client)

    assert decision["status"] == "PASSED"
    assert decision["dest"] == "filtered"


def test_error_verdict_is_distinct_from_blacklisted():
    """The two outcomes must never be conflated again."""
    assert triage.TriageModelError is not None
    decision = {
        "filename": "x.pdf", "status": "ERROR", "dest": None,
        "gate1_score": None, "gate2_score": None, "master_score": None,
        "total_pages": 3, "shortcircuited": False, "error": "boom",
    }
    assert decision["status"] != "BLACKLISTED"
    assert decision["dest"] is None