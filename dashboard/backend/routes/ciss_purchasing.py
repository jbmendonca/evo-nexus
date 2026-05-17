"""
Rotas de compras preditivas CISS.

IMPORTANTE: este blueprint não é registrado aqui; a integração com app.py
fica a cargo do coordenador.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import login_required

from ciss_analytics.config import validate_config
from ciss_analytics.purchase_approvals import get_services
from ciss_analytics.purchasing import (
    EvolutionAlertClient,
    IntegrationError,
    build_alert_payload,
    get_integrim_breaker,
    mark_alert_sent,
)

bp = Blueprint("ciss_purchasing", __name__, url_prefix="/api/ciss/purchasing")


def _require_ciss_config(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        erros = validate_config()
        if erros:
            return jsonify({"ok": False, "erro": "CISS não configurado", "detalhes": erros}), 503
        return fn(*args, **kwargs)

    return wrapper


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _status_for_result(result: dict) -> int:
    if result.get("ok"):
        return 200
    code = result.get("code")
    if code in {"not_found"}:
        return 404
    if code in {"idempotency_conflict", "approval_in_progress", "already_approved"}:
        return 409
    if result.get("status") == "manager_attention":
        return 503
    return 400


@bp.route("/drafts", methods=["GET"])
@login_required
@_require_ciss_config
def list_drafts():
    refresh = _parse_bool(request.args.get("refresh"), default=True)
    status = request.args.get("status")
    limit_raw = request.args.get("limit")
    try:
        limit = int(limit_raw) if limit_raw else None
    except ValueError:
        return jsonify({"ok": False, "error": "limit inválido"}), 400

    engine, _ = get_services()
    if refresh:
        payload = engine.refresh_drafts(limit=limit)
    else:
        drafts = engine.list_drafts(status=status)
        payload = {"ok": True, "drafts": drafts, "generated": 0, "updated": 0, "skipped": 0}
    payload["breaker"] = get_integrim_breaker().snapshot()
    return jsonify(payload)


@bp.route("/drafts/alerts", methods=["POST"])
@login_required
@_require_ciss_config
def send_alerts():
    body = request.get_json(silent=True) or {}
    actor = body.get("actor")
    draft_keys = body.get("draft_keys") or body.get("draft_ids") or []
    if draft_keys and not isinstance(draft_keys, list):
        return jsonify({"ok": False, "error": "draft_keys deve ser uma lista."}), 400

    engine, _ = get_services()
    drafts = engine.list_drafts()
    if draft_keys:
        keys = {str(k) for k in draft_keys}
        drafts = [d for d in drafts if d.get("draft_key") in keys]
    if not drafts:
        return jsonify({"ok": False, "error": "Nenhum rascunho elegível para alerta."}), 404

    payload = build_alert_payload(drafts, actor=actor)
    client = EvolutionAlertClient()
    try:
        alert_result = client.send_purchase_alert(payload)
    except IntegrationError as exc:
        return (
            jsonify(
                {
                    "ok": False,
                    "status": "alert_failed",
                    "retryable": bool(exc.retryable),
                    "error": str(exc),
                }
            ),
            502 if exc.retryable else 400,
        )

    updated = mark_alert_sent([d["draft_key"] for d in drafts], actor=actor)
    return jsonify(
        {
            "ok": True,
            "alert_result": alert_result,
            "drafts_alerted": updated,
            "draft_keys": [d["draft_key"] for d in drafts],
        }
    )


@bp.route("/drafts/<path:draft_key>/approve", methods=["POST"])
@login_required
@_require_ciss_config
def approve_draft(draft_key: str):
    body = request.get_json(silent=True) or {}
    idempotency_key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
    if not idempotency_key:
        return jsonify({"ok": False, "code": "missing_idempotency_key", "error": "Idempotency-Key é obrigatório."}), 400

    actor = body.get("actor")
    note = body.get("note")
    _, approvals = get_services()
    result = approvals.approve_draft(
        draft_key,
        idempotency_key=str(idempotency_key),
        actor=actor,
        note=note,
    )
    return jsonify(result), _status_for_result(result)


@bp.route("/drafts/<path:draft_key>/cancel", methods=["POST"])
@login_required
@_require_ciss_config
def cancel_draft(draft_key: str):
    body = request.get_json(silent=True) or {}
    actor = body.get("actor")
    reason = body.get("reason")

    _, approvals = get_services()
    result = approvals.cancel_draft(draft_key, actor=actor, reason=reason)
    return jsonify(result), _status_for_result(result)


@bp.route("/drafts/<path:draft_key>/adjust", methods=["POST"])
@login_required
@_require_ciss_config
def adjust_draft(draft_key: str):
    body = request.get_json(silent=True) or {}
    if "quantity" not in body:
        return jsonify({"ok": False, "error": "Campo quantity é obrigatório."}), 400
    try:
        quantity = float(body["quantity"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "quantity inválido."}), 400

    actor = body.get("actor")
    note = body.get("note")
    _, approvals = get_services()
    result = approvals.adjust_draft(
        draft_key,
        quantity=quantity,
        actor=actor,
        note=note,
    )
    return jsonify(result), _status_for_result(result)
