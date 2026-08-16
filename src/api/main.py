import asyncio
import hmac
import json
import logging
import os
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user_optional, router as auth_router
from src.database import service
from src.database.connection import AsyncSessionLocal, engine, ensure_user_columns, get_db_dependency
from src.database.models import ReportStatus, User, UtilityType
from src.rag.retrieve import run_pipeline, stream_pipeline

logger = logging.getLogger(__name__)

# Outage report TTL: crowdsourced reports are purged once they are older than
# OUTAGE_TTL_HOURS. The cleanup task runs every OUTAGE_PURGE_INTERVAL_MINUTES.
# These are the *environment defaults*; an admin can override them at runtime
# via the settings table (see /api/admin/config).
OUTAGE_TTL_HOURS = float(os.getenv("OUTAGE_TTL_HOURS", "5"))
OUTAGE_PURGE_INTERVAL_MINUTES = float(os.getenv("OUTAGE_PURGE_INTERVAL_MINUTES", "30"))

# Admin endpoints (purge stats / manual purge) require this API key via the
# X-Admin-Key header. When unset, admin endpoints refuse to run (safer than a
# default password). Set ADMIN_API_KEY in .env / the environment.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# In-memory history of purge runs (timestamp, count, outcome). Survives only
# as long as the process -- fine for an admin "what happened recently" view.
# The deque holds the most recent PURGE_HISTORY_SIZE runs.
PURGE_HISTORY_SIZE = 50
PURGE_HISTORY: deque = deque(maxlen=PURGE_HISTORY_SIZE)
PURGE_TOTAL_DELETED = 0
PURGE_TOTAL_RUNS = 0


async def _effective_ttl_hours(session) -> float:
    """TTL as configured at runtime (DB setting, else env default)."""
    value = await service.get_setting(session, "outage_ttl_hours")
    return float(value or OUTAGE_TTL_HOURS)


async def _effective_purge_interval(session) -> float:
    """Purge interval in minutes (DB setting, else env default)."""
    value = await service.get_setting(session, "outage_purge_interval_minutes")
    return float(value or OUTAGE_PURGE_INTERVAL_MINUTES)


async def _purge_expired_outages() -> int:
    """Delete outage reports older than the configured TTL (best-effort).

    Reads the effective TTL from the runtime settings (admin-editable),
    falling back to the environment default. Records the outcome in
    ``PURGE_HISTORY`` so admins can inspect recent runs via
    ``GET /api/admin/purge-stats``. Returns the number of reports deleted
    (0 on failure -- the error is logged and visible in the history entry).
    """
    global PURGE_TOTAL_DELETED, PURGE_TOTAL_RUNS
    deleted = 0
    error = None
    try:
        async with AsyncSessionLocal() as session:
            ttl_hours = await _effective_ttl_hours(session)
            deleted = await service.delete_expired_outages(
                session, timedelta(hours=ttl_hours)
            )
            if deleted:
                logger.info("Purged %d expired outage reports", deleted)
    except Exception as e:
        error = str(e)
        logger.exception("Outage purge task failed (will retry on next tick)")

    PURGE_TOTAL_RUNS += 1
    PURGE_TOTAL_DELETED += deleted
    PURGE_HISTORY.appendleft(
        {
            "run": PURGE_TOTAL_RUNS,
            "at": datetime.now(timezone.utc).isoformat(),
            "deleted": deleted,
            "error": error,
        }
    )
    return deleted


async def _outage_cleanup_loop() -> None:
    """Background loop: purge expired outage reports on an interval.

    The interval is read from runtime settings on every tick so admin config
    changes take effect without a restart.
    """
    while True:
        await _purge_expired_outages()
        interval_minutes = OUTAGE_PURGE_INTERVAL_MINUTES
        try:
            async with AsyncSessionLocal() as session:
                interval_minutes = await _effective_purge_interval(session)
        except Exception:
            pass  # keep the env default on DB hiccups
        await asyncio.sleep(interval_minutes * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort schema compatibility: add auth columns to pre-auth DBs.
    try:
        await ensure_user_columns(engine)
    except Exception:
        logger.exception("Schema compatibility check failed (continuing)")

    task = asyncio.create_task(_outage_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Tunisia Energy RAG API", lifespan=lifespan)
app.include_router(auth_router)

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


class PurgeRunOut(BaseModel):
    run: int
    at: str
    deleted: int
    error: Optional[str] = None


class PurgeStatsOut(BaseModel):
    ttl_hours: float
    purge_interval_minutes: float
    total_runs: int
    total_deleted: int
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    recent_runs: List[PurgeRunOut]


class ConfigUpdate(BaseModel):
    """One or more settings to upsert (values are strings, like the env vars)."""
    settings: Dict[str, str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def require_admin_key(x_admin_key: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency guarding the admin endpoints.

    Requires the ``X-Admin-Key`` header to match ``ADMIN_API_KEY``. If the
    server has no key configured, admin access is refused entirely rather
    than falling back to a weak default.

    The comparison is constant-time (``hmac.compare_digest``) so response
    timing cannot be used to brute-force the key. An empty/whitespace-only
    header is always rejected.
    """
    if not ADMIN_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Admin API is not configured (set ADMIN_API_KEY).",
        )
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key.")
    if not hmac.compare_digest(x_admin_key.encode(), ADMIN_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin API key.")


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


async def _resolve_conversation(session, conversation_id: Optional[uuid.UUID], user: Optional[User] = None):
    """Return an existing conversation id or create a fresh one.

    Authenticated users own their conversations; anonymous sessions fall back
    to the shared demo user so chat keeps working without an account.
    """
    if conversation_id is not None:
        conversation = await service.get_conversation(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return conversation_id
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    conversation = await service.create_conversation(session, owner.id)
    return conversation.id


# ---------------------------------------------------------------------------
# Chat (non-streaming, kept for clients that want a plain JSON response)
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=QueryResponse)
async def chat_endpoint(
    request: QueryRequest,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
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
            resolved = await _resolve_conversation(session, parsed_cid, user)
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
async def chat_stream_endpoint(
    request: QueryRequest,
    user: Optional[User] = Depends(get_current_user_optional),
):
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
                conversation_id = await _resolve_conversation(session, parsed_cid, user)
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
async def list_conversations(
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    return await service.list_conversations(session, owner.id)


@app.post("/api/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    return await service.create_conversation(session, owner.id, payload.title)


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
async def create_outage(
    payload: OutageCreate,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    return await service.create_outage(
        session,
        utility=payload.utility,
        region=payload.region.strip(),
        latitude=payload.latitude,
        longitude=payload.longitude,
        description=payload.description,
        user_id=user.id if user else None,
    )


@app.patch("/api/outages/{outage_id}/status", response_model=OutageReportOut)
async def update_outage_status(
    outage_id: uuid.UUID, payload: OutageStatusUpdate, session=Depends(get_db_dependency)
):
    report = await service.update_outage_status(session, outage_id, payload.status)
    if report is None:
        raise HTTPException(status_code=404, detail="Outage report not found.")
    return report


# ---------------------------------------------------------------------------
# Admin: outage purge stats
# ---------------------------------------------------------------------------

@app.get("/api/admin/purge-stats", response_model=PurgeStatsOut)
async def purge_stats(
    _: None = Depends(require_admin_key), session=Depends(get_db_dependency)
):
    """Recent outage purge activity: how many reports were deleted and when."""
    ttl_hours = await _effective_ttl_hours(session)
    interval_minutes = await _effective_purge_interval(session)
    last_run = PURGE_HISTORY[0] if PURGE_HISTORY else None
    return PurgeStatsOut(
        ttl_hours=ttl_hours,
        purge_interval_minutes=interval_minutes,
        total_runs=PURGE_TOTAL_RUNS,
        total_deleted=PURGE_TOTAL_DELETED,
        last_run_at=last_run["at"] if last_run else None,
        next_run_at=(
            datetime.now(timezone.utc)
            + timedelta(minutes=interval_minutes)
        ).isoformat()
        if last_run
        else None,
        recent_runs=[PurgeRunOut(**entry) for entry in PURGE_HISTORY],
    )


@app.post("/api/admin/purge")
async def run_purge_now(_: None = Depends(require_admin_key)):
    """Trigger an immediate purge (useful for ops/testing)."""
    deleted = await _purge_expired_outages()
    return {"status": "ok", "deleted": deleted}


@app.get("/api/admin/config")
async def get_admin_config(
    _: None = Depends(require_admin_key), session=Depends(get_db_dependency)
):
    """All runtime settings (DB overrides + env defaults)."""
    return await service.get_all_settings(session)


@app.get("/api/config")
async def get_public_config(session=Depends(get_db_dependency)):
    """Public subset of runtime settings the dashboard needs (no admin key).

    Kept minimal on purpose: only keys that tune client behaviour without
    exposing anything sensitive.
    """
    all_settings = await service.get_all_settings(session)
    return {"map_refresh_seconds": all_settings.get("map_refresh_seconds", "60")}


@app.put("/api/admin/config")
async def update_admin_config(
    payload: ConfigUpdate,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Upsert runtime settings. Only known keys are accepted."""
    unknown = [k for k in payload.settings if k not in service.DEFAULT_SETTINGS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown settings: {', '.join(sorted(unknown))}",
        )
    for key, value in payload.settings.items():
        await service.set_setting(session, key, value)
    return await service.get_all_settings(session)


@app.get("/health")
def health_check():
    """Simple endpoint to verify the server is running."""
    return {"status": "healthy", "api": "online"}