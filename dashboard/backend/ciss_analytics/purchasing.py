"""
Motor preditivo de compras para CISS Analytics.

Responsabilidades:
- Cruza estoque e vendas no DW e gera/atualiza rascunhos de compra.
- Mantém clientes isolados para Evolution (alertas) e Integrim (finalização).
- Aplica retry com backoff e circuit breaker para integrações do Integrim.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

import requests

from .schema import get_dw_conn

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PurchasingSettings:
    lookback_days: int = _to_int(os.environ.get("CISS_PURCHASE_LOOKBACK_DAYS"), 30)
    lead_time_days: int = _to_int(os.environ.get("CISS_PURCHASE_LEAD_TIME_DAYS"), 7)
    safety_days: int = _to_int(os.environ.get("CISS_PURCHASE_SAFETY_DAYS"), 3)
    max_items: int = _to_int(os.environ.get("CISS_PURCHASE_MAX_ITEMS"), 200)
    min_daily_demand: float = _to_float(os.environ.get("CISS_PURCHASE_MIN_DAILY_DEMAND"), 0.05)


class IntegrationError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class CircuitOpenError(IntegrationError):
    pass


class IntegrimCircuitBreaker:
    def __init__(
        self,
        failure_threshold: int,
        reset_timeout_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._reset_timeout_sec = max(1.0, reset_timeout_sec)
        self._clock = clock
        self._lock = Lock()
        self._failures = 0
        self._opened_until = 0.0
        self._last_error: str | None = None

    def before_call(self) -> None:
        with self._lock:
            now = self._clock()
            if self._opened_until > now:
                wait_s = round(self._opened_until - now, 2)
                raise CircuitOpenError(
                    f"Integrim temporariamente bloqueado pelo circuit breaker. Tente novamente em {wait_s}s.",
                    retryable=True,
                )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_until = 0.0
            self._last_error = None

    def record_failure(self, error_msg: str) -> dict[str, Any]:
        with self._lock:
            self._failures += 1
            self._last_error = error_msg
            opened = False
            if self._failures >= self._failure_threshold:
                self._opened_until = self._clock() + self._reset_timeout_sec
                opened = True
            return {
                "failures": self._failures,
                "opened": opened,
                "opened_until_monotonic": self._opened_until,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            is_open = self._opened_until > now
            retry_after = max(0.0, self._opened_until - now) if is_open else 0.0
            return {
                "is_open": is_open,
                "retry_after_sec": round(retry_after, 2),
                "failures": self._failures,
                "last_error": self._last_error,
            }


_integrim_breaker = IntegrimCircuitBreaker(
    failure_threshold=_to_int(os.environ.get("CISS_PURCHASE_BREAKER_FAIL_THRESHOLD"), 3),
    reset_timeout_sec=_to_float(os.environ.get("CISS_PURCHASE_BREAKER_RESET_SEC"), 120.0),
)


def get_integrim_breaker() -> IntegrimCircuitBreaker:
    return _integrim_breaker


class EvolutionAlertClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout_sec: int = 12,
    ) -> None:
        self.base_url = (base_url or os.environ.get("EVOLUTION_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("EVOLUTION_API_KEY", "")
        self.timeout_sec = timeout_sec
        self.session = session or requests.Session()

    def send_purchase_alert(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise IntegrationError(
                "EVOLUTION_API_URL não configurada para envio de alertas.",
                retryable=False,
            )

        url = f"{self.base_url}/purchasing/alerts"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["apikey"] = self.api_key

        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout_sec)
        except requests.RequestException as exc:
            raise IntegrationError(f"Falha de rede no Evolution: {exc}") from exc

        if resp.status_code >= 400:
            retryable = resp.status_code >= 500 or resp.status_code == 429
            raise IntegrationError(
                f"Evolution retornou HTTP {resp.status_code}: {resp.text[:240]}",
                retryable=retryable,
            )

        try:
            data = resp.json()
        except ValueError:
            data = {"raw_response": resp.text[:240]}

        return {
            "ok": True,
            "provider": "evolution",
            "status_code": resp.status_code,
            "response": data,
        }


class IntegrimPurchaseClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout_sec: int | None = None,
        retry_attempts: int | None = None,
        backoff_base_sec: float | None = None,
        breaker: IntegrimCircuitBreaker | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = (base_url or os.environ.get("CISS_PURCHASE_INTEGRIM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("CISS_PURCHASE_INTEGRIM_API_KEY", "")
        self.timeout_sec = timeout_sec or _to_int(os.environ.get("CISS_PURCHASE_INTEGRIM_TIMEOUT_SEC"), 20)
        self.retry_attempts = max(1, retry_attempts or _to_int(os.environ.get("CISS_PURCHASE_INTEGRIM_RETRY_ATTEMPTS"), 3))
        self.backoff_base_sec = max(0.05, backoff_base_sec or _to_float(os.environ.get("CISS_PURCHASE_INTEGRIM_BACKOFF_BASE_SEC"), 0.8))
        self.breaker = breaker or get_integrim_breaker()
        self.sleep_fn = sleep_fn
        self.session = session or requests.Session()

    def finalize_purchase_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise IntegrationError(
                "CISS_PURCHASE_INTEGRIM_BASE_URL não configurada para finalizar pedido no Integrim.",
                retryable=False,
            )

        self.breaker.before_call()
        url = f"{self.base_url}/purchase-orders"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: IntegrationError | None = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = self.session.post(url, json=order_payload, headers=headers, timeout=self.timeout_sec)
                if 200 <= resp.status_code < 300:
                    self.breaker.record_success()
                    try:
                        parsed = resp.json()
                    except ValueError:
                        parsed = {"raw_response": resp.text[:240]}
                    return {
                        "ok": True,
                        "attempt": attempt,
                        "status_code": resp.status_code,
                        "response": parsed,
                    }

                retryable = resp.status_code >= 500 or resp.status_code == 429
                err = IntegrationError(
                    f"Integrim retornou HTTP {resp.status_code}: {resp.text[:240]}",
                    retryable=retryable,
                )
                if not retryable:
                    self.breaker.record_failure(str(err))
                    raise err
                last_error = err
                self.breaker.record_failure(str(err))
            except requests.RequestException as exc:
                last_error = IntegrationError(f"Falha de rede no Integrim: {exc}", retryable=True)
                self.breaker.record_failure(str(last_error))

            if attempt < self.retry_attempts:
                self.sleep_fn(self.backoff_base_sec * (2 ** (attempt - 1)))

        if last_error is None:
            last_error = IntegrationError("Falha desconhecida ao finalizar pedido no Integrim.", retryable=True)
        raise last_error


def _ensure_purchase_tables(conn: Any) -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS dw_purchase_drafts (
            draft_key            TEXT PRIMARY KEY,
            id_produto           TEXT NOT NULL,
            codigo_produto       TEXT,
            nome_produto         TEXT,
            nome_categoria       TEXT,
            estoque_atual        REAL NOT NULL DEFAULT 0,
            estoque_minimo       REAL NOT NULL DEFAULT 0,
            demanda_media_dia    REAL NOT NULL DEFAULT 0,
            dias_cobertura       REAL,
            ponto_pedido         REAL NOT NULL DEFAULT 0,
            quantidade_sugerida  REAL NOT NULL DEFAULT 0,
            quantidade_ajustada  REAL,
            preco_custo          REAL NOT NULL DEFAULT 0,
            valor_estimado       REAL NOT NULL DEFAULT 0,
            janela_dias          INTEGER NOT NULL DEFAULT 30,
            lead_time_dias       INTEGER NOT NULL DEFAULT 7,
            safety_dias          INTEGER NOT NULL DEFAULT 3,
            status               TEXT NOT NULL DEFAULT 'draft',
            alerta_enviado_em    TEXT,
            integrim_pedido_id   TEXT,
            motivo_cancelamento  TEXT,
            observacoes          TEXT,
            ultima_acao_por      TEXT,
            criado_em            TEXT NOT NULL DEFAULT (datetime('now')),
            atualizado_em        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dw_purchase_drafts_status
            ON dw_purchase_drafts(status);
        CREATE INDEX IF NOT EXISTS idx_dw_purchase_drafts_prod
            ON dw_purchase_drafts(id_produto);

        CREATE TABLE IF NOT EXISTS dw_purchase_idempotency (
            idempotency_key   TEXT PRIMARY KEY,
            draft_key         TEXT NOT NULL,
            status            TEXT NOT NULL,
            response_payload  TEXT,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dw_purchase_idempotency_draft
            ON dw_purchase_idempotency(draft_key);
    """
    for statement in ddl.split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)


def ensure_purchase_schema() -> None:
    conn = get_dw_conn()
    try:
        _ensure_purchase_tables(conn)
        conn.commit()
    finally:
        conn.close()


class PurchasingEngine:
    def __init__(
        self,
        *,
        settings: PurchasingSettings | None = None,
        conn_factory: Callable[[], Any] = get_dw_conn,
    ) -> None:
        self.settings = settings or PurchasingSettings()
        self.conn_factory = conn_factory

    def refresh_drafts(self, *, limit: int | None = None) -> dict[str, Any]:
        conn = self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            window_days = max(1, self.settings.lookback_days)
            lead_days = max(1, self.settings.lead_time_days)
            safety_days = max(0, self.settings.safety_days)
            cap = max(1, min(limit or self.settings.max_items, self.settings.max_items))
            cutoff = (date.today() - timedelta(days=window_days)).isoformat()

            candidates = conn.execute(
                """
                SELECT
                    e.id_produto,
                    COALESCE(e.codigo_produto, p.codigo) AS codigo_produto,
                    COALESCE(e.nome_produto, p.nome) AS nome_produto,
                    p.nome_categoria AS nome_categoria,
                    COALESCE(e.quantidade, 0) AS estoque_atual,
                    COALESCE(e.estoque_minimo, 0) AS estoque_minimo,
                    COALESCE(p.preco_custo, 0) AS preco_custo,
                    COALESCE(SUM(
                        CASE
                            WHEN v.status IS NULL OR v.status != 'cancelada' THEN i.quantidade
                            ELSE 0
                        END
                    ), 0) AS vendas_periodo
                FROM dw_estoque e
                LEFT JOIN dw_produtos p ON p.id_produto = e.id_produto
                LEFT JOIN dw_itens_venda i ON i.id_produto = e.id_produto
                LEFT JOIN dw_vendas v ON v.id_venda = i.id_venda AND v.data_venda >= ?
                GROUP BY e.id_produto
                ORDER BY vendas_periodo DESC, estoque_atual ASC
                """,
                (cutoff,),
            ).fetchall()

            generated = 0
            updated = 0
            skipped = 0
            touched_keys: set[str] = set()
            now_iso = _utc_now_iso()

            for row in candidates:
                id_produto = (row["id_produto"] or "").strip()
                if not id_produto:
                    skipped += 1
                    continue

                demanda_dia = _to_float(row["vendas_periodo"]) / float(window_days)
                if demanda_dia < self.settings.min_daily_demand:
                    skipped += 1
                    continue

                estoque_atual = max(0.0, _to_float(row["estoque_atual"]))
                estoque_minimo = max(0.0, _to_float(row["estoque_minimo"]))
                preco_custo = max(0.0, _to_float(row["preco_custo"]))
                ponto_pedido = max(estoque_minimo, demanda_dia * float(lead_days + safety_days))

                if estoque_atual >= ponto_pedido:
                    skipped += 1
                    continue

                qtd_sugerida = max(1.0, math.ceil(ponto_pedido - estoque_atual))
                valor_estimado = round(qtd_sugerida * preco_custo, 2)
                dias_cobertura = round(estoque_atual / demanda_dia, 2) if demanda_dia > 0 else None
                draft_key = f"purchase:{id_produto}"
                touched_keys.add(draft_key)

                existing = conn.execute(
                    "SELECT status FROM dw_purchase_drafts WHERE draft_key = ?",
                    (draft_key,),
                ).fetchone()
                if existing and existing["status"] in {"approved", "cancelled"}:
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO dw_purchase_drafts (
                        draft_key, id_produto, codigo_produto, nome_produto, nome_categoria,
                        estoque_atual, estoque_minimo, demanda_media_dia, dias_cobertura,
                        ponto_pedido, quantidade_sugerida, preco_custo, valor_estimado,
                        janela_dias, lead_time_dias, safety_dias, status, atualizado_em
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
                    ON CONFLICT(draft_key) DO UPDATE SET
                        codigo_produto = excluded.codigo_produto,
                        nome_produto = excluded.nome_produto,
                        nome_categoria = excluded.nome_categoria,
                        estoque_atual = excluded.estoque_atual,
                        estoque_minimo = excluded.estoque_minimo,
                        demanda_media_dia = excluded.demanda_media_dia,
                        dias_cobertura = excluded.dias_cobertura,
                        ponto_pedido = excluded.ponto_pedido,
                        quantidade_sugerida = excluded.quantidade_sugerida,
                        preco_custo = excluded.preco_custo,
                        valor_estimado = excluded.valor_estimado,
                        janela_dias = excluded.janela_dias,
                        lead_time_dias = excluded.lead_time_dias,
                        safety_dias = excluded.safety_dias,
                        status = CASE
                            WHEN dw_purchase_drafts.status IN ('approved', 'cancelled') THEN dw_purchase_drafts.status
                            ELSE 'draft'
                        END,
                        atualizado_em = excluded.atualizado_em
                    """,
                    (
                        draft_key,
                        id_produto,
                        row["codigo_produto"],
                        row["nome_produto"],
                        row["nome_categoria"],
                        estoque_atual,
                        estoque_minimo,
                        round(demanda_dia, 4),
                        dias_cobertura,
                        round(ponto_pedido, 2),
                        round(qtd_sugerida, 2),
                        round(preco_custo, 2),
                        valor_estimado,
                        window_days,
                        lead_days,
                        safety_days,
                        now_iso,
                    ),
                )
                if existing:
                    updated += 1
                else:
                    generated += 1

                if generated + updated >= cap:
                    break

            conn.commit()
            drafts = self.list_drafts(conn=conn, include_terminal=False, only_touched=touched_keys if touched_keys else None)
            return {
                "ok": True,
                "generated": generated,
                "updated": updated,
                "skipped": skipped,
                "drafts": drafts,
                "window_days": window_days,
                "cutoff_date": cutoff,
            }
        finally:
            conn.close()

    def list_drafts(
        self,
        *,
        conn: Any | None = None,
        status: str | None = None,
        include_terminal: bool = False,
        only_touched: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        owns_conn = conn is None
        conn = conn or self.conn_factory()
        try:
            _ensure_purchase_tables(conn)
            where = []
            params: list[Any] = []
            if status:
                where.append("status = ?")
                params.append(status)
            elif not include_terminal:
                where.append("status NOT IN ('approved', 'cancelled')")
            if only_touched:
                placeholders = ", ".join(["?"] * len(only_touched))
                where.append(f"draft_key IN ({placeholders})")
                params.extend(sorted(only_touched))

            sql = "SELECT * FROM dw_purchase_drafts"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY valor_estimado DESC, atualizado_em DESC"

            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]
        finally:
            if owns_conn:
                conn.close()


def build_alert_payload(drafts: list[dict[str, Any]], *, actor: str | None = None) -> dict[str, Any]:
    total = round(sum(_to_float(d.get("valor_estimado")) for d in drafts), 2)
    return {
        "type": "purchase_draft_alert",
        "created_at": _utc_now_iso(),
        "actor": actor,
        "draft_count": len(drafts),
        "total_estimated_value": total,
        "drafts": [
            {
                "draft_key": d.get("draft_key"),
                "id_produto": d.get("id_produto"),
                "nome_produto": d.get("nome_produto"),
                "quantidade_sugerida": d.get("quantidade_sugerida"),
                "quantidade_ajustada": d.get("quantidade_ajustada"),
                "valor_estimado": d.get("valor_estimado"),
                "dias_cobertura": d.get("dias_cobertura"),
                "ponto_pedido": d.get("ponto_pedido"),
            }
            for d in drafts
        ],
    }


def mark_alert_sent(draft_keys: list[str], *, actor: str | None = None) -> int:
    if not draft_keys:
        return 0
    conn = get_dw_conn()
    try:
        _ensure_purchase_tables(conn)
        now_iso = _utc_now_iso()
        placeholders = ", ".join(["?"] * len(draft_keys))
        cur = conn.execute(
            f"""
            UPDATE dw_purchase_drafts
            SET alerta_enviado_em = ?, ultima_acao_por = ?, status = CASE
                WHEN status IN ('draft', 'adjusted') THEN 'alerted'
                ELSE status
            END, atualizado_em = ?
            WHERE draft_key IN ({placeholders})
            """,
            (now_iso, actor, now_iso, *draft_keys),
        )
        conn.commit()
        return len(draft_keys)
    finally:
        conn.close()


def serialize_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
