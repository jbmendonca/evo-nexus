"""
Blueprint Flask: rotas CISS Analytics para o dashboard EvoNexus.

Registrado em app.py como qualquer outro blueprint.
Prefixo: /api/ciss
"""
import logging
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import login_required

from ciss_analytics.schema import get_dw_conn, init_dw
from ciss_analytics.scheduler import start_ciss_scheduler
from ciss_analytics.config import validate_config

logger = logging.getLogger(__name__)
bp = Blueprint("ciss_analytics", __name__, url_prefix="/api/ciss")

# Inicializar DW e scheduler ao importar
try:
    init_dw()
    start_ciss_scheduler()
    logger.info("CISS Analytics: DW e scheduler inicializados")
except Exception as _exc:
    logger.warning("CISS Analytics: inicialização parcial — %s", _exc)


def _require_ciss_config(f):
    """Decorator: verifica se CISS está configurado antes de processar."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        erros = validate_config()
        if erros:
            return jsonify({"erro": "CISS não configurado", "detalhes": erros}), 503
        return f(*args, **kwargs)
    return wrapper


# ── Utilitário ────────────────────────────────────────────────────────────────
def _parse_periodo() -> int:
    """Lê ?periodo=30 da query string. Default: 30 dias."""
    try:
        p = int(request.args.get("periodo", 30))
        return max(1, min(p, 365))
    except (ValueError, TypeError):
        return 30


def _parse_limit(default: int = 10, *, minimum: int = 1, maximum: int = 50) -> int:
    """Lê ?limit=10 com bounds e falha explícita para entradas inválidas."""
    raw = request.args.get("limit", default)
    try:
        limit = int(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Parâmetro 'limit' inválido. Use inteiro entre 1 e 50.") from exc
    return max(minimum, min(limit, maximum))


def _parse_units() -> list[str]:
    """Lê filtros de unidade por `unidades` (csv) ou `unidade` repetido."""
    units: list[str] = []
    csv_values = request.args.get("unidades", "")
    if csv_values:
        units.extend([item.strip() for item in csv_values.split(",") if item.strip()])
    units.extend([item.strip() for item in request.args.getlist("unidade") if item.strip()])
    unique: list[str] = []
    for item in units:
        if item not in unique:
            unique.append(item)
    return unique


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _int_num(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _days_since(date_value) -> int | None:
    if not date_value:
        return None
    try:
        return (date.today() - date.fromisoformat(str(date_value)[:10])).days
    except (TypeError, ValueError):
        return None


def _cliente_rows_from_sales(conn, limit: int, cutoff: str | None = None) -> list[dict]:
    """Fallback para ambientes onde a tabela materializada ainda nao existe."""
    where_cutoff = "AND v.data_venda >= ?" if cutoff else ""
    params = (cutoff, limit) if cutoff else (limit,)
    rows = conn.execute(
        f"""
        SELECT
            v.id_cliente,
            COALESCE(NULLIF(MAX(v.nome_cliente), ''), NULLIF(MAX(c.nome), ''), v.id_cliente) AS nome_cliente,
            MAX(c.cidade) AS cidade,
            MAX(c.estado) AS estado,
            MAX(c.rfm_segmento) AS segmento_rfm,
            COUNT(DISTINCT v.id_venda) AS num_compras,
            SUM(COALESCE(v.num_itens, 0)) AS itens_comprados,
            SUM(COALESCE(v.total_liquido, 0)) AS valor_total,
            AVG(COALESCE(v.total_liquido, 0)) AS ticket_medio,
            MIN(v.data_venda) AS primeira_compra,
            MAX(v.data_venda) AS ultima_compra
        FROM dw_vendas v
        LEFT JOIN dw_clientes c ON c.id_cliente = v.id_cliente
        WHERE COALESCE(v.id_cliente, '') != ''
          AND (v.status IS NULL OR v.status != 'cancelada')
          {where_cutoff}
        GROUP BY v.id_cliente
        ORDER BY valor_total DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    payload: list[dict] = []
    total_geral = sum(_num(row["valor_total"]) for row in rows)
    acumulado = 0.0
    for index, row in enumerate(rows):
        valor_total = round(_num(row["valor_total"]), 2)
        acumulado += valor_total
        share = (acumulado / total_geral * 100) if total_geral else 0
        dias_desde_ultima = _days_since(row["ultima_compra"])
        if share <= 80 or index == 0:
            classe = "A"
        elif share <= 95:
            classe = "B"
        else:
            classe = "C"
        segmento = row["segmento_rfm"]
        if not segmento:
            if dias_desde_ultima is None:
                segmento = "Sem recencia"
            elif dias_desde_ultima <= 30:
                segmento = "Ativo recente"
            elif dias_desde_ultima <= 90:
                segmento = "Recorrente"
            else:
                segmento = "Reativar"
        payload.append(
            {
                "id_cliente": row["id_cliente"],
                "nome_cliente": row["nome_cliente"],
                "cidade": row["cidade"],
                "estado": row["estado"],
                "segmento_rfm": segmento,
                "classificacao_abc": classe,
                "num_compras": _int_num(row["num_compras"]),
                "itens_comprados": round(_num(row["itens_comprados"]), 2),
                "valor_total": valor_total,
                "ticket_medio": round(_num(row["ticket_medio"]), 2),
                "skus_distintos": 0,
                "primeira_compra": row["primeira_compra"],
                "ultima_compra": row["ultima_compra"],
                "dias_desde_ultima": dias_desde_ultima,
            }
        )
    return payload


def _abc_distribution(clientes: list[dict]) -> list[dict]:
    result: dict[str, dict] = {}
    for cliente in clientes:
        classe = cliente.get("classificacao_abc") or "C"
        bucket = result.setdefault(classe, {"classificacao_abc": classe, "total_clientes": 0, "total_valor": 0.0})
        bucket["total_clientes"] += 1
        bucket["total_valor"] += _num(cliente.get("valor_total"))
    return [
        {**item, "total_valor": round(item["total_valor"], 2)}
        for item in sorted(result.values(), key=lambda row: row["classificacao_abc"])
    ]


# ── KPIs Gerais ───────────────────────────────────────────────────────────────
@bp.route("/dashboard/kpis")
@login_required
@_require_ciss_config
def kpis():
    """KPIs principais: total de vendas, transações, ticket médio, crescimento."""
    periodo = _parse_periodo()
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo)).isoformat()
        cutoff_ant = (date.today() - timedelta(days=periodo * 2)).isoformat()
        hoje = date.today().isoformat()

        row = conn.execute(
            f"""
            SELECT
                SUM(total_liquido)          AS total_vendas,
                COUNT(DISTINCT id_venda)    AS num_transacoes,
                AVG(total_liquido)          AS ticket_medio,
                COUNT(DISTINCT id_cliente)  AS clientes_ativos,
                SUM(total_desconto)         AS total_descontos
            FROM dw_vendas
            WHERE data_venda >= '{cutoff}' AND status != 'cancelada'
            """
        ).fetchone()

        row_ant = conn.execute(
            f"""
            SELECT SUM(total_liquido) AS total_ant
            FROM dw_vendas
            WHERE data_venda >= '{cutoff_ant}' AND data_venda < '{cutoff}'
              AND status != 'cancelada'
            """
        ).fetchone()

        hoje_row = conn.execute(
            f"""
            SELECT SUM(total_liquido) AS vendas_hoje, COUNT(*) AS trans_hoje
            FROM dw_vendas WHERE data_venda = '{hoje}' AND status != 'cancelada'
            """
        ).fetchone()

        etl_row = conn.execute(
            """
            SELECT concluido_em, status, registros_ins
            FROM dw_etl_log ORDER BY id DESC LIMIT 1
            """
        ).fetchone()

        total = float(row["total_vendas"] or 0)
        total_ant = float(row_ant["total_ant"] or 0)
        crescimento = ((total - total_ant) / max(total_ant, 1)) * 100 if total_ant else 0

        return jsonify({
            "periodo_dias": periodo,
            "total_vendas": round(total, 2),
            "num_transacoes": int(row["num_transacoes"] or 0),
            "ticket_medio": round(float(row["ticket_medio"] or 0), 2),
            "clientes_ativos": int(row["clientes_ativos"] or 0),
            "total_descontos": round(float(row["total_descontos"] or 0), 2),
            "crescimento_pct": round(crescimento, 1),
            "vendas_hoje": round(float(hoje_row["vendas_hoje"] or 0), 2),
            "transacoes_hoje": int(hoje_row["trans_hoje"] or 0),
            "ultima_sincronizacao": dict(etl_row) if etl_row else None,
        })
    finally:
        conn.close()


# ── Vendas Diárias (série histórica) ──────────────────────────────────────────
@bp.route("/dashboard/vendas-diarias")
@login_required
@_require_ciss_config
def vendas_diarias():
    """Série histórica de vendas diárias para gráfico de linha."""
    periodo = _parse_periodo()
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo)).isoformat()
        rows = conn.execute(
            f"""
            SELECT data_ref, total_vendas, total_transacoes, ticket_medio
            FROM dw_kpis_diarios
            WHERE data_ref >= '{cutoff}'
            ORDER BY data_ref ASC
            """
        ).fetchall()
        return jsonify({"serie": [dict(r) for r in rows], "periodo_dias": periodo})
    finally:
        conn.close()


# ── Top Clientes ───────────────────────────────────────────────────────────────
@bp.route("/dashboard/top-clientes")
@login_required
@_require_ciss_config
def top_clientes():
    """Top 10 clientes por valor de compras no período."""
    periodo = _parse_periodo()
    try:
        limit = _parse_limit()
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo)).isoformat()
        try:
            rows = conn.execute(
                """
                SELECT
                    id_cliente,
                    nome_cliente AS nome,
                    cidade,
                    estado,
                    segmento_rfm AS rfm_segmento,
                    valor_total AS total_compras,
                    num_compras,
                    ticket_medio,
                    ultima_compra
                FROM dw_cliente_metricas
                WHERE id_cliente != ''
                ORDER BY total_compras DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            clientes = [
                {**dict(r), "total_compras": round(_num(r["total_compras"]), 2), "ticket_medio": round(_num(r["ticket_medio"]), 2)}
                for r in rows
            ]
        except Exception as exc:
            logger.warning("top_clientes: usando fallback em dw_vendas: %s", exc)
            conn.rollback()
            clientes = [
                {
                    "id_cliente": r["id_cliente"],
                    "nome": r["nome_cliente"],
                    "cidade": r["cidade"],
                    "estado": r["estado"],
                    "rfm_segmento": r["segmento_rfm"],
                    "total_compras": r["valor_total"],
                    "num_compras": r["num_compras"],
                    "ticket_medio": r["ticket_medio"],
                    "ultima_compra": r["ultima_compra"],
                }
                for r in _cliente_rows_from_sales(conn, limit, cutoff)
            ]
        return jsonify({
            "clientes": clientes,
            "periodo_dias": periodo,
        })
    finally:
        conn.close()


# ── Clientes 360 (materializado) ──────────────────────────────────────────────
@bp.route("/dashboard/clientes/ranking")
@login_required
@_require_ciss_config
def clientes_ranking():
    """Ranking materializado de clientes com classificação ABC e segmento RFM."""
    try:
        limit = _parse_limit(default=50, maximum=500)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400

    classe_abc = str(request.args.get("classe_abc", "")).strip().upper()
    segmento_rfm = str(request.args.get("segmento_rfm", "")).strip()
    where = []
    params: list = []
    if classe_abc in {"A", "B", "C"}:
        where.append("classificacao_abc = ?")
        params.append(classe_abc)
    if segmento_rfm:
        where.append("segmento_rfm = ?")
        params.append(segmento_rfm)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    conn = get_dw_conn()
    try:
        try:
            rows = conn.execute(
                f"""
                SELECT
                    id_cliente,
                    nome_cliente,
                    cidade,
                    estado,
                    segmento_rfm,
                    classificacao_abc,
                    num_compras,
                    itens_comprados,
                    valor_total,
                    ticket_medio,
                    skus_distintos,
                    primeira_compra,
                    ultima_compra,
                    dias_desde_ultima
                FROM dw_cliente_metricas
                {where_sql}
                ORDER BY valor_total DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            clientes = [
                {
                    **dict(r),
                    "num_compras": _int_num(r["num_compras"]),
                    "itens_comprados": round(_num(r["itens_comprados"]), 2),
                    "valor_total": round(_num(r["valor_total"]), 2),
                    "ticket_medio": round(_num(r["ticket_medio"]), 2),
                    "skus_distintos": _int_num(r["skus_distintos"]),
                    "dias_desde_ultima": _int_num(r["dias_desde_ultima"]) if r["dias_desde_ultima"] is not None else None,
                }
                for r in rows
            ]
            dist = [
                {**dict(r), "total_clientes": _int_num(r["total_clientes"]), "total_valor": round(_num(r["total_valor"]), 2)}
                for r in conn.execute(
                    """
                    SELECT classificacao_abc, COUNT(*) AS total_clientes, SUM(valor_total) AS total_valor
                    FROM dw_cliente_metricas
                    GROUP BY classificacao_abc
                    ORDER BY classificacao_abc
                    """
                ).fetchall()
            ]
        except Exception as exc:
            logger.warning("clientes_ranking: usando fallback em dw_vendas: %s", exc)
            conn.rollback()
            clientes = _cliente_rows_from_sales(conn, limit)
            if classe_abc in {"A", "B", "C"}:
                clientes = [r for r in clientes if r["classificacao_abc"] == classe_abc]
            if segmento_rfm:
                clientes = [r for r in clientes if r["segmento_rfm"] == segmento_rfm]
            dist = _abc_distribution(clientes)
        return jsonify(
            {
                "clientes": clientes,
                "distribuicao_abc": dist,
                "filtros": {"classe_abc": classe_abc or None, "segmento_rfm": segmento_rfm or None},
            }
        )
    finally:
        conn.close()


@bp.route("/dashboard/clientes/<id_cliente>/top-produtos")
@login_required
@_require_ciss_config
def cliente_top_produtos(id_cliente: str):
    """Produtos mais comprados por um cliente (valor e quantidade)."""
    try:
        limit = _parse_limit(default=20, maximum=200)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    id_cliente = str(id_cliente or "").strip()
    if not id_cliente:
        return jsonify({"erro": "id_cliente obrigatorio"}), 400

    conn = get_dw_conn()
    try:
        cliente = conn.execute(
            """
            SELECT *
            FROM dw_cliente_metricas
            WHERE id_cliente = ?
            LIMIT 1
            """,
            (id_cliente,),
        ).fetchone()
        if not cliente:
            return jsonify({"erro": "Cliente nao encontrado nas metricas materializadas"}), 404

        top_valor = conn.execute(
            """
            SELECT
                id_produto,
                nome_produto,
                categoria,
                quantidade_total,
                valor_total,
                num_compras,
                preco_medio,
                ultima_compra,
                rank_valor,
                participacao_valor_cliente
            FROM dw_cliente_produto_metricas
            WHERE id_cliente = ?
            ORDER BY rank_valor ASC, valor_total DESC
            LIMIT ?
            """,
            (id_cliente, limit),
        ).fetchall()

        top_qtd = conn.execute(
            """
            SELECT
                id_produto,
                nome_produto,
                categoria,
                quantidade_total,
                valor_total,
                num_compras,
                preco_medio,
                ultima_compra,
                rank_quantidade,
                participacao_qtd_cliente
            FROM dw_cliente_produto_metricas
            WHERE id_cliente = ?
            ORDER BY rank_quantidade ASC, quantidade_total DESC
            LIMIT ?
            """,
            (id_cliente, limit),
        ).fetchall()

        return jsonify(
            {
                "cliente": dict(cliente),
                "top_produtos_por_valor": [dict(r) for r in top_valor],
                "top_produtos_por_quantidade": [dict(r) for r in top_qtd],
            }
        )
    finally:
        conn.close()


# ── Top Produtos ───────────────────────────────────────────────────────────────
@bp.route("/dashboard/top-produtos")
@login_required
@_require_ciss_config
def top_produtos():
    """Top 10 produtos mais vendidos por valor no período."""
    periodo = _parse_periodo()
    try:
        limit = _parse_limit()
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=periodo)).isoformat()
        rows = conn.execute(
            """
            SELECT
                i.id_produto,
                i.codigo_produto,
                i.nome_produto,
                i.nome_categoria,
                SUM(i.quantidade)          AS total_qtd,
                SUM(i.total_item)          AS total_valor,
                COUNT(DISTINCT i.id_venda) AS num_vendas,
                AVG(i.preco_unitario)      AS preco_medio
            FROM dw_itens_venda i
            JOIN dw_vendas v ON v.id_venda = i.id_venda
            WHERE v.data_venda >= ? AND v.status != 'cancelada'
            GROUP BY i.id_produto, i.nome_produto
            ORDER BY total_valor DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return jsonify({
            "produtos": [
                {**dict(r),
                 "total_qtd": round(float(r["total_qtd"] or 0), 2),
                 "total_valor": round(float(r["total_valor"] or 0), 2),
                 "preco_medio": round(float(r["preco_medio"] or 0), 2)}
                for r in rows
            ],
            "periodo_dias": periodo,
        })
    finally:
        conn.close()


# ── Estoque ────────────────────────────────────────────────────────────────────
@bp.route("/estoque/alertas")
@login_required
@_require_ciss_config
def alertas_estoque():
    """Alertas de estoque baixo / crítico com previsão de ruptura."""
    from ciss_analytics.ml.stock_alert import carregar_alertas_cache, gerar_alertas_estoque
    data = carregar_alertas_cache() or gerar_alertas_estoque()
    return jsonify(data)


@bp.route("/estoque/todos")
@login_required
@_require_ciss_config
def estoque_todos():
    """Lista completa de estoque com nível de alerta."""
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT e.*, p.nome_categoria, p.preco_venda
            FROM dw_estoque e
            LEFT JOIN dw_produtos p ON p.id_produto = e.id_produto
            ORDER BY
                CASE nivel_alerta
                    WHEN 'critico' THEN 1
                    WHEN 'alerta' THEN 2
                    WHEN 'atencao' THEN 3
                    ELSE 4
                END, e.nome_produto ASC
            """
        ).fetchall()
        return jsonify({"estoque": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


# ── ML Insights ────────────────────────────────────────────────────────────────
@bp.route("/insights")
@login_required
@_require_ciss_config
def insights():
    """Insights consolidados com frases em linguagem natural."""
    from ciss_analytics.ml.insight_engine import carregar_insights_cache, gerar_insights
    forcar = request.args.get("forcar", "false").lower() == "true"
    data = (None if forcar else carregar_insights_cache()) or gerar_insights()
    return jsonify(data)


@bp.route("/insights/previsao")
@login_required
@_require_ciss_config
def previsao_vendas():
    """Previsão de vendas para os próximos 30 dias."""
    from ciss_analytics.ml.sales_forecaster import carregar_previsao_cache, gerar_previsao
    forcar = request.args.get("forcar", "false").lower() == "true"
    data = (None if forcar else carregar_previsao_cache()) or gerar_previsao()
    return jsonify(data)


@bp.route("/clientes/segmentos")
@login_required
@_require_ciss_config
def segmentos_clientes():
    """Segmentação RFM de clientes."""
    from ciss_analytics.ml.customer_segmentation import carregar_segmentos_cache, segmentar_clientes
    forcar = request.args.get("forcar", "false").lower() == "true"
    data = (None if forcar else carregar_segmentos_cache()) or segmentar_clientes()
    return jsonify(data)


@bp.route("/anomalias")
@login_required
@_require_ciss_config
def anomalias():
    """Anomalias detectadas nas vendas."""
    from ciss_analytics.ml.anomaly_detector import carregar_anomalias_cache, detectar_anomalias
    forcar = request.args.get("forcar", "false").lower() == "true"
    data = (None if forcar else carregar_anomalias_cache()) or detectar_anomalias()
    return jsonify(data)


# ── ETL Manual ─────────────────────────────────────────────────────────────────
# --- Analytics gerencial + agente de dados ---------------------------------
@bp.route("/decision/overview")
@login_required
@_require_ciss_config
def decision_overview():
    """Snapshot gerencial completo para proprietarios e gerentes."""
    from ciss_analytics.manager_analytics import build_manager_snapshot

    periodo = _parse_periodo()
    units = _parse_units()
    try:
        limit = _parse_limit(default=15, maximum=100)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    return jsonify(build_manager_snapshot(period_days=periodo, limit=limit, unit_keys=units))


@bp.route("/decision/ask", methods=["POST"])
@login_required
@_require_ciss_config
def decision_ask():
    """Responde perguntas sobre o DW CISS com dados consolidados."""
    from ciss_analytics.data_agent import answer_question

    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or body.get("pergunta") or "").strip()
    if not question:
        return jsonify({"ok": False, "erro": "Campo question/pergunta e obrigatorio."}), 400
    period_days = body.get("periodo_dias") or body.get("periodo") or _parse_periodo()
    try:
        period_days = max(1, min(int(period_days), 3650))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "periodo_dias invalido."}), 400
    return jsonify(answer_question(question, period_days=period_days))


@bp.route("/decision/agent/history")
@login_required
def decision_agent_history():
    """Historico recente de perguntas feitas ao agente de dados."""
    from ciss_analytics.data_agent import recent_questions

    try:
        limit = _parse_limit(default=20, maximum=100)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    return jsonify({"ok": True, "questions": recent_questions(limit=limit)})


@bp.route("/etl/trigger", methods=["POST"])
@login_required
@_require_ciss_config
def etl_trigger():
    """Trigger manual de ETL. Body JSON: {"tipo": "vendas"|"estoque"|"full"}"""
    import threading
    from ciss_analytics.scheduler import _run_etl_vendas, _run_etl_estoque, _run_etl_full

    body = request.get_json(silent=True) or {}
    tipo = body.get("tipo", "vendas")

    fn_map = {
        "vendas": _run_etl_vendas,
        "estoque": _run_etl_estoque,
        "full": _run_etl_full,
    }

    fn = fn_map.get(tipo)
    if not fn:
        return jsonify({"erro": f"Tipo inválido: {tipo}. Use: vendas, estoque, full"}), 400

    t = threading.Thread(target=fn, daemon=True, name=f"ciss-etl-manual-{tipo}")
    t.start()
    return jsonify({"ok": True, "mensagem": f"ETL '{tipo}' iniciado em background"})


@bp.route("/etl/status")
@login_required
def etl_status():
    """Status das últimas execuções do ETL."""
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT tipo, status, iniciado_em, concluido_em,
                   registros_ext, registros_ins, erro_msg
            FROM dw_etl_log ORDER BY id DESC LIMIT 10
            """
        ).fetchall()
        erros_config = validate_config()
        return jsonify({
            "logs": [dict(r) for r in rows],
            "configurado": len(erros_config) == 0,
            "erros_config": erros_config,
        })
    finally:
        conn.close()
