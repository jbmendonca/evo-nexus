"""
Sistema de Alertas de Estoque com previsão de ruptura.

Calcula consumo médio diário por produto e projeta quando o estoque
vai zerar, gerando alertas por nível de urgência.
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..schema import get_dw_conn
from ..config import (
    CISS_MODELS_PATH,
    STOCK_ALERT_DAYS_CRITICAL,
    STOCK_ALERT_DAYS_WARNING,
    STOCK_ALERT_DAYS_ATTENTION,
)

logger = logging.getLogger(__name__)


def _calcular_consumo_medio() -> None:
    """
    Calcula consumo médio diário por produto (últimos 30 dias)
    e atualiza dw_estoque com consumo_medio_dia e dias_cobertura.
    """
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=30)).isoformat()

        # Consumo por produto nos últimos 30 dias
        consumos = conn.execute(
            f"""
            SELECT i.id_produto, SUM(i.quantidade) / 30.0 AS consumo_dia
            FROM dw_itens_venda i
            JOIN dw_vendas v ON v.id_venda = i.id_venda
            WHERE v.data_venda >= '{cutoff}'
              AND v.status != 'cancelada'
            GROUP BY i.id_produto
            """
        ).fetchall()

        consumo_map = {r["id_produto"]: float(r["consumo_dia"]) for r in consumos}

        # Atualizar estoque com dias de cobertura e alerta
        estoques = conn.execute("SELECT * FROM dw_estoque").fetchall()

        hoje = date.today()
        for row in estoques:
            consumo = consumo_map.get(row["id_produto"], 0.0)
            qtd = float(row["quantidade"] or 0)

            if consumo > 0:
                dias_cob = qtd / consumo
                data_rupt = (hoje + timedelta(days=int(dias_cob))).isoformat()
            else:
                dias_cob = 9999.0   # sem consumo = sem previsão de ruptura
                data_rupt = None

            # Determinar nível de alerta
            if qtd <= float(row["estoque_minimo"] or 0):
                nivel = "critico"
            elif dias_cob <= STOCK_ALERT_DAYS_CRITICAL:
                nivel = "critico"
            elif dias_cob <= STOCK_ALERT_DAYS_WARNING:
                nivel = "alerta"
            elif dias_cob <= STOCK_ALERT_DAYS_ATTENTION:
                nivel = "atencao"
            else:
                nivel = "ok"

            conn.execute(
                """
                UPDATE dw_estoque SET
                    consumo_medio_dia = ?,
                    dias_cobertura = ?,
                    nivel_alerta = ?,
                    data_ruptura_prev = ?,
                    ml_atualizado = datetime('now')
                WHERE id_estoque = ?
                """,
                (
                    round(consumo, 3) if consumo > 0 else None,
                    round(dias_cob, 1) if dias_cob < 9999 else None,
                    nivel,
                    data_rupt,
                    row["id_estoque"],
                ),
            )

        conn.commit()
        logger.info("Consumo médio e alertas de estoque atualizados para %d produtos", len(estoques))
    finally:
        conn.close()


def gerar_alertas_estoque() -> dict[str, Any]:
    """
    Gera relatório de alertas de estoque ordenado por urgência.

    Returns:
        {
          "critico": [lista de produtos críticos],
          "alerta": [lista em alerta],
          "atencao": [lista em atenção],
          "resumo": {"critico": int, "alerta": int, "atencao": int, "ok": int},
          "gerado_em": str
        }
    """
    _calcular_consumo_medio()

    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                e.id_produto,
                e.codigo_produto,
                e.nome_produto,
                e.nome_deposito,
                e.quantidade,
                e.estoque_minimo,
                e.consumo_medio_dia,
                e.dias_cobertura,
                e.nivel_alerta,
                e.data_ruptura_prev,
                p.nome_categoria,
                p.preco_venda
            FROM dw_estoque e
            LEFT JOIN dw_produtos p ON p.id_produto = e.id_produto
            WHERE e.nivel_alerta != 'ok'
            ORDER BY
                CASE e.nivel_alerta
                    WHEN 'critico' THEN 1
                    WHEN 'alerta'  THEN 2
                    WHEN 'atencao' THEN 3
                    ELSE 4
                END,
                e.dias_cobertura ASC NULLS LAST
            """
        ).fetchall()

        # Contar totais por nível
        resumo_row = conn.execute(
            """
            SELECT nivel_alerta, COUNT(*) AS cnt
            FROM dw_estoque
            GROUP BY nivel_alerta
            """
        ).fetchall()
    finally:
        conn.close()

    def fmt_produto(r) -> dict:
        dias = r["dias_cobertura"]
        return {
            "id_produto": r["id_produto"],
            "codigo": r["codigo_produto"],
            "nome": r["nome_produto"],
            "deposito": r["nome_deposito"],
            "quantidade_atual": round(float(r["quantidade"] or 0), 2),
            "estoque_minimo": round(float(r["estoque_minimo"] or 0), 2),
            "consumo_medio_dia": round(float(r["consumo_medio_dia"] or 0), 2),
            "dias_cobertura": round(float(dias), 1) if dias else None,
            "data_ruptura_prevista": r["data_ruptura_prev"],
            "nivel_alerta": r["nivel_alerta"],
            "categoria": r["nome_categoria"],
            "preco_venda": round(float(r["preco_venda"] or 0), 2),
            "quantidade_sugerida_reposicao": max(
                0,
                round(
                    float(r["consumo_medio_dia"] or 0) * 30
                    - float(r["quantidade"] or 0),
                    0,
                ),
            ),
        }

    produtos_alertas = [dict(r) for r in rows]
    criticos  = [fmt_produto(r) for r in rows if r["nivel_alerta"] == "critico"]
    alertas   = [fmt_produto(r) for r in rows if r["nivel_alerta"] == "alerta"]
    atencao   = [fmt_produto(r) for r in rows if r["nivel_alerta"] == "atencao"]

    resumo = {"critico": 0, "alerta": 0, "atencao": 0, "ok": 0}
    for r in resumo_row:
        resumo[r["nivel_alerta"]] = r["cnt"]

    resultado = {
        "critico": criticos,
        "alerta": alertas,
        "atencao": atencao,
        "resumo": resumo,
        "total_produtos_monitorados": sum(resumo.values()),
        "gerado_em": date.today().isoformat(),
    }

    cache_path = Path(CISS_MODELS_PATH) / "stock_alerts.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Alertas de estoque: %d críticos, %d alertas, %d atenção",
        len(criticos), len(alertas), len(atencao)
    )
    return resultado


def carregar_alertas_cache() -> dict[str, Any] | None:
    cache_path = Path(CISS_MODELS_PATH) / "stock_alerts.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("gerado_em") == date.today().isoformat():
            return data
    except Exception:
        pass
    return None
