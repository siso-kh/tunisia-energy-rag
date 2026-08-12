from src.rag.retrieve import retrieve_context, generate_answer
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.rag.retrieve import (
    retrieve_context_structured, 
    format_sources_for_prompt, 
    generate_answer,
    rewrite_query_with_history
)

app = FastAPI(title="Tunisia Energy RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatMessage(BaseModel):
    role: str
    content: str

class QueryRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatMessage]] = []

class SourceItem(BaseModel):
    source_name: str
    page: Any
    content: str

class QueryResponse(BaseModel):
    query: str
    sources: List[SourceItem]
    answer: str

@app.post("/api/chat", response_model=QueryResponse)
def chat_endpoint(request: QueryRequest):
    try:
        user_query = request.query.strip()
        if not user_query:
            raise HTTPException(status_code=400, detail="Query cannot be empty.")
        
        # 1. Rewrite query if history exists
        history_dicts = [{"role": m.role, "content": m.content} for m in request.chat_history]
        standalone_query = rewrite_query_with_history(user_query, history_dicts)
        
        # 2. Retrieve structured sources
        structured_sources = retrieve_context_structured(standalone_query, n_results=5)
        
        # 3. Format context string for LLM prompt
        context_str = format_sources_for_prompt(structured_sources)
        
        # 4. Generate answer with LLM
        answer = generate_answer(user_query, context_str, history_dicts)
        
        return QueryResponse(
            query=user_query,
            sources=structured_sources,
            answer=answer
        )
    except HTTPException:
        raise 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/health")
def health_check():
    """Simple endpoint to verify the server is running."""
    return {"status": "healthy", "api": "online"}