import time
from src.rag.retrieve import retrieve_context

def test_retrieval_speed_and_content():
    """
    Tests only the ChromaDB retrieval. This should execute in under 1 second
    and cost zero API tokens.
    """
    query = "Quelles sont les conditions du régime d'autoconsommation ?"
    
    # 1. WARM-UP RUN (Not timed)
    # This forces the embedding model to initialize its memory buffers.
    _ = retrieve_context("dummy query", n_results=1)

    # 2. ACTUAL TEST (Timed)
    start_time = time.time()
    context = retrieve_context(query, n_results=5)
    end_time = time.time()
    
    execution_time = end_time - start_time
    
    assert isinstance(context, str), "The retrieved context must be a string."
    assert len(context) > 0, "The database returned an empty context."
    assert execution_time < 1.0, f"Retrieval was too slow! Took {execution_time:.3f} seconds."
    
    print(f"\n[Success] Retrieved {len(context)} characters of context in {execution_time:.3f} seconds.")