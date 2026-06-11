from __future__ import annotations

from fastapi import HTTPException, Request, Response, status

from .identity import SessionAuditContext


ACCESS_TOKEN_COOKIE_NAME = "access_token"
REFRESH_TOKEN_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/"


def request_uses_secure_cookies(request: Request) -> bool:
    return request.url.scheme == "https"


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_max_age_seconds: int,
    refresh_max_age_seconds: int,
    secure: bool,
) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=access_max_age_seconds,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=refresh_max_age_seconds,
        path=REFRESH_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def extract_access_token_from_request(request: Request) -> str | None:
    return request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)


def extract_refresh_token_from_request(request: Request, *, body_refresh_token: str | None = None) -> str | None:
    if body_refresh_token:
        return body_refresh_token
    return request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)


def extract_client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


async def read_request_body_limited(
    request: Request,
    *,
    max_bytes: int,
    resource_name: str = "request body",
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    content_length_header = request.headers.get("Content-Length")
    if content_length_header is not None and content_length_header.strip():
        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid Content-Length header",
            ) from exc
        if content_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid Content-Length header",
            )
        if content_length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"{resource_name} exceeds limit of {max_bytes} bytes",
            )

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"{resource_name} exceeds limit of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def build_session_audit_context(request: Request) -> SessionAuditContext:
    user_agent = request.headers.get("User-Agent")
    return SessionAuditContext(
        ip=extract_client_ip(request),
        user_agent=None if user_agent is None or not user_agent.strip() else user_agent.strip(),
    )
