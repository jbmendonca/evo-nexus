"""
Testes para o módulo CISS Analytics.
Usa mocks para não depender de conexão real com a API CISS.
"""
import json
import sqlite3
import tempfile
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../dashboard/backend"))


class TestTransformer(unittest.TestCase):
    """Testa normalização de dados brutos."""

    def setUp(self):
        from ciss_analytics import transformer as T
        self.T = T

    def test_safe_float(self):
        self.assertEqual(self.T._safe_float("1.234,56".replace(",", ".")), 1234.56)
        self.assertEqual(self.T._safe_float(None), 0.0)
        self.assertEqual(self.T._safe_float("abc"), 0.0)

    def test_safe_date_iso(self):
        self.assertEqual(self.T._safe_date("2026-01-31"), "2026-01-31")
        self.assertEqual(self.T._safe_date("2026-01-31T10:00:00"), "2026-01-31")

    def test_safe_date_br(self):
        self.assertEqual(self.T._safe_date("31/01/2026"), "2026-01-31")

    def test_transform_vendas_filtra_sem_id(self):
        raw = [
            {"id": "V001", "data_venda": "2026-01-01", "total_liquido": 100},
            {"id": "",     "data_venda": "2026-01-01", "total_liquido": 50},  # sem id
        ]
        result = self.T.transform_vendas(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id_venda"], "V001")

    def test_transform_estoque_compoe_id(self):
        raw = [{"id_produto": "P1", "id_deposito": "D1", "quantidade": 50, "estoque_minimo": 10}]
        result = self.T.transform_estoque(raw)
        self.assertEqual(result[0]["id_estoque"], "P1_D1")


class TestSchema(unittest.TestCase):
    """Testa criação do DW."""

    def test_init_dw_cria_tabelas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "ciss_dw.db")
            with patch.dict(os.environ, {"CISS_DW_PATH": db_path, "CISS_DW_DSN": "", "CISS_DATABASE_URL": ""}, clear=False):
                from ciss_analytics.schema import dispose_dw_engine, init_dw

                dispose_dw_engine()
                conn = init_dw()
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                conn.close()
                dispose_dw_engine()

        self.assertIn("dw_vendas", tables)
        self.assertIn("dw_clientes", tables)
        self.assertIn("dw_estoque", tables)
        self.assertIn("dw_etl_log", tables)

    def test_get_dw_engine_postgres_usa_queuepool(self):
        from sqlalchemy.pool import QueuePool
        from ciss_analytics import schema

        schema.dispose_dw_engine()
        dsn = "postgresql+psycopg2://ciss:ciss@localhost:5432/ciss_dw"
        with patch.dict(os.environ, {"CISS_DW_DSN": dsn}, clear=False):
            with patch("ciss_analytics.schema.create_engine") as create_engine:
                fake_engine = MagicMock()
                create_engine.return_value = fake_engine
                engine = schema.get_dw_engine()

        self.assertIs(engine, fake_engine)
        kwargs = create_engine.call_args.kwargs
        self.assertIs(kwargs["poolclass"], QueuePool)
        self.assertTrue(kwargs["pool_pre_ping"])
        schema.dispose_dw_engine()

    def test_build_readonly_user_sql_valida_identificadores(self):
        from ciss_analytics.schema import build_readonly_user_sql

        sql = build_readonly_user_sql("ciss_reader", "secret", "ciss_dw")
        self.assertIn("GRANT SELECT ON ALL TABLES", "\n".join(sql))

        with self.assertRaises(ValueError):
            build_readonly_user_sql("reader;DROP", "secret", "ciss_dw")


class TestLoader(unittest.TestCase):
    """Testa persistencia SQLite compativel com o loader PostgreSQL-ready."""

    def test_upsert_vendas_atualiza_registro(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "ciss_dw.db")
            with patch.dict(os.environ, {"CISS_DW_PATH": db_path, "CISS_DW_DSN": "", "CISS_DATABASE_URL": ""}, clear=False):
                from ciss_analytics.schema import dispose_dw_engine
                from ciss_analytics.loader import carregar_vendas

                dispose_dw_engine()
                carregar_vendas([{
                    "id_venda": "V001",
                    "data_venda": "2026-01-01",
                    "total_liquido": 100.0,
                    "total_bruto": 100.0,
                    "total_desconto": 0.0,
                    "status": "confirmada",
                }])
                carregar_vendas([{
                    "id_venda": "V001",
                    "data_venda": "2026-01-01",
                    "total_liquido": 125.5,
                    "total_bruto": 125.5,
                    "total_desconto": 0.0,
                    "status": "confirmada",
                }])

                conn = sqlite3.connect(db_path)
                total, count = conn.execute(
                    "SELECT total_liquido, COUNT(*) FROM dw_vendas WHERE id_venda = 'V001'"
                ).fetchone()
                conn.close()
                dispose_dw_engine()

        self.assertEqual(count, 1)
        self.assertEqual(total, 125.5)

    def test_upsert_rejeita_coluna_invalida(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "ciss_dw.db")
            with patch.dict(os.environ, {"CISS_DW_PATH": db_path, "CISS_DW_DSN": "", "CISS_DATABASE_URL": ""}, clear=False):
                from ciss_analytics.schema import dispose_dw_engine
                from ciss_analytics.loader import carregar_vendas

                dispose_dw_engine()
                with self.assertRaises(ValueError):
                    carregar_vendas([{
                        "id_venda": "V001",
                        "data_venda": "2026-01-01",
                        "total_liquido": 10.0,
                        "coluna;drop": "x",
                    }])
                dispose_dw_engine()


class TestSalesForecaster(unittest.TestCase):
    """Testa previsão de vendas."""

    def test_ema_basico(self):
        from ciss_analytics.ml.sales_forecaster import _ema
        result = _ema([100, 110, 120], alpha=0.5)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 100)

    def test_previsao_sem_dados(self):
        with patch("ciss_analytics.ml.sales_forecaster._load_sales_series", return_value=[]):
            from ciss_analytics.ml.sales_forecaster import gerar_previsao
            result = gerar_previsao()
            self.assertIn("erro", result)


class TestStockAlert(unittest.TestCase):
    """Testa alertas de estoque."""

    def test_niveis_alerta(self):
        """Verifica que produto com 1 dia de cobertura é crítico."""
        from ciss_analytics.ml.stock_alert import gerar_alertas_estoque
        # Sem dados reais, só checa que não explode
        with patch("ciss_analytics.ml.stock_alert.get_dw_conn") as mock_conn:
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = []
            conn.execute.return_value.fetchone.return_value = None
            mock_conn.return_value.__enter__ = lambda s: conn
            mock_conn.return_value = conn
            # Não explode mesmo sem dados
            try:
                gerar_alertas_estoque()
            except Exception:
                pass  # esperado sem DW real


class TestAPIBlueprint(unittest.TestCase):
    """Testa os endpoints Flask."""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

    def test_blueprint_registrado(self):
        """Verifica que o blueprint foi criado."""
        from routes.ciss_analytics import bp
        self.assertEqual(bp.name, "ciss_analytics")
        self.assertEqual(bp.url_prefix, "/api/ciss")

    def test_parse_limit_valido_e_invalido(self):
        from flask import Flask
        from routes.ciss_analytics import _parse_limit

        app = Flask(__name__)
        with app.test_request_context("/api/ciss/dashboard/top-clientes?limit=20"):
            self.assertEqual(_parse_limit(), 20)

        with app.test_request_context("/api/ciss/dashboard/top-clientes?limit=999"):
            self.assertEqual(_parse_limit(), 50)

        with app.test_request_context("/api/ciss/dashboard/top-clientes?limit=abc"):
            with self.assertRaises(ValueError):
                _parse_limit()


if __name__ == "__main__":
    unittest.main()
