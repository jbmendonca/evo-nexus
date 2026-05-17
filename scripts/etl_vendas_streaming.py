#!/usr/bin/env python3
"""ETL de vendas CISS em streaming.

Extrai get_pedido_venda_prod em paginas, persiste itens por lote e materializa
vendas agregadas no DW ao final. Isso evita perder horas de extracao se a sessao
remota cair antes da carga.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta

_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(_script_dir, "..", "dashboard", "backend")
sys.path.insert(0, _backend_dir)

from ciss_analytics.ciss_client import get_client  # noqa: E402
from ciss_analytics.loader import carregar_itens_venda, materializar_kpis_diarios, materializar_metricas_clientes, registrar_etl  # noqa: E402
from ciss_analytics.name_enrichment import enrich_customer_links_from_ciss, enrich_dw_names  # noqa: E402
from ciss_analytics.schema import get_dw_conn, init_dw  # noqa: E402
from ciss_analytics.transformer import transform_itens_venda  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("etl_vendas_streaming")


def _clausula(campo: str, valor: str) -> dict:
    return {"campo": campo, "valor": valor, "operador": "MAIOR_IGUAL", "operadorLogico": "AND"}


def _flush_itens(buffer: list[dict], counters: dict[str, int]) -> None:
    if not buffer:
        return
    itens = transform_itens_venda(buffer)
    loaded = carregar_itens_venda(itens)
    counters["itens_validos"] += len(itens)
    counters["itens_carregados"] += loaded
    buffer.clear()


def _materializar_vendas_desde(data_inicio: str) -> int:
    conn = get_dw_conn()
    try:
        conn.execute(
            """
            INSERT INTO dw_vendas
                (id_venda, data_venda, id_vendedor, total_bruto, total_desconto,
                 total_liquido, num_itens, id_empresa, status, id_cliente, atualizado_em)
            SELECT
                id_venda,
                MIN(data_venda) AS data_venda,
                MIN(COALESCE(id_vendedor, '')) AS id_vendedor,
                SUM(COALESCE(preco_unitario, 0) * COALESCE(quantidade, 0)) AS total_bruto,
                SUM(COALESCE(desconto_val, desconto_item, 0)) AS total_desconto,
                SUM(COALESCE(total_item, 0)) AS total_liquido,
                COUNT(*) AS num_itens,
                MIN(COALESCE(id_empresa, '1')) AS id_empresa,
                'aprovado' AS status,
                '' AS id_cliente,
                CURRENT_TIMESTAMP AS atualizado_em
            FROM dw_itens_venda
            WHERE data_venda >= ?
            GROUP BY id_venda
            ON CONFLICT(id_venda) DO UPDATE SET
                data_venda = excluded.data_venda,
                id_vendedor = excluded.id_vendedor,
                total_bruto = excluded.total_bruto,
                total_desconto = excluded.total_desconto,
                total_liquido = excluded.total_liquido,
                num_itens = excluded.num_itens,
                id_empresa = excluded.id_empresa,
                status = excluded.status,
                atualizado_em = excluded.atualizado_em
            """,
            (data_inicio,),
        )
        result = conn.execute("SELECT COUNT(*) AS total FROM dw_vendas WHERE data_venda >= ?", (data_inicio,))
        row = result.fetchone()
        conn.commit()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL streaming de vendas CISS")
    parser.add_argument("--dias", type=int, default=int(os.environ.get("CISS_SALES_INITIAL_DAYS", "90")))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("CISS_MAX_PAGES", "5000")))
    parser.add_argument("--batch-pages", type=int, default=25)
    args = parser.parse_args()

    inicio = datetime.utcnow().isoformat()
    data_inicio = (date.today() - timedelta(days=max(1, args.dias))).isoformat()
    counters = {"raw": 0, "paginas": 0, "itens_validos": 0, "itens_carregados": 0}
    buffer: list[dict] = []
    t0 = time.time()

    logger.info("=== ETL VENDAS STREAMING: ultimos %d dias desde %s ===", args.dias, data_inicio)
    init_dw()
    client = get_client()
    clausulas = [_clausula("dtalteracao", data_inicio)]

    try:
        for page in range(1, args.max_pages + 1):
            data = client.post_service("get_pedido_venda_prod", page=page, clausulas=clausulas)
            if not data:
                break
            registros = client._extrair_registros(data, "get_pedido_venda_prod")
            if registros:
                buffer.extend(registros)
                counters["raw"] += len(registros)
            counters["paginas"] = page

            if page % args.batch_pages == 0:
                _flush_itens(buffer, counters)
                logger.info(
                    "Pagina %d | raw=%d | itens_carregados=%d | %.0fs",
                    page,
                    counters["raw"],
                    counters["itens_carregados"],
                    time.time() - t0,
                )

            if not data.get("hasNext", False):
                break

        _flush_itens(buffer, counters)
        vendas = _materializar_vendas_desde(data_inicio)
        customer_links = enrich_customer_links_from_ciss(data_inicio=data_inicio)
        enrichment = enrich_dw_names(fetch_sellers=True)
        materializar_kpis_diarios()
        customer_metrics = materializar_metricas_clientes()
        registrar_etl(
            "etl_vendas_streaming",
            "ok",
            inicio,
            counters["raw"],
            vendas,
            ultima_data_ref=date.today().isoformat(),
        )
        logger.info(
            "=== ETL VENDAS STREAMING OK: paginas=%d raw=%d itens=%d vendas=%d customer_links=%s enrichment=%s customer_metrics=%s ===",
            counters["paginas"],
            counters["raw"],
            counters["itens_carregados"],
            vendas,
            customer_links,
            enrichment,
            customer_metrics,
        )
        return 0
    except Exception as exc:
        registrar_etl("etl_vendas_streaming", "error", inicio, counters["raw"], counters["itens_carregados"], erro_msg=str(exc))
        logger.exception("ETL vendas streaming falhou: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
