"""Security middleware helpers for Flask APIs (CSRF + rate limit)."""

from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass

from flask import Flask, current_app, request, session
from werkzeug.exceptions import TooManyRequests

from errors import error_response

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_DEFAULT_CSRF_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/needs-setup",
    "/api/config/workspace-status",
    "/api/version",
    "/api/version/check",
    "/api/agents/active",
    "/api/docs",
    "/api/knowledge/v1/",
    "/api/shares/",
)

_DEFAULT_WEBHOOK_PREFIXES = ("/api/triggers/webhook/", "/api/ciss/sales/")

_DEFAULT_RATE_LIMIT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/setup",
    "/api/knowledge/v1/",
    "/api/triggers/webhook/",
    "/api/ciss/sales/webhook/",
    "/api/version",
    "/api/version/check",
    "/api/agents/active",
)

_DEFAULT_RATE_LIMIT_EXEMPT_PREFIXES = (
    "/api/auth/needs-setup",
    "/api/config/workspace-status",
    "/api/docs",
)

_DEFAULT_WEBHOOK_SIGNATURE_HEADERS = (
    "X-Signature",
    "X-Webhook-Signature",
    "X-Hub-Signature-256",
    "Stripe-Signature",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return max(parsed, minimum)


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _path_matches_prefixes(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def _request_has_bearer_token() -> bool:
    return request.headers.get("Authorization", "").startswith("Bearer ")


def _get_client_ip() -> str:
    if request.access_route:
        candidate = (request.access_route[0] or "").strip()
        if candidate:
            return candidate
    if request.remote_addr:
        return request.remote_addr
    return "unknown"


@dataclass
class _RateEntry:
    window_started_at: float
    count: int


class InMemoryRateLimiter:
    """Simple fixed-window in-memory rate limiter keyed by (IP, path)."""

    def __init__(self, *, max_requests: int, window_seconds: int, max_keys: int = 10000):
        self.max_requests = max(max_requests, 1)
        self.window_seconds = max(window_seconds, 1)
        self.max_keys = max(max_keys, 1)
        self._entries: dict[tuple[str, str], _RateEntry] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        if len(self._entries) < self.max_keys:
            return
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if (now - entry.window_started_at) >= self.window_seconds
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
        if len(self._entries) < self.max_keys:
            return
        oldest_keys = sorted(
            self._entries.items(),
            key=lambda kv: kv[1].window_started_at,
        )[: max(1, self.max_keys // 10)]
        for key, _entry in oldest_keys:
            self._entries.pop(key, None)

    def check(self, *, ip: str, path: str) -> int | None:
        """Return retry_after seconds if blocked, otherwise None."""
        now = time.monotonic()
        key = (ip, path)
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
            if entry is None or (now - entry.window_started_at) >= self.window_seconds:
                self._entries[key] = _RateEntry(window_started_at=now, count=1)
                return None

            if entry.count >= self.max_requests:
                retry_after = self.window_seconds - int(now - entry.window_started_at)
                return max(1, retry_after)

            entry.count += 1
            return None


def _ensure_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def register_security(
    app: Flask,
    *,
    csrf_exempt_prefixes: tuple[str, ...] | None = None,
    webhook_prefixes: tuple[str, ...] | None = None,
    webhook_signature_headers: tuple[str, ...] | None = None,
    rate_limit_prefixes: tuple[str, ...] | None = None,
    rate_limit_exempt_prefixes: tuple[str, ...] | None = None,
    rate_limiter: InMemoryRateLimiter | None = None,
) -> None:
    """Register CSRF and rate-limit middleware on a Flask app."""
    csrf_enabled = _env_bool("API_CSRF_ENABLED", True)
    csrf_cookie_name = os.environ.get("API_CSRF_COOKIE_NAME", "csrf_token").strip() or "csrf_token"
    csrf_header_names = _env_csv("API_CSRF_HEADER_NAMES", ("X-CSRF-Token", "X-XSRF-Token"))
    csrf_exempt = csrf_exempt_prefixes or _env_csv("API_CSRF_EXEMPT_PREFIXES", _DEFAULT_CSRF_EXEMPT_PREFIXES)
    webhook_exempt = webhook_prefixes or _env_csv("API_CSRF_WEBHOOK_PREFIXES", _DEFAULT_WEBHOOK_PREFIXES)
    webhook_headers = webhook_signature_headers or _env_csv(
        "API_CSRF_WEBHOOK_SIGNATURE_HEADERS",
        _DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
    )

    rate_limit_enabled = _env_bool("API_RATE_LIMIT_ENABLED", True)
    effective_rate_limiter = rate_limiter or InMemoryRateLimiter(
        max_requests=_env_int("API_RATE_LIMIT_MAX_REQUESTS", 120, minimum=1),
        window_seconds=_env_int("API_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=1),
        max_keys=_env_int("API_RATE_LIMIT_MAX_KEYS", 10000, minimum=100),
    )
    rl_prefixes = rate_limit_prefixes or _env_csv("API_RATE_LIMIT_PATH_PREFIXES", _DEFAULT_RATE_LIMIT_PREFIXES)
    rl_exempt_prefixes = rate_limit_exempt_prefixes or _env_csv(
        "API_RATE_LIMIT_EXEMPT_PREFIXES",
        _DEFAULT_RATE_LIMIT_EXEMPT_PREFIXES,
    )

    app.extensions["evonexus_security"] = {
        "csrf_enabled": csrf_enabled,
        "csrf_cookie_name": csrf_cookie_name,
        "csrf_header_names": csrf_header_names,
        "csrf_exempt_prefixes": csrf_exempt,
        "webhook_exempt_prefixes": webhook_exempt,
        "webhook_signature_headers": webhook_headers,
        "rate_limit_enabled": rate_limit_enabled,
        "rate_limiter": effective_rate_limiter,
        "rate_limit_prefixes": rl_prefixes,
        "rate_limit_exempt_prefixes": rl_exempt_prefixes,
    }

    @app.before_request
    def _csrf_protect():
        if not csrf_enabled:
            return None
        if request.method not in _MUTATING_METHODS:
            return None
        if not request.path.startswith("/api/"):
            return None
        if _path_matches_prefixes(request.path, csrf_exempt):
            return None
        if _path_matches_prefixes(request.path, webhook_exempt):
            if any(request.headers.get(h) for h in webhook_headers):
                return None
        if _request_has_bearer_token():
            return None

        session_cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "session")
        has_session_cookie = bool(request.cookies.get(session_cookie_name))
        if not has_session_cookie:
            return None

        session_token = session.get("_csrf_token")
        cookie_token = request.cookies.get(csrf_cookie_name)
        header_token = None
        for name in csrf_header_names:
            header_value = request.headers.get(name)
            if header_value:
                header_token = header_value
                break

        if not session_token or not cookie_token or not header_token:
            return error_response(
                403,
                code="csrf_failed",
                message="CSRF validation failed",
            )

        if not secrets.compare_digest(str(session_token), str(cookie_token)):
            return error_response(403, code="csrf_failed", message="CSRF validation failed")
        if not secrets.compare_digest(str(session_token), str(header_token)):
            return error_response(403, code="csrf_failed", message="CSRF validation failed")
        return None

    @app.before_request
    def _rate_limit_guard():
        if not rate_limit_enabled:
            return None
        if request.method == "OPTIONS":
            return None
        if not request.path.startswith("/api/"):
            return None
        if not _path_matches_prefixes(request.path, rl_prefixes):
            return None
        if _path_matches_prefixes(request.path, rl_exempt_prefixes):
            return None

        retry_after = effective_rate_limiter.check(ip=_get_client_ip(), path=request.path)
        if retry_after is None:
            return None
        raise TooManyRequests(description="Rate limit exceeded", retry_after=retry_after)

    @app.after_request
    def _set_csrf_cookie(response):
        if not csrf_enabled:
            return response
        if not request.path.startswith("/api/"):
            return response

        token = _ensure_csrf_token()
        response.set_cookie(
            csrf_cookie_name,
            token,
            samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Strict"),
            secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)),
            httponly=False,
            path="/",
        )
        return response
