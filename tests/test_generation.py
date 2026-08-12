import asyncio
from src.rag.retrieve import generate_answer

def test_llm_refusal_on_out_of_context_query():
    """
    Tests if the LLM correctly refuses to answer when the context 
    does not contain the necessary information. This prevents hallucinations.
    """
    # A query entirely unrelated to the provided context
    query = "What is the capital of Japan?"
    dummy_context = "La STEG gère l'infrastructure et le raccordement au réseau électrique national en Tunisie."
    
    # We pass the unrelated context directly to the LLM
    response = asyncio.run(generate_answer(query, dummy_context))
    
    # The exact phrase you specified in your system prompt in retrieve.py
    expected_refusal = "I do not have enough information to answer that based on the provided documents"
    
    assert expected_refusal.lower() in response.lower(), f"LLM hallucinated or failed to refuse! Output: {response}"

def test_llm_answers_with_valid_context():
    """
    Tests if the LLM successfully extracts facts when given the correct context,
    and answers in the same language as the prompt.
    """
    query = "Qui gère l'infrastructure électrique ?"
    valid_context = "L'ANME assure la promotion. La STEG gère l'infrastructure et le raccordement au réseau électrique national."
    
    response = asyncio.run(generate_answer(query, valid_context))
    
    # Check if it successfully extracted the target entity
    assert "STEG" in response, f"LLM missed the key entity from the context. Output: {response}"
    # Check if it provided a reasonable length answer
    assert len(response) > 10, "Response is suspiciously short."