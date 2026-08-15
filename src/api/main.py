import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.database import service
from src.database.connection import AsyncSessionLocal, get_db_dependency
from src.database.models import ReportStatus, UtilityType
from src.rag.retrieve import run_pipeline, stream_pipeline

logger = logging.getLogger(__name__)

app = FastAPI(title="Tunisia Energy RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatMessage]] = []
    conversation_id: Optional[str] = None


class SourceItem(BaseModel):
    """Unified citation shape: source_file matches Message.sources in the DB layer."""
    source_file: str
    page: Any
    content: str
    date: Optional[str] = None  # best-effort publication date parsed from the filename


class QueryResponse(BaseModel):
    query: str
    sources: List[SourceItem]
    answer: str
    conversation_id: Optional[str] = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sources: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ConversationDetailOut(BaseModel):
    id: uuid.UUID
    title: Optional[str] = None
    messages: List[MessageOut]


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)


class OutageReportOut(BaseModel):
    id: uuid.UUID
    utility: UtilityType
    region: str
    latitude: float
    longitude: float
    description: Optional[str] = None
    status: ReportStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OutageCreate(BaseModel):
    utility: UtilityType = UtilityType.STEG
    region: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    description: Optional[str] = None


class OutageStatusUpdate(BaseModel):
    status: ReportStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_conversation_id(raw: Optional[str]) -> Optional[uuid.UUID]:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation_id.")


def _sse(event: dict) -> str:
    """Serialize a pipeline event dict to one SSE data frame."""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def _resolve_conversation(session, conversation_id: Optional[uuid.UUID]):
    """Return an existing conversation id or create a fresh one for the demo user."""
    if conversation_id is not None:
        conversation = await service.get_conversation(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return conversation_id
    user = await service.get_or_create_demo_user(session)
    conversation = await service.create_conversation(session, user.id)
    return conversation.id


# ---------------------------------------------------------------------------
# Chat (non-streaming, kept for clients that want a plain JSON response)
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=QueryResponse)
async def chat_endpoint(request: QueryRequest, session=Depends(get_db_dependency)):
    try:
        user_query = request.query.strip()
        if not user_query:
            raise HTTPException(status_code=400, detail="Query cannot be empty.")

        history_dicts = [{"role": m.role, "content": m.content} for m in request.chat_history]

        # Await the fully asynchronous pipeline (ChromaDB retrieval runs in a thread pool)
        answer, structured_sources = await run_pipeline(user_query, history_dicts)

        # Persist the turn (best-effort: never let DB issues break the answer)
        conversation_id = None
        try:
            parsed_cid = _parse_conversation_id(request.conversation_id)
            resolved = await _resolve_conversation(session, parsed_cid)
            if resolved is not None:
                await service.persist_chat_turn(
                    session, resolved, user_query, answer, structured_sources
                )
                conversation_id = str(resolved)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Chat persistence skipped: %s", e)

        return QueryResponse(
            query=user_query,
            sources=structured_sources,
            answer=answer,
            conversation_id=conversation_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Log the real error server-side, but keep the client response sanitized
        logger.exception("Chat endpoint failed: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred during processing.")


# ---------------------------------------------------------------------------
# Chat (SSE streaming) - primary endpoint for the React frontend
# ---------------------------------------------------------------------------

@app.post("/api/chat/stream")
async def chat_stream_endpoint(request: QueryRequest):
    user_query = request.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    history_dicts = [{"role": m.role, "content": m.content} for m in request.chat_history]
    parsed_cid = _parse_conversation_id(request.conversation_id)

    async def event_generator():
        async with AsyncSessionLocal() as session:
            conversation_id: Optional[uuid.UUID] = None
            # Resolve/create the conversation up-front; if the DB is unavailable,
            # stream anyway without persistence.
            try:
                conversation_id = await _resolve_conversation(session, parsed_cid)
            except HTTPException as e:
                yield _sse({"type": "error", "message": e.detail})
                return
            except Exception as e:
                logger.exception("Conversation resolution skipped: %s", e)

            try:
                async for event in stream_pipeline(user_query, history_dicts):
                    if event["type"] == "done":
                        event["conversation_id"] = str(conversation_id) if conversation_id else None
                        if conversation_id is not None:
                            try:
                                await service.persist_chat_turn(
                                    session,
                                    conversation_id,
                                    user_query,
                                    event["answer"],
                                    event["sources"],
                                )
                            except Exception as e:
                                logger.exception("Chat persistence skipped: %s", e)
                    yield _sse(event)
            except Exception as e:
                logger.exception("Stream pipeline failed: %s", e)
                yield _sse({"type": "error", "message": "An internal error occurred during processing."})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@app.get("/api/conversations", response_model=List[ConversationOut])
async def list_conversations(session=Depends(get_db_dependency)):
    user = await service.get_or_create_demo_user(session)
    return await service.list_conversations(session, user.id)


@app.post("/api/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(payload: ConversationCreate, session=Depends(get_db_dependency)):
    user = await service.get_or_create_demo_user(session)
    return await service.create_conversation(session, user.id, payload.title)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(conversation_id: uuid.UUID, session=Depends(get_db_dependency)):
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        messages=[
            MessageOut.model_validate(m)
            for m in sorted(conversation.messages, key=lambda m: m.created_at)
        ],
    )


# ---------------------------------------------------------------------------
# Outage reports (crowdsourced map)
# ---------------------------------------------------------------------------

@app.get("/api/outages", response_model=List[OutageReportOut])
async def list_outages(status: Optional[ReportStatus] = None, session=Depends(get_db_dependency)):
    return await service.list_outages(session, status)


@app.post("/api/outages", response_model=OutageReportOut, status_code=201)
async def create_outage(payload: OutageCreate, session=Depends(get_db_dependency)):
    return await service.create_outage(
        session,
        utility=payload.utility,
        region=payload.region.strip(),
        latitude=payload.latitude,
        longitude=payload.longitude,
        description=payload.description,
    )


@app.patch("/api/outages/{outage_id}/status", response_model=OutageReportOut)
async def update_outage_status(
    outage_id: uuid.UUID, payload: OutageStatusUpdate, session=Depends(get_db_dependency)
):
    report = await service.update_outage_status(session, outage_id, payload.status)
    if report is None:
        raise HTTPException(status_code=404, detail="Outage report not found.")
    return report


@app.get("/health")
def health_check():
    """Simple endpoint to verify the server is running."""
    return {"status": "healthy", "api": "online"}