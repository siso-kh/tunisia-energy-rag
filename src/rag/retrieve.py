import os
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Optional

# ==========================================
# 1. Configuration & Initialization
# ==========================================

load_dotenv()
API_KEY = os.getenv("CUSTOM_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
CHROMA_PATH = "data/chroma_db"

emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection(name="tunisia_energy_rag")

# ==========================================
# 2. Modular Pipeline Functions
# ==========================================

def rewrite_query_with_history(user_query: str, chat_history: List[Dict[str, str]]) -> str:
    """
    Reformulates a follow-up user query into a standalone search query using chat context.
    """
    if not chat_history:
        return user_query

    # Format recent history into a compact string
    formatted_history = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-4:]])
    
    prompt = f"""Given the following conversation history and a follow-up question, rephrase the follow-up question to be a self-contained search query.
Do NOT answer the question, only rephrase it to include necessary entities from context.

History:
{formatted_history}

Follow-up Question: {user_query}
Standalone Query:"""

    # Direct LLM call to contextualize the prompt
    response = client.chat.completions.create(
        model="mistral-large",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    
    return response.choices[0].message.content.strip()


def retrieve_context(user_query: str, n_results: int = 5) -> str:
    """Fetches chunks from ChromaDB and formats them into a single string."""
    raw_query_vectors = emb_fn([user_query])
    
    formatted_query_vectors = [
        vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
        for vector in raw_query_vectors
    ]
    
    results = collection.query(
        query_embeddings=formatted_query_vectors,
        n_results=n_results
    )
    
    retrieved_chunks = results["documents"][0]
    return "\n\n---\n\n".join(retrieved_chunks)


from typing import List, Dict, Any

def retrieve_context_structured(user_query: str, n_results: int = 5) -> List[Dict[str, Any]]:
    """Fetches chunks from ChromaDB along with metadata (source, page, etc.)."""
    raw_query_vectors = emb_fn([user_query])
    
    formatted_query_vectors = [
        vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
        for vector in raw_query_vectors
    ]
    
    results = collection.query(
        query_embeddings=formatted_query_vectors,
        n_results=n_results,
        include=["documents", "metadatas"]
    )
    
    documents = results["documents"][0]
    metadatas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else [{}] * len(documents)
    
    structured_sources = []
    for doc, meta in zip(documents, metadatas):
        structured_sources.append({
            "content": doc,
            "source_name": meta.get("source", meta.get("file_name", "Unknown Document")),
            "page": meta.get("page", meta.get("page_number", "N/A"))
        })
        
    return structured_sources

def format_sources_for_prompt(sources: List[Dict[str, Any]]) -> str:
    """Helper function to format structured sources into a clean prompt context string."""
    formatted_chunks = []
    for idx, src in enumerate(sources, 1):
        formatted_chunks.append(
            f"[Doc {idx} - Source: {src['source_name']} (Page {src['page']})]\n{src['content']}"
        )
    return "\n\n---\n\n".join(formatted_chunks)

def generate_answer(user_query: str, context: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    """Takes pre-fetched context and optional chat history to query the LLM."""
    system_prompt = (
        "You are an expert AI assistant specializing in the Tunisian energy sector. "
        "Use ONLY the following context to answer the user's question. "
        "If the answer is not contained in the context, say 'I do not have enough information to answer that based on the provided documents.' "
        "Do not hallucinate or use outside knowledge. Answer in the same language as the user's query.\n\n"
        f"Context:\n{context}"
    )

    # Build full message chain including past conversation
    messages = [{"role": "system", "content": system_prompt}]
    
    if chat_history:
        for msg in chat_history[-4:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
    messages.append({"role": "user", "content": user_query})

    response = client.chat.completions.create(
        model="mistral-large",
        messages=messages,
        temperature=0.1
    )
    
    return response.choices[0].message.content


def run_pipeline(user_query: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    """Executes the full Retrieval-Augmented Generation flow with conversational memory."""
    if chat_history is None:
        chat_history = []

    # Step 1: Contextualize query if history exists
    print("\n[1] Contextualizing query...")
    standalone_query = rewrite_query_with_history(user_query, chat_history)
    print(f"    Standalone query: '{standalone_query}'")
    
    # Step 2: Vector search with standalone query
    print("[2] Searching database...")
    structured_sources = retrieve_context_structured(standalone_query, n_results=5)
    context_str = format_sources_for_prompt(structured_sources)
    
    # Step 3: Synthesize answer with full chat history
    print("[3] Synthesizing answer with Mistral Large...\n")
    return generate_answer(user_query, context_str, chat_history)
# ==========================================
# 3. Execution Example
# ==========================================

if __name__ == "__main__":
    # Simulated conversation history
    sample_history = [
        {"role": "user", "content": "Quel est le rôle de l'ANME ?"},
        {"role": "assistant", "content": "L'ANME conçoit et met en œuvre la politique de l'État en matière de maîtrise de l'énergie."}
    ]
    
    # Follow-up question relying on prior context ("leur" -> ANME)
    follow_up_query = "Qui gère leur budget ?"
    
    answer = run_pipeline(follow_up_query, chat_history=sample_history)
    print("========== FINAL ANSWER ==========\n")
    print(answer)