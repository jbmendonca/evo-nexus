"""Centralized JSON error responses for Flask APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

_ERROR_DEFAULTS: dict[int, dict[str, str]] = {
    400: {"code": "bad_request", "message": "Bad request"},
    401: {"code": "unauthorized", "message": "Authentication required"},
    403: {"code": "forbidden", "message": "Forbidden"},
    404: {"code": "not_found", "message": "Resource not found"},
    422: {"code": "unprocessable_entity", "message": "Unprocessable entity"},
    429: {"code": "too_many_requests", "message": "Too many requests"},
    500: {"code": "internal_error", "message": "Internal server error"},
}

_SUPPORTED_STATUS = frozenset(_ERROR_DEFAULTS.keys())


def _default_for(status: int) -> dict[str, str]:
    return _ERROR_DEFAULTS.get(status, _ERROR_DEFAULTS[500])


def _is_token_expired_message(message: str) -> bool:
    msg = (message or "").strip().lower()
    return "token expired" in msg or "expired token" in msg or "jwt expired" in msg


def error_response(
    status: int,
    *,
    message: str | None = None,
    code: str | None = None,
    details: dict[str, Any] | None = None,
    retry_after: int | None = None,
):
    """Return standardized JSON payload for API errors."""
    status_code = status if status in _SUPPORTED_STATUS else 500
    default = _default_for(status_code)
    payload = {
        "ok": False,
        "error": {
            "status": status_code,
            "code": code or default["code"],
            "message": message or default["message"],
        },
        "path": request.path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        payload["error"]["details"] = details
    if retry_after is not None:
        payload["error"]["retry_after"] = retry_after

    response = jsonify(payload)
    response.status_code = status_code
    if retry_after is not None:
        response.headers["Retry-After"] = str(int(retry_after))
    return response


def unauthorized_response(*, token_expired: bool = False):
    """Return a normalized 401 response for auth failures, including expired tokens."""
    if token_expired:
        return error_response(401)
    return error_response(401)


def _http_exception_to_response(exc: HTTPException):
    status = exc.code or 500
    description = str(exc.description or "").strip()

    if status == 401 and _is_token_expired_message(description):
        return unauthorized_response(token_expired=True)

    retry_after = None
    if status == 429:
        retry_after_value = getattr(exc, "retry_after", None)
        if retry_after_value is not None:
            try:
                retry_after = int(retry_after_value)
            except (TypeError, ValueError):
                retry_after = None

    if status == 401:
        return unauthorized_response()

    msg = description or None
    return error_response(
        status,
        message=msg,
        retry_after=retry_after,
    )


def register_error_handlers(app: Flask) -> None:
    """Register centralized HTTP/Exception handlers on a Flask app."""

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        return _http_exception_to_response(exc)

    @app.errorhandler(Exception)
    def _handle_unexpected_exception(_exc: Exception):
        return error_response(500)

