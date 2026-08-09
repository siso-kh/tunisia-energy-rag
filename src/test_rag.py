import os
import json
import time
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

print("Loading local embedding model...")
emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)

print("Connecting to local ChromaDB...")
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection(name="tunisia_energy_rag")

# ==========================================
# 2. Test Cases (10 Diverse Scenarios)
# ==========================================

TEST_SUITE = [
    {
        "id": 1,
        "category": "Direct Factual / Targets",
        "question": "Quel est l'objectif de la Tunisie concernant la part des énergies renouvelables dans la production d'électricité à l'horizon 2030 ?"
    },
    {
        "id": 2,
        "category": "Regulatory / Autoconsommation",
        "question": "Quelles sont les conditions réglementaires requises pour bénéficier du régime d'autoconsommation selon la loi n°2015-12 ?"
    },
    {
        "id": 3,
        "category": "Financial Incentives / FTE",
        "question": "Comment le Fonds de Transition Énergétique (FTE) est-il financé et quels types de primes accorde-t-il ?"
    },
    {
        "id": 4,
        "category": "Institutional Roles",
        "question": "Quels sont les rôles respectifs de l'ANME et de la STEG dans la gestion et le raccordement des projets d'énergies renouvelables ?"
    },
    {
        "id": 5,
        "category": "Technical Compliance / Energy Audits",
        "question": "Quelles sont les obligations légales relatives aux audits énergétiques pour les établissements industriels et le secteur tertiaire ?"
    },
    {
        "id": 6,
        "category": "Concessions & Licensing",
        "question": "Quelle est la procédure d'octroi des concessions pour les grands projets de production d'électricité renouvelable ?"
    },
    {
        "id": 7,
        "category": "Building Thermal Regulations",
        "question": "Que prévoit la réglementation thermique des bâtiments neufs (RTBNT) en Tunisie ?"
    },
    {
        "id": 8,
        "category": "Trap / Out-of-Scope (Hallucination Check)",
        "question": "Quelles sont les subventions accordées par l'ANME pour l'achat de véhicules électriques individuels importés ?"
    },
    {
        "id": 9,
        "category": "Solar Thermal / PROSOL",
        "question": "Comment fonctionne le programme PROSOL pour la promotion des chauffe-eau solaires en Tunisie ?"
    },
    {
        "id": 10,
        "category": "Sanctions & Compliance",
        "question": "Quelles sont les pénalités prévues par le cadre réglementaire en cas de non-respect des obligations de maîtrise de l'énergie ?"
    }
]

# ==========================================
# 3. Test Runner
# ==========================================

def run_test_suite(output_file="test_results.json", n_results=20):
    results_log = []
    
    print(f"\nStarting test execution over {len(TEST_SUITE)} queries (Fetching Top {n_results} Chunks)...")
    print("=" * 60)

    for idx, test_case in enumerate(TEST_SUITE, 1):
        q_id = test_case["id"]
        category = test_case["category"]
        query = test_case["question"]
        
        print(f"[{idx}/10] Testing [{category}]...")
        start_time = time.time()
        
        # Step 1: Embedding generation & vector format normalization
        raw_query_vectors = emb_fn([query])
        formatted_vectors = [
            vector.tolist() if hasattr(vector, 'tolist') else list(vector) 
            for vector in raw_query_vectors
        ]
        
        # Step 2: Vector Search
        search_start = time.time()
        chroma_results = collection.query(
            query_embeddings=formatted_vectors,
            n_results=n_results
        )
        search_latency = round(time.time() - search_start, 3)
        
        retrieved_documents = chroma_results["documents"][0]
        context_text = "\n\n---\n\n".join(retrieved_documents)
        
        # Step 3: LLM Synthesis
        llm_start = time.time()
        system_prompt = (
            "You are an expert AI assistant specializing in the Tunisian energy sector. "
            "Use ONLY the following context to answer the user's question. "
            "If the answer is not contained in the context, say 'I do not have enough information to answer that based on the provided documents.' "
            "Do not hallucinate or use outside knowledge. Answer in the same language as the user's query.\n\n"
            f"Context:\n{context_text}"
        )

        try:
            response = client.chat.completions.create(
                model="mistral-large",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                temperature=0.1
            )
            model_answer = response.choices[0].message.content
        except Exception as e:
            model_answer = f"ERROR: LLM request failed with exception: {str(e)}"

        llm_latency = round(time.time() - llm_start, 3)
        total_latency = round(time.time() - start_time, 3)
        
        # Structure result record
        test_record = {
            "test_id": q_id,
            "category": category,
            "question": query,
            "response": model_answer,
            "metrics": {
                "total_latency_seconds": total_latency,
                "vector_search_seconds": search_latency,
                "llm_generation_seconds": llm_latency,
                "retrieved_chunks_count": len(retrieved_documents),
                "context_char_length": len(context_text)
            },
            "retrieved_context_chunks": retrieved_documents
        }
        
        results_log.append(test_record)
        print(f" -> Completed in {total_latency}s (Search: {search_latency}s | LLM: {llm_latency}s)\n")

    # Step 4: Export to JSON
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results_log, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"Test suite execution complete! All results exported to '{output_file}'.")

if __name__ == "__main__":
    run_test_suite()