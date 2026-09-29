"""The site's one admin identity, gated by MTG_API_ADMIN_PASSWORD.

Ported from the Connections app: an HMAC-signed session cookie
("<expiresAt>.<hex signature>") keyed on sha256("admin:" + password), so
there is no separate secret to configure and changing the password logs
every session out. Cookie-authenticated admin calls also need an
X-Admin-Request: 1 header, as defense in depth alongside SameSite=Strict.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from mtg_api.config import settings

ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_REQUEST_HEADER = "x-admin-request"
# 90 days: log in once per device.
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60


def session_secret(password: str) -> bytes:
    return hashlib.sha256(f"admin:{password}".encode()).digest()


def _signature(secret: bytes, expires_at: str) -> str:
    return hmac.new(secret, expires_at.encode(), hashlib.sha256).hexdigest()


def sign_session(secret: bytes, now: float | None = None) -> str:
    expires_at = str(int((time.time() if now is None else now) + SESSION_MAX_AGE_SECONDS))
    return f"{expires_at}.{_signature(secret, expires_at)}"


def verify_session(value: str | None, secret: bytes, now: float | None = None) -> bool:
    if not value:
        return False
    expires_at, _, signature = value.partition(".")
    if not expires_at.isdigit() or not signature:
        return False
    if int(expires_at) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(_signature(secret, expires_at), signature)


def _password() -> str:
    return settings.admin_password.get_secret_value()


def is_admin(request: Request) -> bool:
    password = _password()
    return bool(password) and verify_session(
        request.cookies.get(ADMIN_SESSION_COOKIE), session_secret(password)
    )


def has_admin_marker(request: Request) -> bool:
    return request.headers.get(ADMIN_REQUEST_HEADER) == "1"


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="admin login required")
    if not has_admin_marker(request):
        raise HTTPException(status_code=403, detail="missing X-Admin-Request header")


router = APIRouter(prefix="/api/v1/auth")


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    password = _password()
    if not password or not hmac.compare_digest(body.password.encode(), password.encode()):
        raise HTTPException(status_code=403, detail="Incorrect password.")
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        sign_session(session_secret(password)),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        # uvicorn --proxy-headers takes the scheme from X-Forwarded-Proto.
        secure=request.url.scheme == "https",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    return {"is_admin": is_admin(request)}
