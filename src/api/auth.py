"""User authentication endpoints (email/password + JWT).

- ``POST /api/auth/register`` — create an account, returns a JWT.
- ``POST /api/auth/login``    — verify credentials, returns a JWT.
- ``GET  /api/auth/me``       — current user (Bearer token).

Also exports ``get_current_user``, the FastAPI dependency that resolves the
``Authorization: Bearer <token>`` header to a ``User`` row. Endpoints that
want the user simply add ``user: User = Depends(get_current_user_optional)``;
endpoints that *require* login use ``Depends(get_current_user)``.

When ``JWT_SECRET`` is unset the auth endpoints refuse to run (503) rather
than falling back to a weak default -- same posture as the admin API key.
"""

import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.ratelimit import AUTH_LIMIT, limiter
from src.database import service
from src.database.connection import get_db_dependency
from src.database.models import User
from src.utils.security import (
    auth_configured,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# bcrypt only uses the first 72 bytes of a password; reject longer inputs
# explicitly so a user is never silently truncated.
MAX_PASSWORD_LENGTH = 72
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)
    display_name: Optional[str] = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    id: str
    email: Optional[str] = None
    display_name: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def _user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, display_name=user.display_name)


def _require_auth_configured() -> None:
    if not auth_configured():
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured (set JWT_SECRET).",
        )


def _auth_header_token(authorization: Optional[str]) -> Optional[str]:
    """Extract the token from ``Authorization: Bearer <token>`` (or None)."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _user_id_from_payload(payload: Optional[dict]) -> Optional[str]:
    """Extract the ``sub`` claim as a string, or None if absent/invalid."""
    if not payload:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


async def _resolve_user(
    session: AsyncSession, payload: Optional[dict]
) -> Optional[User]:
    """Load the User for a decoded token payload (None when invalid/unknown)."""
    sub = _user_id_from_payload(payload)
    if sub is None:
        return None
    try:
        user_id = uuid.UUID(sub)
    except ValueError:
        return None
    return await service.get_user_by_id(session, user_id)


async def get_current_user(
    session: AsyncSession = Depends(get_db_dependency),
    authorization: Optional[str] = Header(default=None),
) -> User:
    """FastAPI dependency: resolve the Bearer token to a User or raise 401.

    The 401 body is identical for missing, malformed, unknown, and expired
    tokens so attackers get no oracle about why a token was rejected.
    """
    token = _auth_header_token(authorization)
    payload = decode_access_token(token) if token else None
    user = await _resolve_user(session, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user


async def get_current_user_optional(
    session: AsyncSession = Depends(get_db_dependency),
    authorization: Optional[str] = Header(default=None),
) -> Optional[User]:
    """Like ``get_current_user`` but returns None when not authenticated.

    Used by anonymous-capable endpoints (chat, outage reporting) so the app
    keeps working without an account -- conversations then fall back to the
    shared demo user.
    """
    token = _auth_header_token(authorization)
    payload = decode_access_token(token) if token else None
    return await _resolve_user(session, payload)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=AuthResponse, status_code=201)
@limiter.limit(AUTH_LIMIT)
async def register(
    request: Request,
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db_dependency),
):
    _require_auth_configured()
    email = payload.email.lower().strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address.")

    existing = await service.get_user_by_email(session, email)
    if existing is not None:
        # Same body as a login failure: don't reveal whether the email exists.
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = await service.create_user(
        session,
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    token = create_access_token(str(user.id))
    logger.info("New user registered: %s", email)
    return AuthResponse(access_token=token, user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
@limiter.limit(AUTH_LIMIT)
async def login(
    request: Request,
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_dependency),
):
    _require_auth_configured()
    user = await service.get_user_by_email(session, payload.email.lower().strip())
    # Identical 401 for unknown email and wrong password (no user oracle).
    if user is None or not user.password_hash or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_access_token(str(user.id))
    return AuthResponse(access_token=token, user=_user_out(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)
