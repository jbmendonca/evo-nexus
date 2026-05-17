import os
import sys
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


class _Resp500:
    status_code = 500
    text = "upstream failure"

    def json(self):
        return {}


class _CountingIntegrimClient:
    def __init__(self):
        self.calls = 0

    def finalize_purchase_order(self, payload):
        self.calls += 1
        return {"ok": True, "response": {"order_id": "PO-123"}, "payload": payload}


@pytest.fixture()
def temp_dw(monkeypatch, tmp_path):
    db_path = tmp_path / "ciss_dw_test.db"

    import ciss_analytics.schema as schema

    monkeypatch.setenv("CISS_DW_PATH", str(db_path))
    monkeypatch.delenv("CISS_DW_DSN", raising=False)
    monkeypatch.delenv("CISS_DATABASE_URL", raising=False)
    schema.dispose_dw_engine()
    conn = schema.init_dw()
    conn.close()

    # Bypass static validation constants in routes during tests.
    import routes.ciss_purchasing as ciss_route

    monkeypatch.setattr(ciss_route, "validate_config", lambda: [])
    yield db_path
    schema.dispose_dw_engine()


@pytest.fixture()
def seeded_dw(temp_dw):
    import ciss_analytics.schema as schema

    conn = schema.get_dw_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO dw_produtos (id_produto, codigo, nome, preco_custo, nome_categoria, ativo)
            VALUES ('P1', 'SKU-P1', 'Produto P1', 10.0, 'Mercearia', 1)
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO dw_estoque
                (id_estoque, id_produto, codigo_produto, nome_produto, id_deposito, nome_deposito,
                 quantidade, estoque_minimo, atualizado_em)
            VALUES
                ('P1_D1', 'P1', 'SKU-P1', 'Produto P1', 'D1', 'Depósito 1', 3, 5, datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO dw_vendas
                (id_venda, data_venda, total_bruto, total_desconto, total_liquido, status)
            VALUES
                ('V1', date('now', '-1 day'), 300, 0, 300, 'confirmada')
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO dw_itens_venda
                (id_item, id_venda, id_produto, quantidade, preco_unitario, total_item)
            VALUES
                ('I1', 'V1', 'P1', 40, 10, 400)
            """
        )
        conn.commit()
    finally:
        conn.close()
    return temp_dw


@pytest.fixture()
def app(seeded_dw):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["LOGIN_DISABLED"] = True

    login_manager = LoginManager()
    login_manager.init_app(app)

    from routes.ciss_purchasing import bp as purchasing_bp

    app.register_blueprint(purchasing_bp)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_refresh_drafts_generates_purchase_draft(seeded_dw):
    from ciss_analytics.purchasing import PurchasingEngine, PurchasingSettings

    engine = PurchasingEngine(
        settings=PurchasingSettings(
            lookback_days=10,
            lead_time_days=5,
            safety_days=2,
            max_items=50,
            min_daily_demand=0.1,
        )
    )
    result = engine.refresh_drafts()

    assert result["ok"] is True
    assert result["generated"] >= 1
    assert len(result["drafts"]) >= 1
    draft = result["drafts"][0]
    assert draft["draft_key"] == "purchase:P1"
    assert draft["quantidade_sugerida"] > 0
    assert draft["ponto_pedido"] > draft["estoque_atual"]


def test_approval_is_idempotent_on_replay(seeded_dw):
    from ciss_analytics.purchasing import PurchasingEngine, PurchasingSettings
    from ciss_analytics.purchase_approvals import PurchaseApprovalService

    engine = PurchasingEngine(
        settings=PurchasingSettings(
            lookback_days=10,
            lead_time_days=5,
            safety_days=2,
            max_items=10,
            min_daily_demand=0.1,
        )
    )
    draft_key = engine.refresh_drafts()["drafts"][0]["draft_key"]

    fake_integrim = _CountingIntegrimClient()
    service = PurchaseApprovalService(integrim_client=fake_integrim)
    first = service.approve_draft(
        draft_key,
        idempotency_key="idem-approve-0001",
        actor="gerente.teste",
    )
    second = service.approve_draft(
        draft_key,
        idempotency_key="idem-approve-0001",
        actor="gerente.teste",
    )

    assert first["ok"] is True
    assert first["status"] == "approved"
    assert fake_integrim.calls == 1
    assert second["ok"] is True
    assert second["idempotency_replay"] is True


def test_integrim_retry_and_circuit_breaker_open(seeded_dw):
    from ciss_analytics.purchasing import (
        CircuitOpenError,
        IntegrimCircuitBreaker,
        IntegrimPurchaseClient,
        IntegrationError,
    )

    class _Session:
        def post(self, *args, **kwargs):
            return _Resp500()

    breaker = IntegrimCircuitBreaker(failure_threshold=1, reset_timeout_sec=30)
    client = IntegrimPurchaseClient(
        base_url="http://integrim.local",
        api_key="x",
        session=_Session(),
        retry_attempts=1,
        backoff_base_sec=0.01,
        breaker=breaker,
        sleep_fn=lambda _x: None,
    )

    with pytest.raises(IntegrationError):
        client.finalize_purchase_order({"draft_key": "purchase:P1"})

    with pytest.raises(CircuitOpenError):
        client.finalize_purchase_order({"draft_key": "purchase:P1"})


def test_route_requires_idempotency_key_for_approval(client, seeded_dw):
    from ciss_analytics.purchasing import PurchasingEngine, PurchasingSettings

    engine = PurchasingEngine(
        settings=PurchasingSettings(
            lookback_days=10,
            lead_time_days=5,
            safety_days=2,
            max_items=10,
            min_daily_demand=0.1,
        )
    )
    draft_key = engine.refresh_drafts()["drafts"][0]["draft_key"]

    resp = client.post(f"/api/ciss/purchasing/drafts/{draft_key}/approve", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "missing_idempotency_key"


def test_route_alert_failure_returns_502(client, monkeypatch, seeded_dw):
    from ciss_analytics.purchasing import PurchasingEngine, PurchasingSettings
    import routes.ciss_purchasing as ciss_route

    engine = PurchasingEngine(
        settings=PurchasingSettings(
            lookback_days=10,
            lead_time_days=5,
            safety_days=2,
            max_items=10,
            min_daily_demand=0.1,
        )
    )
    engine.refresh_drafts()

    class _FailingEvolutionClient:
        def send_purchase_alert(self, _payload):
            from ciss_analytics.purchasing import IntegrationError

            raise IntegrationError("Evolution indisponível", retryable=True)

    monkeypatch.setattr(ciss_route, "EvolutionAlertClient", lambda: _FailingEvolutionClient())

    resp = client.post("/api/ciss/purchasing/drafts/alerts", json={"actor": "gerente"})
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["ok"] is False
    assert body["status"] == "alert_failed"


def test_route_adjust_and_cancel_flow(client, seeded_dw):
    from ciss_analytics.purchasing import PurchasingEngine, PurchasingSettings

    engine = PurchasingEngine(
        settings=PurchasingSettings(
            lookback_days=10,
            lead_time_days=5,
            safety_days=2,
            max_items=10,
            min_daily_demand=0.1,
        )
    )
    draft_key = engine.refresh_drafts()["drafts"][0]["draft_key"]

    adjust = client.post(
        f"/api/ciss/purchasing/drafts/{draft_key}/adjust",
        json={"quantity": 17, "actor": "compras.manager"},
    )
    assert adjust.status_code == 200
    adjust_body = adjust.get_json()
    assert adjust_body["status"] == "adjusted"
    assert adjust_body["quantidade_ajustada"] == 17

    cancel = client.post(
        f"/api/ciss/purchasing/drafts/{draft_key}/cancel",
        json={"reason": "Fornecedor sem prazo", "actor": "compras.manager"},
    )
    assert cancel.status_code == 200
    cancel_body = cancel.get_json()
    assert cancel_body["status"] == "cancelled"
