"""
Engine de Insights em linguagem natural (pt-BR).

Agrega resultados de todos os modelos ML e gera frases de insight
prontas para exibição no dashboard para gerentes e proprietários.
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..schema import get_dw_conn
from ..config import CISS_MODELS_PATH
from . import sales_forecaster, customer_segmentation, anomaly_detector, stock_alert

logger = logging.getLogger(__name__)


def _top_produtos(periodo_dias: int = 30) -> list[dict]:
    """Retorna top 10 produtos mais vendidos no período."""
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo_dias)).isoformat()
        rows = conn.execute(
            f"""
            SELECT
                i.id_produto,
                i.nome_produto,
                i.nome_categoria,
                SUM(i.quantidade)  AS total_qtd,
                SUM(i.total_item)  AS total_valor,
                COUNT(DISTINCT i.id_venda) AS num_vendas
            FROM dw_itens_venda i
            JOIN dw_vendas v ON v.id_venda = i.id_venda
            WHERE v.data_venda >= '{cutoff}'
              AND v.status != 'cancelada'
            GROUP BY i.id_produto, i.nome_produto
            ORDER BY total_valor DESC
            LIMIT 10
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _kpis_periodo(periodo_dias: int = 30) -> dict:
    """Calcula KPIs resumidos do período."""
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo_dias)).isoformat()
        hoje = date.today().isoformat()
        ontem = (date.today() - timedelta(days=1)).isoformat()
        mes_ant_inicio = (date.today() - timedelta(days=periodo_dias * 2)).isoformat()

        row = conn.execute(
            f"""
            SELECT
                SUM(total_liquido)             AS total_vendas,
                COUNT(DISTINCT id_venda)       AS num_transacoes,
                AVG(total_liquido)             AS ticket_medio,
                COUNT(DISTINCT id_cliente)     AS clientes_ativos
            FROM dw_vendas
            WHERE data_venda >= '{cutoff}'
              AND status != 'cancelada'
            """
        ).fetchone()

        # Período anterior para comparação
        row_ant = conn.execute(
            f"""
            SELECT SUM(total_liquido) AS total_vendas
            FROM dw_vendas
            WHERE data_venda >= '{mes_ant_inicio}'
              AND data_venda < '{cutoff}'
              AND status != 'cancelada'
            """
        ).fetchone()

        # Vendas de hoje
        hoje_row = conn.execute(
            f"""
            SELECT SUM(total_liquido) AS vendas_hoje
            FROM dw_vendas
            WHERE data_venda = '{hoje}'
              AND status != 'cancelada'
            """
        ).fetchone()

        total = float(row["total_vendas"] or 0)
        total_ant = float(row_ant["total_vendas"] or 0)
        crescimento = ((total - total_ant) / max(total_ant, 1)) * 100 if total_ant else 0

        return {
            "total_vendas": round(total, 2),
            "num_transacoes": int(row["num_transacoes"] or 0),
            "ticket_medio": round(float(row["ticket_medio"] or 0), 2),
            "clientes_ativos": int(row["clientes_ativos"] or 0),
            "crescimento_pct": round(crescimento, 1),
            "vendas_hoje": round(float(hoje_row["vendas_hoje"] or 0), 2),
            "periodo_dias": periodo_dias,
        }
    finally:
        conn.close()


def gerar_insights() -> dict[str, Any]:
    """
    Gera todos os insights integrados em linguagem natural.

    Returns:
        {
          "kpis": dict com KPIs do período,
          "top_produtos": lista top 10 produtos,
          "top_clientes": lista top 10 clientes,
          "previsao": dados de previsão de vendas,
          "alertas_estoque": resumo de alertas,
          "anomalias": lista de anomalias recentes,
          "frases_insight": lista de strings em pt-BR para exibir no dashboard,
          "gerado_em": str
        }
    """
    logger.info("Gerando insights consolidados...")

    # Coleta dados (com cache quando possível)
    kpis = _kpis_periodo(30)
    top_prod = _top_produtos(30)

    forecast = (
        sales_forecaster.carregar_previsao_cache()
        or sales_forecaster.gerar_previsao()
    )
    segmentos = (
        customer_segmentation.carregar_segmentos_cache()
        or customer_segmentation.segmentar_clientes()
    )
    anomalias_data = (
        anomaly_detector.carregar_anomalias_cache()
        or anomaly_detector.detectar_anomalias()
    )
    alertas = (
        stock_alert.carregar_alertas_cache()
        or stock_alert.gerar_alertas_estoque()
    )

    # ── Gerar frases de insight ───────────────────────────────────────────────
    frases: list[str] = []

    # KPI principal
    if kpis["total_vendas"] > 0:
        cr = kpis["crescimento_pct"]
        emoji = "📈" if cr > 0 else ("📉" if cr < 0 else "➡️")
        frases.append(
            f"{emoji} Vendas de R$ {kpis['total_vendas']:,.2f} nos últimos 30 dias "
            f"({'alta' if cr > 0 else 'queda'} de {abs(cr):.1f}% vs período anterior)"
        )

    # Ticket médio
    if kpis["ticket_medio"] > 0:
        frases.append(f"🛒 Ticket médio de R$ {kpis['ticket_medio']:,.2f} por venda")

    # Top produto
    if top_prod:
        p = top_prod[0]
        frases.append(
            f"🏆 Produto mais vendido: {p['nome_produto']} "
            f"— R$ {float(p['total_valor']):,.2f} em {int(p['num_vendas'])} vendas"
        )

    # Previsão
    if forecast.get("tendencia"):
        t = forecast["tendencia"]
        cr = forecast.get("crescimento_pct", 0)
        emoji = "📈" if t == "alta" else ("📉" if t == "queda" else "➡️")
        frases.append(
            f"{emoji} Tendência de vendas: {t.upper()} — "
            f"crescimento de {cr:.1f}% nos últimos 14 dias"
        )

    # Próximos 7 dias (previsão)
    if forecast.get("previsao"):
        proximos = forecast["previsao"][:7]
        total_prev = sum(p["vendas_previstas"] for p in proximos)
        frases.append(
            f"🔮 Previsão dos próximos 7 dias: R$ {total_prev:,.2f} em vendas"
        )

    # Alertas críticos de estoque
    criticos = alertas.get("resumo", {}).get("critico", 0)
    if criticos > 0:
        prods = alertas.get("critico", [])
        exemplos = ", ".join(p["nome"] for p in prods[:3] if p.get("nome"))
        frases.append(
            f"🚨 URGENTE: {criticos} produto(s) com estoque crítico! "
            f"({exemplos}{'...' if len(prods) > 3 else ''}) — Repor imediatamente"
        )
    elif alertas.get("resumo", {}).get("alerta", 0) > 0:
        n = alertas["resumo"]["alerta"]
        frases.append(f"⚠️ {n} produto(s) com estoque baixo — Planejar reposição em breve")

    # Segmentação
    campiao = segmentos.get("segmentos", {}).get("campiao", {})
    if campiao:
        frases.append(
            f"👑 {campiao['count']} clientes Campeões — "
            f"responsáveis por R$ {campiao['total_vendas']:,.2f} em compras"
        )

    em_risco = segmentos.get("segmentos", {}).get("em_risco", {})
    if em_risco and em_risco.get("count", 0) > 0:
        frases.append(
            f"⚠️ {em_risco['count']} clientes Em Risco de churn "
            f"— considere uma ação de reativação"
        )

    # Anomalias
    anoms = anomalias_data.get("anomalias", [])
    recentes = [a for a in anoms if a.get("data", "") >= (date.today() - timedelta(days=7)).isoformat()]
    if recentes:
        a = recentes[0]
        frases.append(
            f"🔍 Anomalia detectada em {a['data']}: {a['descricao']} "
            f"(score: {a['z_score']:.1f}σ)"
        )

    resultado = {
        "kpis": kpis,
        "top_produtos": top_prod,
        "top_clientes": segmentos.get("top_clientes", [])[:10],
        "previsao": forecast,
        "segmentos_clientes": segmentos.get("segmentos", {}),
        "alertas_estoque": alertas,
        "anomalias": anomalias_data.get("anomalias", [])[:5],
        "frases_insight": frases,
        "gerado_em": date.today().isoformat(),
    }

    cache_path = Path(CISS_MODELS_PATH) / "insights.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    logger.info("Insights gerados: %d frases", len(frases))
    return resultado


def carregar_insights_cache() -> dict[str, Any] | None:
    cache_path = Path(CISS_MODELS_PATH) / "insights.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("gerado_em") == date.today().isoformat():
            return data
    except Exception:
        pass
    return None
