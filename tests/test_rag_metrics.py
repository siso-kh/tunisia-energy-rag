import time
import json
from src.rag.retrieve import run_pipeline # Assuming run_pipeline is available

def run_evaluation_sprint():
    """
    Executes a batch testing sprint across 5 core evaluation categories 
    to measure the accuracy and robustness of the RAG MVP.
    """
    
    # Define benchmark queries for each category
    test_cases = {
        "Direct Factual": "Quel est l'objectif de la Tunisie pour les énergies renouvelables d'ici 2030 ?",
        "Regulatory": "Quelles sont les conditions du régime d'autoconsommation ?",
        "Financial": "Quelles sont les subventions disponibles pour les projets solaires ?",
        "Institutional": "Quel est le rôle de l'ANME et de la STEG ?",
        "Technical Compliance": "Quelles sont les normes techniques pour le raccordement au réseau basse tension ?",
        "Out of Scope (Guardrail Test)": "Comment préparer le couscous tunisien ?"
    }
    
    results = {}
    
    print("Starting automated evaluation sprint...\n")
    
    for category, query in test_cases.items():
        print(f"Testing [{category}]...")
        start_time = time.time()
        
        try:
            # You can also use the requests library here to test the API endpoint directly
            # instead of the underlying function, depending on your preference.
            answer = run_pipeline(query) 
            status = "Success"
        except Exception as e:
            answer = str(e)
            status = "Failed"
            
        execution_time = time.time() - start_time
        
        results[category] = {
            "query": query,
            "answer": answer,
            "latency_seconds": round(execution_time, 2),
            "status": status
        }
        
    # Export results for review
    with open("test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    print("\nEvaluation complete. Results saved to 'test_results.json'.")

if __name__ == "__main__":
    run_evaluation_sprint()