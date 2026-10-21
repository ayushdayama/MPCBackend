import logging
import os
import secrets
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

MIN_SECRET_KEY_BYTES = 32
JWT_ISSUER = "cycle-sense-api"
JWT_AUDIENCE = "cycle-sense-client"


def _load_app_secret_key() -> tuple[str, bool]:
    configured_key = os.getenv("APP_SECRET_KEY", "").strip()
    if configured_key:
        if len(configured_key.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
            raise RuntimeError("APP_SECRET_KEY must be at least 32 bytes")
        return configured_key, True

    if os.getenv("RENDER", "").lower() == "true":
        raise RuntimeError("Set APP_SECRET_KEY in Render before starting the backend")

    logging.warning(
        "APP_SECRET_KEY is unset; using a process-local key. Set a persistent value before deploying."
    )
    return secrets.token_urlsafe(48), False


APP_SECRET_KEY, APP_SECRET_KEY_CONFIGURED = _load_app_secret_key()

_bearer = HTTPBearer(auto_error=False)


def create_session_token(username: str) -> str:
    if not isinstance(username, str) or not username.strip():
        raise ValueError("A non-empty username is required")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "purpose": "session",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + 86400,
            "jti": secrets.token_urlsafe(16),
        },
        APP_SECRET_KEY,
        algorithm="HS256",
    )


def create_calendar_state(username: str) -> str:
    if not isinstance(username, str) or not username.strip():
        raise ValueError("A non-empty username is required")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "purpose": "calendar_oauth",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + 600,
            "jti": secrets.token_urlsafe(16),
        },
        APP_SECRET_KEY,
        algorithm="HS256",
    )


def decode_token(token: str, purpose: str) -> str:
    try:
        payload = jwt.decode(
            token,
            APP_SECRET_KEY,
            algorithms=["HS256"],
            options={
                "require": ["sub", "purpose", "iat", "exp"],
                "verify_aud": False,
            },
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise ValueError("Invalid or expired token") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("purpose") != purpose
        or not isinstance(payload.get("sub"), str)
        or not payload["sub"].strip()
    ):
        raise ValueError("Invalid token purpose")
    has_context_claims = any(claim in payload for claim in ("iss", "aud", "jti"))
    if has_context_claims and (
        payload.get("iss") != JWT_ISSUER
        or payload.get("aud") != JWT_AUDIENCE
        or not isinstance(payload.get("jti"), str)
        or not payload["jti"]
    ):
        raise ValueError("Invalid token context")
    return payload["sub"]


def get_current_username(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Please sign in again")
    try:
        return decode_token(credentials.credentials, "session")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Please sign in again") from exc
