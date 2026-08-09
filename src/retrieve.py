import os
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv

# ==========================================
# 1. Configuration & Initialization
# ==========================================

load_dotenv()

API_KEY = os.getenv("CUSTOM_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)

CHROMA_PATH = "data/chroma_db"

print("Loading local embedding model (from local disk cache)...")
emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)

print("Connecting to local ChromaDB...")
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

# Open collection WITHOUT the embedding function to bypass the AttributeError
collection = chroma_client.get_collection(
    name="tunisia_energy_rag"
)

# ==========================================
# 2. Retrieval & Generation Pipeline
# ==========================================

def retrieve_and_generate(user_query: str):
    print(f"\n[1] Searching database for: '{user_query}'...")
    
    # Generate the mathematical vectors for the query
    raw_query_vectors = emb_fn([user_query])
    
    # Force convert the NumPy arrays into pure Python lists of floats
    # This prevents ChromaDB from throwing the ValueError
    formatted_query_vectors = [
        vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
        for vector in raw_query_vectors
    ]
    
    # Search using the manually formatted vectors
    results = collection.query(
        query_embeddings=formatted_query_vectors,
        n_results=25
    )
    
    retrieved_chunks = results["documents"][0]
    context = "\n\n---\n\n".join(retrieved_chunks)
    
    print("[2] Synthesizing answer with Mistral Large...\n")
    
    system_prompt = (
        "You are an expert AI assistant specializing in the Tunisian energy sector. "
        "Use ONLY the following context to answer the user's question. "
        "If the answer is not contained in the context, say 'I do not have enough information to answer that based on the provided documents.' "
        "Do not hallucinate or use outside knowledge. Answer in the same language as the user's query.\n\n"
        f"Context:\n{context}"
    )

    response = client.chat.completions.create(
        model="mistral-large",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
        temperature=0.1
    )
    
    return response.choices[0].message.content

# ==========================================
# 3. Execution
# ==========================================

if __name__ == "__main__":
    test_query = "quel sont les activitee necessaire pour conserver l'utilisation de l'eau?"
    
    answer = retrieve_and_generate(test_query)
    
    print("========== FINAL ANSWER ==========\n")
    print(answer)
    print("\n==================================")