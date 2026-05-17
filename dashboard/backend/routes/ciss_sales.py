"""Flask routes for CISS omnichannel sales webhooks."""

from __future__ import annotations

import os
import secrets
from typing import Any

from flask import Blueprint, jsonify, request

from ciss_analytics.sales_agent import AgentResponse, get_default_agent


bp = Blueprint("ciss_sales", __name__, url_prefix="/api/ciss/sales")


@bp.route("/webhook/evolution", methods=["POST"])
def evolution_webhook():
    """Receive Evolution API webhook events and run the sales agent."""
    auth_error = _validate_webhook_secret()
    if auth_error:
        status_code = 503 if auth_error.status == "misconfigured" else 401
        return _json(auth_error, status_code)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json(
            AgentResponse(
                ok=False,
                status="invalid_payload",
                message="Expected a JSON object payload.",
                error="payload_not_object",
            ),
            400,
        )

    try:
        result = get_default_agent().handle_webhook(payload)
    except Exception as exc:
        return _json(
            AgentResponse(
                ok=False,
                status="internal_error",
                message="Failed to process CISS sales webhook.",
                error=str(exc),
            ),
            500,
        )

    http_status = 400 if result.status == "invalid_payload" else 200
    return _json(result, http_status)


@bp.route("/handoff/<path:chat_id>", methods=["DELETE"])
def resume_automation(chat_id: str):
    """Resume automation for a chat after a human handoff."""
    auth_error = _validate_webhook_secret()
    if auth_error:
        status_code = 503 if auth_error.status == "misconfigured" else 401
        return _json(auth_error, status_code)
    agent = get_default_agent()
    agent.suspension_store.resume(chat_id)
    return jsonify(
        {
            "ok": True,
            "status": "resumed",
            "message": "Automation resumed for chat.",
            "chat_id": chat_id,
        }
    )


def _validate_webhook_secret() -> AgentResponse | None:
    expected = os.environ.get("CISS_SALES_WEBHOOK_SECRET", "").strip()
    if not expected:
        return AgentResponse(
            ok=False,
            status="misconfigured",
            message="CISS sales webhook secret is not configured.",
            error="missing_webhook_secret",
        )
    supplied = (
        request.headers.get("X-Ciss-Sales-Secret")
        or request.headers.get("X-Webhook-Secret")
        or request.args.get("secret")
        or ""
    ).strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        return AgentResponse(
            ok=False,
            status="unauthorized",
            message="Invalid CISS sales webhook secret.",
            error="invalid_webhook_secret",
        )
    return None


def _json(response: AgentResponse, status_code: int):
    payload: dict[str, Any] = response.to_dict()
    return jsonify(payload), status_code
