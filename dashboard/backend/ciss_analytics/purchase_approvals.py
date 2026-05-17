"""
Fluxo de aprovação/cancelamento/ajuste de pedidos de compra.

Inclui idempotência para aprovação 1-clique e persistência de resultado.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .purchasing import (
    IntegrimPurchaseClient,
    IntegrationError,
    PurchasingEngine,
    _ensure_purchase_tables,
    _to_float,
    _utc_now_iso,
    get_integrim_breaker,
    serialize_payload,
)
from .schema import get_dw_conn

logger = logging.getLogger(__name__)


def _decode_payload(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


class PurchaseApprovalService:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], Any] = get_dw_conn,
        integrim_client: IntegrimPurchaseClient | None = None,
    ) -> None:
        self.conn_factory = conn_factory
        self.integrim_client = integrim_client or IntegrimPurchaseClient()

    def get_draft(self, draft_key: str) -> dict[str, Any] | None:
        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            row = conn.execute(
                "SELECT * FROM dw_purchase_drafts WHERE draft_key = ?",
                (draft_key,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def adjust_draft(
        self,
        draft_key: str,
        *,
        quantity: float,
        actor: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if quantity <= 0:
            return {"ok": False, "error": "Quantidade ajustada deve ser maior que zero."}

        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            draft = conn.execute(
                "SELECT * FROM dw_purchase_drafts WHERE draft_key = ?",
                (draft_key,),
            ).fetchone()
            if not draft:
                return {"ok": False, "error": "Rascunho não encontrado.", "code": "not_found"}
            if draft["status"] in {"approved", "cancelled"}:
                return {"ok": False, "error": "Rascunho já finalizado.", "code": "already_finalized"}

            qty = round(float(quantity), 2)
            valor_estimado = round(qty * _to_float(draft["preco_custo"]), 2)
            now_iso = _utc_now_iso()

            conn.execute(
                """
                UPDATE dw_purchase_drafts
                SET quantidade_ajustada = ?,
                    valor_estimado = ?,
                    status = 'adjusted',
                    observacoes = COALESCE(?, observacoes),
                    ultima_acao_por = ?,
                    atualizado_em = ?
                WHERE draft_key = ?
                """,
                (qty, valor_estimado, note, actor, now_iso, draft_key),
            )
            conn.commit()

            return {
                "ok": True,
                "status": "adjusted",
                "draft_key": draft_key,
                "quantidade_ajustada": qty,
                "valor_estimado": valor_estimado,
            }
        finally:
            conn.close()

    def cancel_draft(
        self,
        draft_key: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            draft = conn.execute(
                "SELECT status FROM dw_purchase_drafts WHERE draft_key = ?",
                (draft_key,),
            ).fetchone()
            if not draft:
                return {"ok": False, "error": "Rascunho não encontrado.", "code": "not_found"}
            if draft["status"] == "approved":
                return {"ok": False, "error": "Pedido já aprovado e não pode ser cancelado.", "code": "already_approved"}
            if draft["status"] == "cancelled":
                return {"ok": True, "status": "cancelled", "draft_key": draft_key, "already_cancelled": True}

            now_iso = _utc_now_iso()
            conn.execute(
                """
                UPDATE dw_purchase_drafts
                SET status = 'cancelled',
                    motivo_cancelamento = ?,
                    ultima_acao_por = ?,
                    atualizado_em = ?
                WHERE draft_key = ?
                """,
                (reason, actor, now_iso, draft_key),
            )
            conn.commit()
            return {"ok": True, "status": "cancelled", "draft_key": draft_key}
        finally:
            conn.close()

    def approve_draft(
        self,
        draft_key: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        key = (idempotency_key or "").strip()
        if len(key) < 8:
            return {"ok": False, "error": "Idempotency key inválida.", "code": "invalid_idempotency_key"}

        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            existing = conn.execute(
                """
                SELECT idempotency_key, draft_key, status, response_payload
                FROM dw_purchase_idempotency
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()

            if existing:
                if existing["draft_key"] != draft_key:
                    return {
                        "ok": False,
                        "code": "idempotency_conflict",
                        "error": "Idempotency key já usada para outro rascunho.",
                    }
                payload = _decode_payload(existing["response_payload"])
                if existing["status"] in {"completed", "failed"}:
                    payload["idempotency_replay"] = True
                    return payload
                if existing["status"] == "processing":
                    return {
                        "ok": False,
                        "code": "approval_in_progress",
                        "error": "Aprovação já está em processamento para esta chave.",
                    }

            now_iso = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO dw_purchase_idempotency
                    (idempotency_key, draft_key, status, response_payload, updated_at)
                VALUES (?, ?, 'processing', NULL, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    status = 'processing',
                    updated_at = excluded.updated_at
                """,
                (key, draft_key, now_iso),
            )

            draft_row = conn.execute(
                "SELECT * FROM dw_purchase_drafts WHERE draft_key = ?",
                (draft_key,),
            ).fetchone()
            if not draft_row:
                fail = {"ok": False, "error": "Rascunho não encontrado.", "code": "not_found"}
                self._finalize_idempotency(conn, key, draft_key, "failed", fail)
                conn.commit()
                return fail

            draft = dict(draft_row)
            if draft["status"] == "approved":
                done = {
                    "ok": True,
                    "status": "approved",
                    "draft_key": draft_key,
                    "message": "Rascunho já estava aprovado.",
                    "already_approved": True,
                }
                self._finalize_idempotency(conn, key, draft_key, "completed", done)
                conn.commit()
                return done
            if draft["status"] == "cancelled":
                fail = {"ok": False, "error": "Rascunho cancelado não pode ser aprovado.", "code": "cancelled"}
                self._finalize_idempotency(conn, key, draft_key, "failed", fail)
                conn.commit()
                return fail

            qty = _to_float(draft.get("quantidade_ajustada")) or _to_float(draft.get("quantidade_sugerida"))
            if qty <= 0:
                fail = {"ok": False, "error": "Rascunho sem quantidade válida para aprovação.", "code": "invalid_quantity"}
                self._finalize_idempotency(conn, key, draft_key, "failed", fail)
                conn.commit()
                return fail

            order_payload = {
                "draft_key": draft_key,
                "idempotency_key": key,
                "product": {
                    "id_produto": draft.get("id_produto"),
                    "codigo_produto": draft.get("codigo_produto"),
                    "nome_produto": draft.get("nome_produto"),
                },
                "quantity": round(qty, 2),
                "estimated_value": round(_to_float(draft.get("valor_estimado")), 2),
                "requested_by": actor,
                "note": note,
            }

            # Persistir estado "processing" antes da chamada externa.
            conn.commit()
        finally:
            conn.close()

        try:
            integrim_result = self.integrim_client.finalize_purchase_order(order_payload)
        except IntegrationError as exc:
            breaker = get_integrim_breaker().snapshot()
            result = {
                "ok": False,
                "status": "manager_attention",
                "draft_key": draft_key,
                "code": "integrim_failure",
                "message": str(exc),
                "retryable": bool(exc.retryable),
                "breaker": breaker,
            }
            self._persist_approval_failure(
                draft_key=draft_key,
                idempotency_key=key,
                actor=actor,
                result=result,
            )
            return result

        order_id = (
            integrim_result.get("response", {}).get("order_id")
            or integrim_result.get("response", {}).get("id")
            or integrim_result.get("response", {}).get("pedido_id")
        )
        success = {
            "ok": True,
            "status": "approved",
            "draft_key": draft_key,
            "integrim": integrim_result,
            "integrim_order_id": order_id,
            "message": "Pedido aprovado e enviado ao Integrim.",
        }
        self._persist_approval_success(
            draft_key=draft_key,
            idempotency_key=key,
            actor=actor,
            note=note,
            integrim_order_id=order_id,
            payload=success,
        )
        return success

    def _persist_approval_success(
        self,
        *,
        draft_key: str,
        idempotency_key: str,
        actor: str | None,
        note: str | None,
        integrim_order_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            now_iso = _utc_now_iso()
            conn.execute(
                """
                UPDATE dw_purchase_drafts
                SET status = 'approved',
                    integrim_pedido_id = ?,
                    observacoes = COALESCE(?, observacoes),
                    ultima_acao_por = ?,
                    atualizado_em = ?
                WHERE draft_key = ?
                """,
                (integrim_order_id, note, actor, now_iso, draft_key),
            )
            self._finalize_idempotency(conn, idempotency_key, draft_key, "completed", payload)
            conn.commit()
        finally:
            conn.close()

    def _persist_approval_failure(
        self,
        *,
        draft_key: str,
        idempotency_key: str,
        actor: str | None,
        result: dict[str, Any],
    ) -> None:
        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            now_iso = _utc_now_iso()
            conn.execute(
                """
                UPDATE dw_purchase_drafts
                SET status = CASE
                    WHEN status = 'approved' THEN 'approved'
                    WHEN status = 'cancelled' THEN 'cancelled'
                    ELSE 'manager_attention'
                END,
                    ultima_acao_por = ?,
                    atualizado_em = ?
                WHERE draft_key = ?
                """,
                (actor, now_iso, draft_key),
            )
            self._finalize_idempotency(conn, idempotency_key, draft_key, "failed", result)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _finalize_idempotency(
        conn: Any,
        idempotency_key: str,
        draft_key: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        now_iso = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO dw_purchase_idempotency
                (idempotency_key, draft_key, status, response_payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                status = excluded.status,
                response_payload = excluded.response_payload,
                updated_at = excluded.updated_at
            """,
            (idempotency_key, draft_key, status, serialize_payload(payload), now_iso),
        )


def get_services() -> tuple[PurchasingEngine, PurchaseApprovalService]:
    engine = PurchasingEngine()
    approvals = PurchaseApprovalService()
    return engine, approvals
