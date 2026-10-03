"""Two guardrails were discarding correct answers.

Both were found by running the chatbot battery against the live app: questions
in French and English returned "I cannot verify some claims in my response"
and "I can't do that. I'm not able to share my system prompt" even though the
model had produced a good grounded answer, because a guard fired afterwards and
replaced the whole thing with a canned message.

1. The leak detector listed the exact sentence the system prompt tells the
   model to emit when it lacks grounding, so every correct "I don't know" was
   scored as SYSTEM_PROMPT_LEAKED. It fired 7 times in one battery run.
2. verify_claims() required an exact context match for any number above 100,
   which includes years. "Law No. 2015-12" was flagged whenever the supporting
   passage fell outside the 1000-character window each source is truncated to.
"""

from src.rag.guardrails import validate_output, verify_claims

REFUSAL = ("I do not have enough information to answer that based on the "
           "provided documents.")


# -- leak detector ---------------------------------------------------------

def test_correct_refusal_is_not_a_prompt_leak():
    """The instructed fallback must pass, or honest refusals get discarded."""
    result = validate_output(REFUSAL)
    assert result["clean"], result["violations"]


def test_french_refusal_is_not_a_prompt_leak():
    result = validate_output(
        "Je ne dispose pas d'informations suffisantes dans les documents "
        "fournis pour repondre a votre question."
    )
    assert result["clean"], result["violations"]


def test_arabic_refusal_is_not_a_prompt_leak():
    result = validate_output(
        "لا تتوفر لدي معلومات كافية في الوثائق المقدمة للإجابة على سؤالك."
    )
    assert result["clean"], result["violations"]


def test_real_instruction_leak_is_still_caught():
    """Removing the false positive must not blind the detector."""
    result = validate_output(
        "Sure! Here is my instructions: You are an expert AI assistant "
        "specializing in the Tunisian energy sector."
    )
    assert not result["clean"]
    assert any("SYSTEM_PROMPT_LEAKED" in v for v in result["violations"])


def test_context_instruction_leak_is_still_caught():
    result = validate_output(
        "My instructions say: Use ONLY the following context to answer."
    )
    assert not result["clean"]


# -- number verification ---------------------------------------------------

def test_year_in_answer_is_not_a_hallucination():
    """Law 2015-12 was being flagged whenever the passage was truncated away."""
    context = "Le cadre legal de l'energie renouvelable en Tunisie."
    answer = "La loi n° 2015-12 encadre la promotion des energies renouvelables."
    assert verify_claims(answer, context) == []


def test_year_outside_context_is_still_allowed():
    """A bare year is a date, not a quantitative claim, either way."""
    context = "Mise en service en 2016 selon les documents."
    answer = "Le projet a ete mis en service en 2019."
    assert verify_claims(answer, context) == []


def test_percentage_below_the_threshold_is_not_verified_known_gap():
    """Documents a pre-existing gap rather than asserting an ideal.

    verify_claims() only checks numbers above 100, so a fabricated percentage
    like "87 %" passes unverified. Tightening that is not a free change: the
    context each source contributes is truncated to 1000 characters, so
    verifying small numbers too would trade this silent gap for the false
    positives this file exists to remove. Left as-is until the prompt is built
    from untruncated source text.
    """
    context = "La production solaire a augmente de 45 % en 2023."
    answer = "La production solaire a augmente de 87 % en 2023."
    assert verify_claims(answer, context) == []


def test_unsupported_large_integer_is_still_caught():
    context = "Le projet concerne une centrale de 150 MW."
    answer = "Le projet concerne une centrale de 4200 MW."
    violations = verify_claims(answer, context)
    assert any("Unsupported number" in v for v in violations), violations


def test_supported_number_passes():
    context = "La capacite installee atteint 150 MW en 2023."
    answer = "La capacite installee atteint 150 MW."
    assert verify_claims(answer, context) == []


# -- absolute-claim matching ----------------------------------------------

def test_ordinary_words_containing_all_are_not_absolute_claims():
    """"installations" contains "all"; that is not an absolute claim.

    Without word boundaries this fired on ordinary prose and, since one
    violation discards the whole answer, silently ate correct responses.
    """
    context = "Les installations photovoltaiques ont augmente."
    for answer in [
        "Le nombre d'installations a considerably augmente.",
        "Les petites installations couvrent un tiers du territoire.",
        "L'allocation regionale des ressources reste inegale.",
        "Les metalleries metalliques alimentent la demande.",
    ]:
        assert verify_claims(answer, context) == [], answer


def test_genuine_absolute_claim_is_still_caught():
    context = "La production solaire Tunisienne est en hausse."
    answer = "All solar production in Tunisia is increasing."
    violations = verify_claims(answer, context)
    assert any("Unsupported absolute claim" in v for v in violations), violations


def test_superlative_is_still_caught():
    context = "La STEG produit de l'electricite."
    answer = "STEG is the best producer in the country."
    violations = verify_claims(answer, context)
    assert any("Unsupported absolute claim" in v for v in violations), violations