import asyncio
import hmac
import json
import logging
import os
import time
import uuid
import requests
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from src.api.auth import get_current_user_optional, router as auth_router
from src.api.dashboard import DASHBOARD_HTML
from src.api.logging_config import setup_logging
from src.api.metrics import (
    ACTIVE_REQUESTS,
    HTTP_DURATION,
    HTTP_REQUESTS,
    INGESTIONS,
    LLM_REQUESTS,
    LLM_TOKENS,
    PURGE_DELETED,
    PURGE_RUNS,
    normalize_path,
)
from src.api.ratelimit import (
    ADMIN_LIMIT,
    CHAT_LIMIT,
    OUTAGE_CREATE_LIMIT,
    configure_limiter,
    limiter,
)
from src.api.security import add_security_middlewares
from src.database import service
from src.database.connection import AsyncSessionLocal, get_db_dependency
from src.database.models import ReportStatus, Source, SourceStatus, User, UtilityType
from src.ingestion.admin_ingest import (
    MAX_UPLOAD_BYTES,
    download_pdf_from_url,
    ingest_pdf,
    is_valid_pdf,
    save_uploaded_pdf,
)
from src.ingestion.research import crawl_website_for_pdfs, download_pdf_from_source, ingest_downloaded_pdf
from src.rag.retrieve import collection, run_pipeline, stream_pipeline

setup_logging()
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
    PURGE_RUNS.inc()
    if deleted:
        PURGE_DELETED.inc(deleted)
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
    # The schema is owned by Alembic migrations (see src/database/schema.py);
    # the app no longer creates or alters tables at startup.
    #
    # Warm the retrieval stack BEFORE serving. Measured on the real 15k-document
    # index: the first query costs ~15s and pushes RSS from ~390 MB to ~1.3 GB,
    # because the sentence-transformer and the BM25 index are built inside the
    # request. On a 2 GB container that in-request allocation is what got the
    # process OOM-killed: the stream emitted "searching" and then died with no
    # traceback, no SSE error frame, and the next request answered 502.
    #
    # Warming is done inline (not in a threadpool worker) because nothing else
    # is running yet, so there is no concurrent torch use to race with. The
    # cross-encoder reranker is deliberately NOT warmed: it is disabled by
    # default and hybrid._load_cross_encoder() memoizes its attempt.
    if os.getenv("WARM_RETRIEVAL_ON_STARTUP", "true").lower() in ("1", "true", "yes"):
        try:
            await _warm_retrieval()
        except Exception:  # noqa: BLE001 - never block boot on an optional warm-up
            logger.warning("Retrieval warm-up failed; first query will be slower.",
                           exc_info=True)

    try:
        _log_startup_diagnostics()
    except Exception:  # noqa: BLE001 - diagnostics must never block boot
        logger.warning("Startup diagnostics failed", exc_info=True)

    task = asyncio.create_task(_outage_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _warm_retrieval() -> None:
    """Load the embedding model and BM25 index before the first request."""
    started = time.monotonic()
    from src.rag.hybrid import _get_bm25
    from src.rag.retrieve import _get_emb_fn

    # Build both inside the threadpool (blocking CPU work), sequentially so
    # their peaks do not overlap.
    await run_in_threadpool(_get_emb_fn)
    await run_in_threadpool(_get_bm25)
    logger.info("Retrieval warmed in %.1fs", time.monotonic() - started)


def _log_startup_diagnostics() -> None:
    """Log the knobs that decide whether a live query can succeed at all.

    Render gives us no shell in production, so these have to be visible in the
    deploy log: a wrong provider, an empty model pool or a re-enabled reranker
    all look identical from the outside (a stream that dies after "searching").
    """
    from src.rag import retrieve as _retrieve

    pool = getattr(_retrieve, "_llm_pool", None)
    models = list(getattr(pool, "_models", None) or _retrieve.FALLBACK_MODELS)
    logger.info(
        "Startup diagnostics: base_url=%s models=%s disabled=%s rerank=%s "
        "rewrite=%s heartbeat=%.0fs warm=%s",
        _retrieve.BASE_URL or "<default>",
        models,
        pool.disabled_models if pool is not None else [],
        os.getenv("RERANK_ENABLED", "false"),
        os.getenv("REWRITE_ENABLED", "true"),
        HEARTBEAT_INTERVAL,
        os.getenv("WARM_RETRIEVAL_ON_STARTUP", "true"),
    )


app = FastAPI(title="Tunisia Energy RAG API", lifespan=lifespan)
app.include_router(auth_router)


def _describe_error(exc: BaseException) -> str:
    """Render a stream failure for the client.

    Previously every failure collapsed into "An internal error occurred during
    processing.", which made provider outages, model-not-found, quota errors
    and retrieval bugs indistinguishable from the outside. Report the specific
    cause; the full traceback is still logged server-side.
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    if not detail:
        return f"Request failed ({name})."
    # Keep it short: this is rendered in the chat bubble.
    return f"Request failed ({name}): {detail[:300]}"

# CORS (env-driven origins) + security headers + rate limiting.
add_security_middlewares(app)
configure_limiter(app, limiter)

# L14 Fix: Global concurrency limiter — prevents pool exhaustion from burst traffic
_concurrency_semaphore = asyncio.Semaphore(15)  # Max 15 concurrent LLM requests


# ---------------------------------------------------------------------------
# Metrics middleware — request latency + count
# ---------------------------------------------------------------------------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record request duration and count for Prometheus."""
    # Skip the /metrics endpoint itself to avoid self-referential noise
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)

    ACTIVE_REQUESTS.inc()
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        norm = normalize_path(path)
        status = str(response.status_code)
        HTTP_DURATION.labels(method=request.method, path=norm, status=status).observe(elapsed)
        HTTP_REQUESTS.labels(method=request.method, path=norm, status=status).inc()
        return response
    except Exception:
        elapsed = time.perf_counter() - start
        norm = normalize_path(path)
        HTTP_DURATION.labels(method=request.method, path=norm, status="500").observe(elapsed)
        HTTP_REQUESTS.labels(method=request.method, path=norm, status="500").inc()
        raise
    finally:
        ACTIVE_REQUESTS.dec()


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
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
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
    """Serialize a pipeline event dict to one SSE frame.

    Heartbeats are emitted as SSE *comments* rather than data frames: a comment
    keeps the connection warm without being interpreted as a payload by the
    client's SSE parser. Render's edge and Cloudflare both drop a response that
    goes silent, which is what produced a stream that emitted "searching" and
    then vanished with no error frame -- the upstream was still healthy.
    """
    if event.get("type") == "heartbeat":
        return ": keep-alive\n\n"
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


# How long the chat stream may stay silent before a keep-alive comment is sent.
# Retrieval runs in a threadpool and can block for tens of seconds, which is
# long enough for Render's edge to drop the response.
HEARTBEAT_INTERVAL = float(os.getenv("SSE_HEARTBEAT_SECONDS", "10") or 10)

_SENTINEL = object()


async def _with_heartbeats(source):
    """Yield frames from `source`, emitting a keep-alive when it goes quiet.

    The pipeline is pumped by its own task into a queue, so the consumer can
    time out on the queue without ever cancelling a suspended ``__anext__``
    (cancelling mid-generator would kill the pipeline instead of nudging it).
    """
    queue: "asyncio.Queue[Any]" = asyncio.Queue()

    async def pump() -> None:
        try:
            async for frame in source:
                queue.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - forwarded to the consumer
            queue.put_nowait(exc)
        finally:
            # Close the pipeline explicitly: a client that walks away mid-stream
            # would otherwise leave it running (holding a concurrency slot and
            # the whole retrieval stack) until the GC gets around to it.
            with suppress(Exception, asyncio.CancelledError):
                await source.aclose()
            queue.put_nowait(_SENTINEL)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            queue.put_nowait(_sse({"type": "heartbeat"}))

    pump_task = asyncio.create_task(pump())
    ticker_task = asyncio.create_task(ticker())
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        for task in (ticker_task, pump_task):
            task.cancel()
        with suppress(asyncio.CancelledError):
            await pump_task
        with suppress(asyncio.CancelledError):
            await ticker_task


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
@limiter.limit(CHAT_LIMIT)
async def chat_endpoint(
    request: Request,
    payload: QueryRequest,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    try:
        user_query = payload.query.strip()
        if not user_query:
            raise HTTPException(status_code=400, detail="Query cannot be empty.")
        
        # L12 FIX: Query complexity validation
        if len(user_query) > 5000:
            raise HTTPException(
                status_code=400,
                detail="Query too long. Please keep questions under 5000 characters."
            )

        history_dicts = [{"role": m.role, "content": m.content} for m in payload.chat_history]

        # L14 Fix: Use concurrency limiter to prevent pool exhaustion
        async with _concurrency_semaphore:
            # Await the fully asynchronous pipeline (ChromaDB retrieval runs in a thread pool)
            answer, structured_sources = await run_pipeline(user_query, history_dicts)

        # Persist the turn (best-effort: never let DB issues break the answer)
        conversation_id = None
        try:
            parsed_cid = _parse_conversation_id(payload.conversation_id)
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
    request: Request,
    payload: QueryRequest,
    user: Optional[User] = Depends(get_current_user_optional),
):
    """SSE streaming endpoint with manual rate limiting.
    
    NOTE: @limiter.limit decorator doesn't work with StreamingResponse,
    so we check rate limits manually here (L11 fix).
    """
    user_query = payload.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    
    # Manual rate limiting for streaming (L11 fix)
    # @limiter.limit decorator doesn't work with StreamingResponse,
    # so we implement a simple sliding-window counter per client IP.
    try:
        from slowapi.util import get_remote_address
        rate_key = get_remote_address(request)
        now = time.time()
        window_key = f"stream:{rate_key}"
        if not hasattr(app.state, '_stream_counts'):
            app.state._stream_counts = {}
        counts = app.state._stream_counts
        # Clean entries older than 60 seconds
        counts[window_key] = [t for t in counts.get(window_key, []) if now - t < 60]
        if len(counts.get(window_key, [])) >= 10:  # 10/minute limit
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")
        counts.setdefault(window_key, []).append(now)
    except HTTPException:
        raise
    except Exception:
        pass  # Don't let rate limiting errors break the stream

    history_dicts = [{"role": m.role, "content": m.content} for m in payload.chat_history]
    parsed_cid = _parse_conversation_id(payload.conversation_id)

    async def event_generator():
        # L14 Fix: Use concurrency limiter to prevent pool exhaustion
        async with _concurrency_semaphore:
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
                    yield _sse({"type": "error", "message": _describe_error(e)})

    # Increase timeout to 5 minutes: without a provider-side timeout, LLM\r
    # streams can take several minutes, especially with the fallback pool.\r
    # The per-model timeout is 60s in retrieve.py, and with 8 fallback models,\r
    # a full cascade can exceed 2 minutes.\r
    async def event_generator_with_timeout():
        try:
            async with asyncio.timeout(300):  # 5 minutes max
                async for sse_frame in _with_heartbeats(event_generator()):
                    # event_generator() already yields SSE-formatted strings,
                    # so yield them directly — do NOT wrap in _sse() again.
                    yield sse_frame
        except asyncio.TimeoutError:
            yield _sse({"type": "error", "message": "Request timed out. Please try a simpler query."})
        except Exception as e:
            logger.exception("Stream timeout/error: %s", e)
            yield _sse({"type": "error", "message": _describe_error(e)})

    return StreamingResponse(
        event_generator_with_timeout(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable nginx buffering: these headers tell any nginx proxy in
            # front to stream the SSE frames as they arrive instead of
            # buffering the entire response and sending it at the end.
            # Render's proxy needs 'proxy_no_cache' because it ignores
            # 'X-Accel-Buffering' on proxied responses.
            "X-Accel-No-Cache": "1",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Surrogate-Control": "no-store",
        },
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
async def get_conversation(
    conversation_id: uuid.UUID,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a conversation by ID with owner validation (L4 IDOR fix)."""
    conversation = await service.get_conversation(session, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    # CRITICAL: Owner validation — prevent IDOR (field is user_id, not owner_id)
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    if conversation.user_id != owner.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        messages=[
            MessageOut.model_validate(m)
            for m in sorted(conversation.messages, key=lambda m: m.created_at)
        ],
    )


@app.delete("/api/conversations")
async def delete_all_conversations(
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    count = await service.delete_all_conversations(session, owner.id)
    return {"status": "ok", "deleted": count}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    owner = user if user is not None else await service.get_or_create_demo_user(session)
    deleted = await service.delete_conversation(session, conversation_id, owner.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Outage reports (crowdsourced map)
# ---------------------------------------------------------------------------

@app.get("/api/outages", response_model=List[OutageReportOut])
async def list_outages(status: Optional[ReportStatus] = None, session=Depends(get_db_dependency)):
    return await service.list_outages(session, status)


@app.post("/api/outages", response_model=OutageReportOut, status_code=201)
@limiter.limit(OUTAGE_CREATE_LIMIT)
async def create_outage(
    request: Request,
    payload: OutageCreate,
    session=Depends(get_db_dependency),
    user: Optional[User] = Depends(get_current_user_optional),
):
    # Default coordinates to the selected governorate's center if not provided
    lat = payload.latitude
    lng = payload.longitude
    if lat is None or lng is None:
        from src.database.seed import _TUNISIA_COORDS
        coords = _TUNISIA_COORDS.get(payload.region.strip())
        if coords:
            lat, lng = coords
        else:
            # Fallback: center of Tunisia
            lat, lng = 34.0, 9.5

    return await service.create_outage(
        session,
        utility=payload.utility,
        region=payload.region.strip(),
        latitude=lat,
        longitude=lng,
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
@limiter.limit(ADMIN_LIMIT)
async def purge_stats(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
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
@limiter.limit(ADMIN_LIMIT)
async def run_purge_now(
    request: Request, _: None = Depends(require_admin_key)
):
    """Trigger an immediate purge (useful for ops/testing)."""
    deleted = await _purge_expired_outages()
    return {"status": "ok", "deleted": deleted}


@app.get("/api/admin/config")
@limiter.limit(ADMIN_LIMIT)
async def get_admin_config(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
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
@limiter.limit(ADMIN_LIMIT)
async def update_admin_config(
    request: Request,
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


# ---------------------------------------------------------------------------
# Admin: PDF document ingestion (upload a file or ingest a URL)
# ---------------------------------------------------------------------------
# Full auto-ingestion flow for a single document:
#   save/download -> data/raw -> LLM triage -> data/filtered or data/blacklisted
#   -> (if accepted) chunk + embed into ChromaDB so the chat can retrieve it.
# The triage + ChromaDB work is synchronous, so it runs in a thread pool to
# keep the event loop responsive.

class DocumentUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DocumentIngestOut(BaseModel):
    filename: str
    status: str
    # Optional: a triage run that could not reach a model returns status
    # "ERROR" with no verdict, so these stay nullable.
    dest: Optional[str] = None
    gate1_score: Optional[float] = None
    gate2_score: Optional[float] = None
    master_score: Optional[float] = None
    total_pages: Optional[int] = None
    chunks_indexed: int = 0
    index_error: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/admin/documents/upload", response_model=DocumentIngestOut)
@limiter.limit(ADMIN_LIMIT)
async def admin_upload_document(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(require_admin_key),
):
    """Admin-only: upload a PDF file, run triage, and index it if accepted."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
        )
    if not is_valid_pdf(content):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF.")

    try:
        pdf_path = await run_in_threadpool(save_uploaded_pdf, content, file.filename or "document.pdf")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    decision = await run_in_threadpool(ingest_pdf, pdf_path)
    return decision


@app.post("/api/admin/documents/from-url", response_model=DocumentIngestOut)
@limiter.limit(ADMIN_LIMIT)
async def admin_ingest_from_url(
    request: Request,
    payload: DocumentUrlIn,
    _: None = Depends(require_admin_key),
):
    """Admin-only: download a PDF from a URL, run triage, index if accepted."""
    try:
        pdf_path = await run_in_threadpool(download_pdf_from_url, payload.url.strip())
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    decision = await run_in_threadpool(ingest_pdf, pdf_path)
    return decision


# ---------------------------------------------------------------------------
# Admin: URL source management (research + ingest)
# ---------------------------------------------------------------------------
# Two-phase workflow for batch PDF collection:
#   1. Admin adds URLs → sources table (dedup by URL)
#   2. "Deep Research" → download + validate → data/raw/
#   3. "Ingest" → triage (LLM) → ChromaDB (if accepted)

class SourceCreateIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceOut(BaseModel):
    id: uuid.UUID
    url: str
    filename: Optional[str] = None
    status: str
    file_size: Optional[int] = None
    total_pages: Optional[int] = None
    gate1_score: Optional[float] = None
    master_score: Optional[float] = None
    chunks_indexed: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ResearchResult(BaseModel):
    downloaded: int
    failed: int
    total: int


class IngestResult(BaseModel):
    indexed: int
    rejected: int
    failed: int
    total: int


@app.get("/api/admin/sources", response_model=List[SourceOut])
@limiter.limit(ADMIN_LIMIT)
async def list_sources(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """List all tracked sources with their current status."""
    from sqlalchemy import select

    result = await session.execute(select(Source).order_by(Source.created_at.desc()))
    return result.scalars().all()


@app.post("/api/admin/sources", response_model=SourceOut, status_code=201)
@limiter.limit(ADMIN_LIMIT)
async def add_source(
    request: Request,
    payload: SourceCreateIn,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Add a PDF URL to the research queue. Rejects duplicates."""
    from sqlalchemy import select

    # Check for duplicate URL.
    existing = await session.execute(
        select(Source).where(Source.url == payload.url.strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This URL is already being tracked.")

    source = Source(url=payload.url.strip())
    session.add(source)
    await session.commit()
    await session.refresh(source)
    return source


@app.delete("/api/admin/sources/{source_id}")
@limiter.limit(ADMIN_LIMIT)
async def delete_source(
    request: Request,
    source_id: uuid.UUID,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Remove a source from the tracking table."""
    from sqlalchemy import select

    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    await session.delete(source)
    await session.commit()
    return {"status": "ok"}


@app.post("/api/admin/sources/research", response_model=ResearchResult)
@limiter.limit(ADMIN_LIMIT)
async def research_sources(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Download + validate all pending sources. Skips already-downloaded or failed."""
    from sqlalchemy import select

    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.PENDING)
    )
    pending = result.scalars().all()

    if not pending:
        return ResearchResult(downloaded=0, failed=0, total=0)

    downloaded = 0
    failed = 0
    for source in pending:
        result = await run_in_threadpool(download_pdf_from_source, source.url)
        if result["error"]:
            source.status = SourceStatus.FAILED
            source.error_message = result["error"]
            failed += 1
        else:
            source.filename = result["filename"]
            source.file_size = result["file_size"]
            source.status = SourceStatus.DOWNLOADED
            downloaded += 1

    await session.commit()
    return ResearchResult(
        downloaded=downloaded,
        failed=failed,
        total=len(pending),
    )


@app.post("/api/admin/sources/ingest", response_model=IngestResult)
@limiter.limit(ADMIN_LIMIT)
async def ingest_sources(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Triage + ChromaDB index all downloaded sources."""
    from sqlalchemy import select

    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.DOWNLOADED)
    )
    ready = result.scalars().all()

    if not ready:
        return IngestResult(indexed=0, rejected=0, failed=0, total=0)

    indexed = 0
    rejected = 0
    failed = 0
    for source in ready:
        result = await run_in_threadpool(ingest_downloaded_pdf, source.filename)
        source.total_pages = result["total_pages"]
        source.gate1_score = result["gate1_score"]
        source.master_score = result["master_score"]
        source.chunks_indexed = result["chunks_indexed"]
        if result["error"]:
            source.status = SourceStatus.FAILED
            source.error_message = result["error"]
            failed += 1
        elif result["status"] == "indexed":
            source.status = SourceStatus.INDEXED
            indexed += 1
        else:
            source.status = SourceStatus.TRIAGE_REJECTED
            rejected += 1

    await session.commit()
    return IngestResult(
        indexed=indexed,
        rejected=rejected,
        failed=failed,
        total=len(ready),
    )


# ---------------------------------------------------------------------------
# Admin: SSE progress endpoints for research & ingest
# ---------------------------------------------------------------------------
# These yield real-time progress events so the admin can see which file is
# being processed, how many are done, and what the result was.

@app.post("/api/admin/sources/research/stream")
@limiter.limit(ADMIN_LIMIT)
async def research_sources_stream(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """SSE stream: download + validate all pending sources with live progress."""
    from sqlalchemy import select

    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.PENDING)
    )
    pending = result.scalars().all()

    async def event_generator():
        if not pending:
            yield _sse({"type": "done", "downloaded": 0, "failed": 0, "total": 0})
            return

        total = len(pending)
        downloaded = 0
        failed = 0

        for i, source in enumerate(pending):
            yield _sse({
                "type": "progress",
                "current": i + 1,
                "total": total,
                "filename": source.url.split("/")[-1] or source.url,
                "status": "downloading",
                "downloaded": downloaded,
                "failed": failed,
            })

            dl_result = await run_in_threadpool(download_pdf_from_source, source.url)
            if dl_result["error"]:
                source.status = SourceStatus.FAILED
                source.error_message = dl_result["error"]
                failed += 1
            else:
                source.filename = dl_result["filename"]
                source.file_size = dl_result["file_size"]
                source.status = SourceStatus.DOWNLOADED
                downloaded += 1

            yield _sse({
                "type": "file_done",
                "current": i + 1,
                "total": total,
                "filename": source.filename or source.url.split("/")[-1],
                "status": "downloaded" if dl_result["error"] is None else "failed",
                "error": dl_result["error"],
                "downloaded": downloaded,
                "failed": failed,
            })

        await session.commit()
        yield _sse({"type": "done", "downloaded": downloaded, "failed": failed, "total": total})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-No-Cache": "1",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Surrogate-Control": "no-store",
        },
    )


@app.post("/api/admin/sources/ingest/stream")
@limiter.limit(ADMIN_LIMIT)
async def ingest_sources_stream(
    request: Request,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """SSE stream: triage + ChromaDB index all downloaded sources with live progress."""
    from sqlalchemy import select

    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.DOWNLOADED)
    )
    ready = result.scalars().all()

    async def event_generator():
        if not ready:
            yield _sse({"type": "done", "indexed": 0, "rejected": 0, "failed": 0, "total": 0})
            return

        total = len(ready)
        indexed = 0
        rejected = 0
        failed = 0

        for i, source in enumerate(ready):
            yield _sse({
                "type": "progress",
                "current": i + 1,
                "total": total,
                "filename": source.filename or "unknown",
                "status": "ingesting",
                "indexed": indexed,
                "rejected": rejected,
                "failed": failed,
            })

            ing_result = await run_in_threadpool(ingest_downloaded_pdf, source.filename)
            source.total_pages = ing_result["total_pages"]
            source.gate1_score = ing_result["gate1_score"]
            source.master_score = ing_result["master_score"]
            source.chunks_indexed = ing_result["chunks_indexed"]

            if ing_result["error"]:
                source.status = SourceStatus.FAILED
                source.error_message = ing_result["error"]
                failed += 1
                file_status = "failed"
            elif ing_result["status"] == "indexed":
                source.status = SourceStatus.INDEXED
                indexed += 1
                file_status = "indexed"
            else:
                source.status = SourceStatus.TRIAGE_REJECTED
                rejected += 1
                file_status = "rejected"

            yield _sse({
                "type": "file_done",
                "current": i + 1,
                "total": total,
                "filename": source.filename or "unknown",
                "status": file_status,
                "chunks_indexed": source.chunks_indexed,
                "master_score": source.master_score,
                "indexed": indexed,
                "rejected": rejected,
                "failed": failed,
            })

        await session.commit()
        yield _sse({"type": "done", "indexed": indexed, "rejected": rejected, "failed": failed, "total": total})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-No-Cache": "1",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Surrogate-Control": "no-store",
        },
    )


class BulkDeleteRequest(BaseModel):
    ids: Optional[List[uuid.UUID]] = None
    status: Optional[str] = None  # delete all with this status


class BulkDeleteResult(BaseModel):
    deleted: int


class CrawlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    max_depth: int = Field(default=1, ge=0, le=5, description="Recursive crawl depth (0=single page, 5=max)")
    max_pages: int = Field(default=50, ge=1, le=200, description="Max HTML pages to crawl")


class CrawlResult(BaseModel):
    found: int
    added: int
    skipped: int
    pdf_urls: List[str]
    pages_crawled: int
    pages_visited: int


@app.post("/api/admin/sources/bulk-delete", response_model=BulkDeleteResult)
@limiter.limit(ADMIN_LIMIT)
async def bulk_delete_sources(
    request: Request,
    payload: BulkDeleteRequest,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Delete multiple sources by IDs or by status."""
    from sqlalchemy import delete as sa_delete

    if payload.ids:
        result = await session.execute(
            sa_delete(Source).where(Source.id.in_(payload.ids))
        )
    elif payload.status:
        result = await session.execute(
            sa_delete(Source).where(Source.status == payload.status)
        )
    else:
        raise HTTPException(status_code=400, detail="Provide ids or status to delete.")

    await session.commit()
    return BulkDeleteResult(deleted=result.rowcount)


@app.post("/api/admin/sources/crawl", response_model=CrawlResult)
@limiter.limit(ADMIN_LIMIT)
async def crawl_for_sources(
    request: Request,
    payload: CrawlRequest,
    _: None = Depends(require_admin_key),
    session=Depends(get_db_dependency),
):
    """Recursively crawl a website for PDF links and add them as sources."""
    from sqlalchemy import select

    crawl_result = await run_in_threadpool(
        crawl_website_for_pdfs,
        payload.url.strip(),
        payload.max_depth,
        payload.max_pages,
    )
    pdf_urls = crawl_result["pdf_urls"]

    added = 0
    skipped = 0
    for pdf_url in pdf_urls:
        # Check for duplicate URL.
        existing = await session.execute(
            select(Source).where(Source.url == pdf_url)
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        source = Source(url=pdf_url)
        session.add(source)
        added += 1

    await session.commit()
    return CrawlResult(
        found=len(pdf_urls),
        added=added,
        skipped=skipped,
        pdf_urls=pdf_urls,
        pages_crawled=crawl_result["pages_crawled"],
        pages_visited=crawl_result["pages_visited"],
    )


@app.get("/dashboard")
@limiter.exempt
def dashboard(request: Request):
    """Built-in metrics dashboard — no Docker/Grafana needed."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/metrics")
@limiter.exempt
def metrics_endpoint(request: Request):
    """Prometheus scrape endpoint — returns all registered metrics in text format."""
    return PlainTextResponse(
        generate_latest().decode("utf-8"),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/health")
@limiter.exempt
def health_check(request: Request):
    """Liveness probe: verifies the server process is up.

    Orchestrators and healthchecks use /ready (which also checks Postgres and
    ChromaDB) before sending traffic; /health only proves the process runs.
    """
    return {"status": "healthy", "api": "online"}


@app.get("/ready")
@limiter.exempt
async def readiness_check(request: Request):
    """Readiness probe: the app is ready when Postgres and ChromaDB respond.

    Returns 200 with a per-dependency status map when both are reachable, or
    503 listing what failed. Each check has a short timeout so a hung
    dependency cannot block the probe (and thus the orchestrator's decision).
    """
    checks = {"db": False, "chroma": False}

    async def _db_ping():
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

    try:
        # ChromaDB's collection.count() is synchronous file I/O -> thread pool.
        await asyncio.wait_for(asyncio.to_thread(collection.count), timeout=3)
        checks["chroma"] = True
    except Exception as e:
        logger.warning("Readiness: ChromaDB check failed: %s", e)

    try:
        await asyncio.wait_for(_db_ping(), timeout=3)
        checks["db"] = True
    except Exception as e:
        logger.warning("Readiness: database check failed: %s", e)

    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )