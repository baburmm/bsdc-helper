"""Single-login session auth: signed cookie, no user database."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config

_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="bsdc-helper-session")

# Simple in-memory login throttle. Per replica, which is fine for one admin.
_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 300


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    hits = [t for t in _attempts[client_ip] if now - t < WINDOW_SECONDS]
    _attempts[client_ip] = hits
    if len(hits) >= MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts. Wait a few minutes and try again.",
        )


def record_failure(client_ip: str) -> None:
    _attempts[client_ip].append(time.time())


def verify_password(password: str) -> bool:
    if not config.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(password, config.ADMIN_PASSWORD)


def issue_session(response: Response) -> None:
    token = _serializer.dumps({"u": config.ADMIN_USERNAME})
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        max_age=config.SESSION_HOURS * 3600,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(config.COOKIE_NAME, path="/")


def current_user(request: Request) -> str | None:
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=config.SESSION_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")


def require_user(request: Request) -> str:
    """FastAPI dependency for every protected endpoint."""
    user = current_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user
