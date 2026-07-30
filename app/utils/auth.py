"""
utils/auth.py — Authentication helpers: JWT tokens, password hashing,
user context management, and request token extraction.
"""

import asyncio
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.clients.pool import current_user_id
from app.config import JWT_ALGORITHM, JWT_EXPIRY_HOURS, JWT_SECRET, log

try:
    import bcrypt
except ImportError:
    bcrypt = None  # type: ignore

from datetime import datetime, timedelta, timezone

import jwt


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    pw_bytes = password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pw_bytes, hashed_bytes)


def create_access_token(user_id: str, email: str) -> str:
    """Create a signed JWT access token for the given user."""
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns the payload or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


async def get_authenticated_user(token: str) -> Optional[Dict[str, Any]]:
    """Validate a JWT token and return the matching MongoDB user record.
    Returns None if the token is invalid or the user does not exist.
    """
    from app.clients.pool import pool  # lazy import to avoid circular deps

    try:
        payload = decode_access_token(token)
        if not payload:
            return None
        uid = payload.get("sub")
        if not uid:
            return None
        user = await asyncio.to_thread(pool.db.users.find_one, {"_id": uid})
        if not user:
            return None
        return {
            "id": user["_id"],
            "email": user["email"],
            "user_metadata": user.get("user_metadata", {}),
        }
    except Exception as e:
        log.error(f"Error validating MongoDB user token: {e}")
    return None


async def set_user_context(request: Request) -> Optional[str]:
    """Extract user ID from the Authorization header and store it in the
    ContextVar so downstream cache lookups are automatically user-scoped.
    Returns the user ID or None for unauthenticated requests.
    """
    try:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            user = await get_authenticated_user(token)
            if user:
                uid = user.get("id")
                current_user_id.set(uid)
                return uid
    except Exception as e:
        log.warning(f"Error setting user context: {e}")
    current_user_id.set(None)
    return None


def get_token_from_request(request: Request) -> str:
    """Extract the Bearer token from the Authorization header.
    Raises HTTP 401 if the header is missing or malformed.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return auth_header.split(" ")[1]
