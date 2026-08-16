"""Password hashing (bcrypt) and JWT helpers for user authentication.

Secrets come from the environment:
- ``JWT_SECRET``       — signing key for HS256 tokens (required for auth endpoints).
- ``JWT_EXPIRE_MINUTES`` — token lifetime, default 7 days.

Passwords are hashed with bcrypt (72-byte input limit — enforced by the
validation model in ``src/api/auth.py``). Tokens are stateless HS256 JWTs
carrying the user id and an expiry; the ``get_current_user`` dependency in
``src/api/auth.py`` decodes them and loads the user.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days


def auth_configured() -> bool:
    """True when a JWT_SECRET is set (auth endpoints refuse to run otherwise)."""
    return bool(JWT_SECRET)


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the password (as a str)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (e.g. legacy data) must not crash the login endpoint.
        return False


def create_access_token(user_id: str, extra: Optional[Dict[str, Any]] = None) -> str:
    """Create a signed JWT for the given user id."""
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode + verify a JWT; returns the payload or None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
