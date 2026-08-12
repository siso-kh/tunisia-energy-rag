import logging
from typing import List, Optional, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.rag.retrieve import run_pipeline

logger = logging.getLogger(__name__)

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
async def chat_endpoint(request: QueryRequest):
    try:
        user_query = request.query.strip()
        if not user_query:
            raise HTTPException(status_code=400, detail="Query cannot be empty.")
        
        history_dicts = [{"role": m.role, "content": m.content} for m in request.chat_history]
        
        # Await the fully asynchronous pipeline (ChromaDB retrieval runs in a thread pool)
        answer, structured_sources = await run_pipeline(user_query, history_dicts)
        
        return QueryResponse(
            query=user_query,
            sources=structured_sources,
            answer=answer
        )
    except HTTPException:
        raise 
    except Exception as e:
        # Log the real error server-side, but keep the client response sanitized
        logger.exception("Chat endpoint failed: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred during processing.")
@app.get("/health")
def health_check():
    """Simple endpoint to verify the server is running."""
    return {"status": "healthy", "api": "online"}