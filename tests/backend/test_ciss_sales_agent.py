from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from ciss_analytics.integrim_sales import DWCatalogService
from ciss_analytics.omnichannel import normalize_evolution_webhook
from ciss_analytics.sales_agent import (
    AgentResponse,
    FallbackSalesLLM,
    InMemorySuspensionStore,
    RequestedItem,
    SalesAgent,
    SalesIntent,
)


class StaticProvider:
    def __init__(self, intent: SalesIntent) -> None:
        self.intent = intent
        self.calls = 0

    def analyze(self, text, context):
        self.calls += 1
        return self.intent


class FailingProvider:
    def analyze(self, text, context):
        raise RuntimeError("primary down")


def _evolution_text_payload(text: str, chat_id: str = "559999999999@s.whatsapp.net") -> dict:
    return {
        "event": "messages.upsert",
        "instance": "paubrasil",
        "data": {
            "key": {"remoteJid": chat_id, "id": "MSG1", "fromMe": False},
            "pushName": "Cliente Teste",
            "message": {"conversation": text},
        },
    }


def _catalog(tmp_path, quantity: float = 10.0) -> DWCatalogService:
    db_path = tmp_path / "ciss_sales.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE dw_produtos (
            id_produto TEXT PRIMARY KEY,
            codigo TEXT,
            nome TEXT,
            preco_venda REAL,
            unidade TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dw_estoque (
            id_estoque TEXT PRIMARY KEY,
            id_produto TEXT,
            codigo_produto TEXT,
            nome_produto TEXT,
            quantidade REAL,
            nome_deposito TEXT,
            atualizado_em TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO dw_produtos VALUES (?, ?, ?, ?, ?)",
        ("P1", "CIM-50", "Cimento CP II 50kg", 39.9, "SC"),
    )
    conn.execute(
        "INSERT INTO dw_estoque VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        ("P1_D1", "P1", "CIM-50", "Cimento CP II 50kg", quantity, "Loja"),
    )
    conn.commit()
    conn.close()

    def factory():
        new_conn = sqlite3.connect(db_path)
        new_conn.row_factory = sqlite3.Row
        return new_conn

    return DWCatalogService(conn_factory=factory)


def test_normalizes_evolution_text_message():
    result = normalize_evolution_webhook(_evolution_text_payload(" Quero 2 cimento "))

    assert result.status == "ok"
    assert result.incoming is not None
    assert result.incoming.chat_id == "559999999999@s.whatsapp.net"
    assert result.incoming.text == "Quero 2 cimento"
    assert result.incoming.instance == "paubrasil"


def test_audio_without_transcriber_returns_needs_transcription(monkeypatch):
    monkeypatch.delenv("CISS_AUDIO_TRANSCRIBE_PROVIDER", raising=False)
    payload = {
        "data": {
            "key": {"remoteJid": "55@s.whatsapp.net", "id": "AUD1", "fromMe": False},
            "message": {"audioMessage": {"mimetype": "audio/ogg", "url": "https://example/audio.ogg"}},
        }
    }

    result = normalize_evolution_webhook(payload)

    assert result.status == "needs_transcription"
    assert result.incoming is not None
    assert result.incoming.message_type == "audio"


def test_llm_fallback_uses_secondary_provider():
    secondary = StaticProvider(
        SalesIntent(intent="catalog_query", items=[RequestedItem(query="cimento", quantity=1)])
    )
    llm = FallbackSalesLLM(primary=FailingProvider(), secondary=secondary)

    intent = llm.analyze("tem cimento?", {"chat_id": "55"})

    assert intent.intent == "catalog_query"
    assert intent.raw["fallback_from"] == "FailingProvider"
    assert secondary.calls == 1


def test_discount_request_triggers_handoff_and_suspends_chat(tmp_path):
    store = InMemorySuspensionStore()
    agent = SalesAgent(
        catalog=_catalog(tmp_path),
        llm=StaticProvider(SalesIntent(intent="catalog_query")),
        suspension_store=store,
    )

    first = agent.handle_webhook(_evolution_text_payload("Preciso de desconto nesse pedido"))
    second = agent.handle_webhook(_evolution_text_payload("Quero cimento"))

    assert first.status == "handoff"
    assert first.automation_suspended is True
    assert first.actions[0]["type"] == "handoff_to_human"
    assert second.status == "suspended"
    assert store.is_suspended("559999999999@s.whatsapp.net") is True


def test_confirmed_order_revalidates_stock_and_prepares_integrim_payload(tmp_path):
    agent = SalesAgent(
        catalog=_catalog(tmp_path, quantity=5),
        llm=StaticProvider(
            SalesIntent(intent="confirm_order", items=[RequestedItem(query="cimento", quantity=2)])
        ),
        suspension_store=InMemorySuspensionStore(),
    )

    result = agent.handle_webhook(_evolution_text_payload("Confirmo 2 cimento"))

    assert result.status == "sale_payload_ready"
    payload = result.data["integrim_sale"]
    assert payload["service"] == "post_pedido_venda"
    assert payload["body"]["chat_id"] == "559999999999@s.whatsapp.net"
    assert payload["body"]["itens"][0]["idproduto"] == "P1"
    assert payload["body"]["itens"][0]["quantidade"] == 2


def test_out_of_stock_blocks_sale_payload(tmp_path):
    agent = SalesAgent(
        catalog=_catalog(tmp_path, quantity=1),
        llm=StaticProvider(
            SalesIntent(intent="confirm_order", items=[RequestedItem(query="cimento", quantity=2)])
        ),
        suspension_store=InMemorySuspensionStore(),
    )

    result = agent.handle_webhook(_evolution_text_payload("Confirmo 2 cimento"))

    assert result.status == "out_of_stock"
    assert "integrim_sale" not in result.data
    assert result.data["stock"]["issues"][0]["available"] == 1


def test_ciss_sales_route_returns_structured_json(monkeypatch):
    from routes import ciss_sales

    class StubAgent:
        def handle_webhook(self, payload):
            return AgentResponse(
                ok=True,
                status="quote_ready",
                message="ok",
                chat_id="55@s.whatsapp.net",
                reply="quote",
            )

    monkeypatch.setenv("CISS_SALES_WEBHOOK_SECRET", "super-secret")
    monkeypatch.setattr(ciss_sales, "get_default_agent", lambda: StubAgent())
    app = Flask(__name__)
    app.register_blueprint(ciss_sales.bp)

    response = app.test_client().post(
        "/api/ciss/sales/webhook/evolution",
        json=_evolution_text_payload("tem cimento?"),
        headers={"X-Ciss-Sales-Secret": "super-secret"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["status"] == "quote_ready"
    assert body["chat_id"] == "55@s.whatsapp.net"


def test_ciss_sales_route_requires_secret_config(monkeypatch):
    from routes import ciss_sales

    monkeypatch.delenv("CISS_SALES_WEBHOOK_SECRET", raising=False)
    app = Flask(__name__)
    app.register_blueprint(ciss_sales.bp)

    response = app.test_client().post(
        "/api/ciss/sales/webhook/evolution",
        json=_evolution_text_payload("tem cimento?"),
    )

    assert response.status_code == 503
    body = response.get_json()
    assert body["ok"] is False
    assert body["status"] == "misconfigured"
    assert body["error"] == "missing_webhook_secret"


def test_ciss_sales_route_rejects_invalid_secret(monkeypatch):
    from routes import ciss_sales

    monkeypatch.setenv("CISS_SALES_WEBHOOK_SECRET", "super-secret")
    app = Flask(__name__)
    app.register_blueprint(ciss_sales.bp)

    response = app.test_client().post(
        "/api/ciss/sales/webhook/evolution",
        json=_evolution_text_payload("tem cimento?"),
        headers={"X-Ciss-Sales-Secret": "wrong-secret"},
    )

    assert response.status_code == 401
    body = response.get_json()
    assert body["ok"] is False
    assert body["status"] == "unauthorized"
    assert body["error"] == "invalid_webhook_secret"
