"""API security/error middleware tests for dashboard backend."""

from __future__ import annotations

import sys
from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from errors import register_error_handlers
from security import InMemoryRateLimiter, register_security


def _make_app(*, rate_limit_max_requests: int = 100) -> flask.Flask:
    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "security-test-secret"
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    @app.get("/api/bootstrap")
    def bootstrap():
        flask.session["user_id"] = "1"
        return flask.jsonify({"ok": True})

    @app.post("/api/private-write")
    def private_write():
        return flask.jsonify({"ok": True})

    @app.post("/api/auth/login")
    def auth_login():
        return flask.jsonify({"ok": True})

    @app.post("/api/triggers/webhook/signed")
    def signed_webhook():
        return flask.jsonify({"ok": True})

    @app.get("/api/knowledge/v1/ping")
    def public_ping():
        return flask.jsonify({"ok": True})

    @app.get("/api/bad-request")
    def bad_request():
        flask.abort(400, description="Missing field")

    @app.get("/api/forbidden")
    def forbidden():
        flask.abort(403)

    @app.get("/api/unprocessable")
    def unprocessable():
        flask.abort(422, description="Invalid payload")

    @app.get("/api/auth-expired")
    def auth_expired():
        flask.abort(401, description="Token expired")

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("boom")

    register_error_handlers(app)
    register_security(
        app,
        rate_limiter=InMemoryRateLimiter(
            max_requests=rate_limit_max_requests,
            window_seconds=60,
        ),
    )
    return app


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def client(app):
    return app.test_client()


def test_structured_errors_for_common_status_codes(client):
    checks = [
        ("/api/bad-request", 400, "bad_request"),
        ("/api/auth-expired", 401, "unauthorized"),
        ("/api/forbidden", 403, "forbidden"),
        ("/api/missing-route", 404, "not_found"),
        ("/api/unprocessable", 422, "unprocessable_entity"),
        ("/api/boom", 500, "internal_error"),
    ]
    for path, status, code in checks:
        resp = client.get(path)
        assert resp.status_code == status
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"]["status"] == status
        assert body["error"]["code"] == code
        assert "path" in body
        assert "timestamp" in body


def test_401_token_expired_response_is_simple_and_standardized(client):
    resp = client.get("/api/auth-expired")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"] == "Authentication required"


def test_csrf_blocks_mutating_session_request_without_header(client):
    client.get("/api/bootstrap")
    resp = client.post("/api/private-write", json={"x": 1})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["error"]["code"] == "csrf_failed"


def test_csrf_accepts_valid_session_cookie_and_header(client):
    client.get("/api/bootstrap")
    with client.session_transaction() as sess:
        csrf_token = sess["_csrf_token"]

    resp = client.post(
        "/api/private-write",
        json={"x": 1},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200


def test_csrf_exempts_public_login_endpoint(client):
    client.get("/api/bootstrap")
    resp = client.post("/api/auth/login", json={"username": "u", "password": "p"})
    assert resp.status_code == 200


def test_csrf_exempts_signed_webhook(client):
    client.get("/api/bootstrap")
    resp = client.post(
        "/api/triggers/webhook/signed",
        json={"event": "ping"},
        headers={"X-Signature": "sig"},
    )
    assert resp.status_code == 200


def test_csrf_exempts_bearer_requests(client):
    client.get("/api/bootstrap")
    resp = client.post(
        "/api/private-write",
        json={"x": 1},
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200


def test_rate_limit_returns_structured_429_with_retry_after():
    app = _make_app(rate_limit_max_requests=2)
    client = app.test_client()

    first = client.get("/api/knowledge/v1/ping")
    second = client.get("/api/knowledge/v1/ping")
    third = client.get("/api/knowledge/v1/ping")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429

    body = third.get_json()
    assert body["ok"] is False
    assert body["error"]["status"] == 429
    assert body["error"]["code"] == "too_many_requests"
    assert int(third.headers["Retry-After"]) >= 1

