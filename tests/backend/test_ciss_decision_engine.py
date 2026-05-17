import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def seeded_decision_dw(monkeypatch, tmp_path):
    db_path = tmp_path / "ciss_decision.db"

    import ciss_analytics.schema as schema
    from ciss_analytics.loader import (
        carregar_estoque,
        carregar_itens_venda,
        carregar_produtos,
        carregar_vendas,
        materializar_kpis_diarios,
    )

    monkeypatch.setenv("CISS_DW_PATH", str(db_path))
    monkeypatch.delenv("CISS_DW_DSN", raising=False)
    monkeypatch.delenv("CISS_DATABASE_URL", raising=False)
    schema.dispose_dw_engine()
    conn = schema.init_dw()
    conn.close()

    carregar_produtos(
        [
            {
                "id_produto": "P1",
                "codigo": "CIM-50",
                "codigo_produto": "CIM-50",
                "nome": "Cimento 50kg",
                "nome_produto": "Cimento 50kg",
                "nome_categoria": "Materiais basicos",
                "preco_custo": 30.0,
                "preco_venda": 45.0,
                "ativo": 1,
            },
            {
                "id_produto": "P2",
                "codigo": "ARG-20",
                "codigo_produto": "ARG-20",
                "nome": "Argamassa 20kg",
                "nome_produto": "Argamassa 20kg",
                "nome_categoria": "Materiais basicos",
                "preco_custo": 12.0,
                "preco_venda": 21.0,
                "ativo": 1,
            },
        ]
    )
    carregar_vendas(
        [
            {
                "id_venda": "V1",
                "data_venda": "2026-05-01",
                "id_vendedor": "S1",
                "nome_vendedor": "Ana",
                "total_bruto": 450.0,
                "total_desconto": 0.0,
                "total_liquido": 450.0,
                "num_itens": 1,
                "status": "confirmada",
            },
            {
                "id_venda": "V2",
                "data_venda": "2026-05-02",
                "id_vendedor": "S2",
                "nome_vendedor": "Bruno",
                "total_bruto": 210.0,
                "total_desconto": 10.0,
                "total_liquido": 200.0,
                "num_itens": 1,
                "status": "confirmada",
            },
            {
                "id_venda": "V3",
                "data_venda": "2026-05-03",
                "id_vendedor": "S1",
                "nome_vendedor": "Ana",
                "total_bruto": 90.0,
                "total_desconto": 0.0,
                "total_liquido": 90.0,
                "num_itens": 1,
                "status": "confirmada",
            },
        ]
    )
    carregar_itens_venda(
        [
            {
                "id_item": "I1",
                "id_venda": "V1",
                "id_produto": "P1",
                "codigo_produto": "CIM-50",
                "nome_produto": "Cimento 50kg",
                "nome_categoria": "Materiais basicos",
                "id_vendedor": "S1",
                "data_venda": "2026-05-01",
                "quantidade": 10.0,
                "preco_unitario": 45.0,
                "total_item": 450.0,
                "desconto_item": 0.0,
                "desconto_val": 0.0,
                "per_margem": 33.0,
                "val_lucro": 150.0,
            },
            {
                "id_item": "I2",
                "id_venda": "V2",
                "id_produto": "P2",
                "codigo_produto": "ARG-20",
                "nome_produto": "Argamassa 20kg",
                "nome_categoria": "Materiais basicos",
                "id_vendedor": "S2",
                "data_venda": "2026-05-02",
                "quantidade": 10.0,
                "preco_unitario": 21.0,
                "total_item": 200.0,
                "desconto_item": 10.0,
                "desconto_val": 10.0,
                "per_margem": 40.0,
                "val_lucro": 90.0,
            },
            {
                "id_item": "I3",
                "id_venda": "V3",
                "id_produto": "P1",
                "codigo_produto": "CIM-50",
                "nome_produto": "Cimento 50kg",
                "nome_categoria": "Materiais basicos",
                "id_vendedor": "S1",
                "data_venda": "2026-05-03",
                "quantidade": 2.0,
                "preco_unitario": 45.0,
                "total_item": 90.0,
                "desconto_item": 0.0,
                "desconto_val": 0.0,
                "per_margem": 33.0,
                "val_lucro": 30.0,
            },
        ]
    )
    carregar_estoque(
        [
            {
                "id_estoque": "P1_D1",
                "id_produto": "P1",
                "codigo_produto": "CIM-50",
                "nome_produto": "Cimento 50kg",
                "id_deposito": "D1",
                "nome_deposito": "Loja",
                "quantidade": 1.0,
                "estoque_minimo": 5.0,
                "consumo_medio_dia": 1.2,
                "dias_cobertura": 1.0,
                "nivel_alerta": "critico",
                "data_ruptura_prev": "2026-05-04",
                "atualizado_em": "2026-05-03",
            }
        ]
    )
    materializar_kpis_diarios()
    yield db_path
    schema.dispose_dw_engine()


def test_manager_snapshot_answers_core_business_questions(seeded_decision_dw):
    from ciss_analytics.manager_analytics import build_manager_snapshot

    snapshot = build_manager_snapshot(period_days=30, limit=5)

    assert snapshot["resumo"]["total_vendas"] == 740.0
    assert snapshot["resumo"]["transacoes"] == 3
    assert snapshot["resumo"]["ticket_mediano"] == 200.0
    assert snapshot["produtos"]["top_vendas"][0]["id_produto"] == "P1"
    assert snapshot["vendedores"]["ranking"][0]["nome_vendedor"] == "Ana"
    assert snapshot["estoque"]["resumo_alertas"]["critico"] == 1
    assert snapshot["estatistica"]["clusters_produtos"][0]["cluster"] in {"campeoes_de_receita", "alto_giro"}
    assert snapshot["preditivo"]["status"] == "ok"
    assert snapshot["preditivo"]["total_previsto_7_dias"] >= 0


def test_data_agent_answers_and_records_question(seeded_decision_dw):
    from ciss_analytics.data_agent import answer_question, recent_questions

    answer = answer_question("Quais produtos mais vendem?", period_days=30)

    assert answer["ok"] is True
    assert answer["topic"] == "produtos"
    assert "Cimento 50kg" in answer["answer"]

    prediction = answer_question("Qual a previsao de vendas?", period_days=30)
    assert prediction["topic"] == "preditivo"
    assert "previsao de 30 dias" in prediction["answer"]

    history = recent_questions(limit=5)
    assert history
    assert history[0]["topic"] == "preditivo"
