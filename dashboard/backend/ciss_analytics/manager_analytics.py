"""Manager-facing analytics for Pau Brasil CISS data.

This module reads the CISS data warehouse and returns decision-ready metrics
without exposing arbitrary SQL to the UI or to the data agent.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import math
import statistics
from typing import Any

from .schema import get_dw_conn


def _today() -> date:
    return date.today()


def _iso(d: date) -> str:
    return d.isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _round(value: Any, ndigits: int = 2) -> float:
    return round(_float(value), ndigits)


def _safe_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _status_filter_sql(alias: str = "v") -> str:
    return f"({alias}.status IS NULL OR lower({alias}.status) NOT IN ('cancelada','cancelado','canceled'))"


_UNIT_OPTIONS = [
    {"key": "matriz", "label": "Matriz", "patterns": ("matriz",)},
    {"key": "buritis", "label": "Buritis", "patterns": ("buritis",)},
    {"key": "trinta_um_de_marco", "label": "31 de Março", "patterns": ("31 de marco", "31 de março")},
    {"key": "pb_tintas", "label": "P.B Tintas", "patterns": ("p.b tintas", "pb tintas")},
    {"key": "callcenter", "label": "Callcenter", "patterns": ("callcenter",)},
]
_UNIT_BY_KEY = {item["key"]: item for item in _UNIT_OPTIONS}


def _normalize_unit_key(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value == "31_de_marco":
        return "trinta_um_de_marco"
    if value == "31_de_março":
        return "trinta_um_de_marco"
    return value


def _normalize_unit_keys(raw_units: list[str] | None) -> list[str]:
    if not raw_units:
        return []
    unique: list[str] = []
    for raw in raw_units:
        key = _normalize_unit_key(raw)
        if key in _UNIT_BY_KEY and key not in unique:
            unique.append(key)
    return unique


def _unit_filter_sql(alias: str, unit_keys: list[str]) -> tuple[str, tuple[Any, ...]]:
    if not unit_keys:
        return "", ()
    clauses: list[str] = []
    params: list[Any] = []
    for key in unit_keys:
        unit = _UNIT_BY_KEY.get(key)
        if not unit:
            continue
        patterns = unit["patterns"]
        likes = " OR ".join([f"lower(COALESCE({alias}.nome_vendedor, '')) LIKE ?" for _ in patterns])
        clauses.append(f"({likes})")
        params.extend([f"%{pattern}%" for pattern in patterns])
    if not clauses:
        return "", ()
    return " OR ".join(clauses), tuple(params)


def _sales_filter_clause(alias: str, start: str, end: str, unit_keys: list[str]) -> tuple[str, tuple[Any, ...]]:
    where = f"{alias}.data_venda >= ? AND {alias}.data_venda <= ? AND {_status_filter_sql(alias)}"
    params: list[Any] = [start, end]
    unit_sql, unit_params = _unit_filter_sql(alias, unit_keys)
    if unit_sql:
        where += f" AND ({unit_sql})"
        params.extend(unit_params)
    return where, tuple(params)


def _fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = get_dw_conn()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    conn = get_dw_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _table_count(table_name: str) -> int:
    try:
        row = _fetchone(f"SELECT COUNT(*) AS total FROM {table_name}")
        return _int(row.get("total"))
    except Exception:
        return 0


def _period_bounds(period_days: int) -> tuple[str, str, str]:
    days = max(1, min(int(period_days or 90), 3650))
    end = _today()
    start = end - timedelta(days=days - 1)
    previous_start = start - timedelta(days=days)
    return _iso(start), _iso(end), _iso(previous_start)


def _sales_rows(start: str, end: str, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    return _fetchall(
        f"""
        SELECT
            v.id_venda,
            v.data_venda,
            v.id_vendedor,
            COALESCE(NULLIF(v.nome_vendedor, ''), v.id_vendedor, 'Sem vendedor') AS nome_vendedor,
            v.total_bruto,
            v.total_desconto,
            v.total_liquido,
            v.num_itens,
            v.status
        FROM dw_vendas v
        WHERE {where}
        """,
        params,
    )


def _daily_sales(start: str, end: str, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    rows = _fetchall(
        f"""
        SELECT
            v.data_venda AS data,
            SUM(v.total_liquido) AS total_vendas,
            COUNT(DISTINCT v.id_venda) AS transacoes,
            AVG(v.total_liquido) AS ticket_medio,
            SUM(COALESCE(i.qtd_itens, 0)) AS itens_vendidos
        FROM dw_vendas v
        LEFT JOIN (
            SELECT id_venda, SUM(quantidade) AS qtd_itens
            FROM dw_itens_venda
            GROUP BY id_venda
        ) i ON i.id_venda = v.id_venda
        WHERE {where}
        GROUP BY v.data_venda
        ORDER BY v.data_venda ASC
        """,
        params,
    )
    return [
        {
            "data": row["data"],
            "total_vendas": _round(row.get("total_vendas")),
            "transacoes": _int(row.get("transacoes")),
            "ticket_medio": _round(row.get("ticket_medio")),
            "itens_vendidos": _round(row.get("itens_vendidos")),
        }
        for row in rows
    ]


def _aggregate_periods(daily: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    weekly: dict[str, dict[str, Any]] = {}
    monthly: dict[str, dict[str, Any]] = {}
    yearly: dict[str, dict[str, Any]] = {}

    def add(bucket: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
        target = bucket.setdefault(key, {"periodo": key, "total_vendas": 0.0, "transacoes": 0, "itens_vendidos": 0.0})
        target["total_vendas"] += _float(row.get("total_vendas"))
        target["transacoes"] += _int(row.get("transacoes"))
        target["itens_vendidos"] += _float(row.get("itens_vendidos"))

    for row in daily:
        d = _safe_date(row.get("data"))
        if not d:
            continue
        iso_year, iso_week, _ = d.isocalendar()
        add(weekly, f"{iso_year}-W{iso_week:02d}", row)
        add(monthly, d.strftime("%Y-%m"), row)
        add(yearly, d.strftime("%Y"), row)

    def finish(bucket: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for key in sorted(bucket):
            item = bucket[key]
            trans = max(_int(item.get("transacoes")), 1)
            out.append(
                {
                    "periodo": item["periodo"],
                    "total_vendas": round(item["total_vendas"], 2),
                    "transacoes": _int(item["transacoes"]),
                    "itens_vendidos": round(item["itens_vendidos"], 2),
                    "ticket_medio": round(item["total_vendas"] / trans, 2),
                }
            )
        return out

    return {"diario": daily, "semanal": finish(weekly), "mensal": finish(monthly), "anual": finish(yearly)}


def _summary(period_days: int, start: str, end: str, previous_start: str, sales: list[dict[str, Any]], unit_keys: list[str]) -> dict[str, Any]:
    total = sum(_float(row.get("total_liquido")) for row in sales)
    tickets = [_float(row.get("total_liquido")) for row in sales if _float(row.get("total_liquido")) > 0]
    discounts = sum(_float(row.get("total_desconto")) for row in sales)
    unit_previous_sql, unit_previous_params = _unit_filter_sql("v", unit_keys)
    unit_current_sql, unit_current_params = _unit_filter_sql("v", unit_keys)
    where_previous = f"v.data_venda >= ? AND v.data_venda < ? AND {_status_filter_sql('v')}"
    if unit_previous_sql:
        where_previous += f" AND ({unit_previous_sql})"
    previous_params: list[Any] = [previous_start, start, *unit_previous_params]
    previous = _fetchone(
        f"""
        SELECT SUM(total_liquido) AS total_vendas
        FROM dw_vendas v
        WHERE {where_previous}
        """,
        tuple(previous_params),
    )
    previous_total = _float(previous.get("total_vendas"))
    growth = ((total - previous_total) / previous_total * 100.0) if previous_total > 0 else 0.0
    where_current = f"v.data_venda >= ? AND v.data_venda <= ? AND {_status_filter_sql('v')}"
    if unit_current_sql:
        where_current += f" AND ({unit_current_sql})"
    current_params: list[Any] = [start, end, *unit_current_params]
    item_row = _fetchone(
        f"""
        SELECT
            SUM(i.quantidade) AS itens_vendidos,
            COUNT(DISTINCT i.id_produto) AS skus_vendidos,
            AVG(i.per_margem) AS margem_media,
            SUM(i.val_lucro) AS lucro_bruto_estimado
        FROM dw_itens_venda i
        JOIN dw_vendas v ON v.id_venda = i.id_venda
        WHERE {where_current}
        """,
        tuple(current_params),
    )
    customers = _fetchone(
        f"""
        SELECT COUNT(DISTINCT id_cliente) AS clientes_ativos
        FROM dw_vendas v
        WHERE {where_current}
          AND COALESCE(id_cliente, '') != ''
        """,
        tuple(current_params),
    )
    sellers = _fetchone(
        f"""
        SELECT COUNT(DISTINCT id_vendedor) AS vendedores_ativos
        FROM dw_vendas v
        WHERE {where_current}
          AND COALESCE(id_vendedor, '') != ''
        """,
        tuple(current_params),
    )

    return {
        "periodo_dias": period_days,
        "data_inicio": start,
        "data_fim": end,
        "total_vendas": round(total, 2),
        "total_vendas_anterior": round(previous_total, 2),
        "crescimento_pct": round(growth, 1),
        "transacoes": len(sales),
        "ticket_medio": round(total / len(sales), 2) if sales else 0.0,
        "ticket_mediano": round(statistics.median(tickets), 2) if tickets else 0.0,
        "total_descontos": round(discounts, 2),
        "itens_vendidos": _round(item_row.get("itens_vendidos")),
        "skus_vendidos": _int(item_row.get("skus_vendidos")),
        "clientes_ativos": _int(customers.get("clientes_ativos")),
        "vendedores_ativos": _int(sellers.get("vendedores_ativos")),
        "margem_media_pct": _round(item_row.get("margem_media")),
        "lucro_bruto_estimado": _round(item_row.get("lucro_bruto_estimado")),
    }


def _top_products(start: str, end: str, limit: int, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    rows = _fetchall(
        f"""
        SELECT
            i.id_produto,
            COALESCE(NULLIF(i.codigo_produto, ''), p.codigo, p.codigo_produto, '') AS codigo_produto,
            COALESCE(NULLIF(i.nome_produto, ''), p.nome, p.nome_produto, 'Produto sem nome') AS nome_produto,
            COALESCE(NULLIF(i.nome_categoria, ''), p.nome_categoria, 'Sem categoria') AS categoria,
            SUM(i.quantidade) AS quantidade,
            SUM(i.total_item) AS total_vendas,
            COUNT(DISTINCT i.id_venda) AS transacoes,
            AVG(i.preco_unitario) AS preco_medio,
            AVG(i.per_margem) AS margem_media,
            SUM(i.val_lucro) AS lucro_estimado,
            MAX(v.data_venda) AS ultima_venda
        FROM dw_itens_venda i
        JOIN dw_vendas v ON v.id_venda = i.id_venda
        LEFT JOIN dw_produtos p ON p.id_produto = i.id_produto
        WHERE {where}
        GROUP BY i.id_produto, 2, 3, 4
        ORDER BY total_vendas DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    total_period = sum(_float(row.get("total_vendas")) for row in rows) or 1.0
    return [
        {
            "id_produto": row.get("id_produto"),
            "codigo_produto": row.get("codigo_produto"),
            "nome_produto": row.get("nome_produto"),
            "categoria": row.get("categoria"),
            "quantidade": _round(row.get("quantidade")),
            "total_vendas": _round(row.get("total_vendas")),
            "participacao_pct": round(_float(row.get("total_vendas")) / total_period * 100.0, 1),
            "transacoes": _int(row.get("transacoes")),
            "preco_medio": _round(row.get("preco_medio")),
            "margem_media_pct": _round(row.get("margem_media")),
            "lucro_estimado": _round(row.get("lucro_estimado")),
            "ultima_venda": row.get("ultima_venda"),
        }
        for row in rows
    ]


def _top_sellers(start: str, end: str, limit: int, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    where = f"{where} AND COALESCE(v.id_vendedor, '') != ''"
    rows = _fetchall(
        f"""
        SELECT
            COALESCE(NULLIF(v.id_vendedor, ''), 'sem-vendedor') AS id_vendedor,
            COALESCE(NULLIF(v.nome_vendedor, ''), NULLIF(v.id_vendedor, ''), 'Sem vendedor') AS nome_vendedor,
            SUM(v.total_liquido) AS total_vendas,
            COUNT(DISTINCT v.id_venda) AS transacoes,
            AVG(v.total_liquido) AS ticket_medio,
            SUM(COALESCE(i.itens_vendidos, 0)) AS itens_vendidos,
            SUM(COALESCE(i.skus_vendidos, 0)) AS skus_vendidos,
            MAX(v.data_venda) AS ultima_venda
        FROM dw_vendas v
        LEFT JOIN (
            SELECT id_venda, SUM(quantidade) AS itens_vendidos, COUNT(DISTINCT id_produto) AS skus_vendidos
            FROM dw_itens_venda
            GROUP BY id_venda
        ) i ON i.id_venda = v.id_venda
        WHERE {where}
        GROUP BY id_vendedor, nome_vendedor
        ORDER BY total_vendas DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    return [
        {
            "id_vendedor": row.get("id_vendedor"),
            "nome_vendedor": row.get("nome_vendedor"),
            "total_vendas": _round(row.get("total_vendas")),
            "transacoes": _int(row.get("transacoes")),
            "ticket_medio": _round(row.get("ticket_medio")),
            "itens_vendidos": _round(row.get("itens_vendidos")),
            "skus_vendidos": _int(row.get("skus_vendidos")),
            "ultima_venda": row.get("ultima_venda"),
        }
        for row in rows
    ]


def _category_mix(start: str, end: str, limit: int, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    rows = _fetchall(
        f"""
        SELECT
            COALESCE(NULLIF(i.nome_categoria, ''), p.nome_categoria, 'Sem categoria') AS categoria,
            SUM(i.total_item) AS total_vendas,
            SUM(i.quantidade) AS quantidade,
            COUNT(DISTINCT i.id_produto) AS skus
        FROM dw_itens_venda i
        JOIN dw_vendas v ON v.id_venda = i.id_venda
        LEFT JOIN dw_produtos p ON p.id_produto = i.id_produto
        WHERE {where}
        GROUP BY categoria
        ORDER BY total_vendas DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    return [
        {
            "categoria": row.get("categoria"),
            "total_vendas": _round(row.get("total_vendas")),
            "quantidade": _round(row.get("quantidade")),
            "skus": _int(row.get("skus")),
        }
        for row in rows
    ]


def _inventory(limit: int) -> dict[str, Any]:
    rows = _fetchall(
        """
        SELECT
            e.id_produto,
            e.codigo_produto,
            COALESCE(NULLIF(e.nome_produto, ''), p.nome, p.nome_produto, 'Produto sem nome') AS nome_produto,
            COALESCE(NULLIF(e.nome_deposito, ''), 'Padrao') AS deposito,
            e.quantidade,
            e.estoque_minimo,
            e.consumo_medio_dia,
            e.dias_cobertura,
            e.nivel_alerta,
            e.data_ruptura_prev,
            p.nome_categoria,
            p.preco_custo,
            p.preco_venda
        FROM dw_estoque e
        LEFT JOIN dw_produtos p ON p.id_produto = e.id_produto
        ORDER BY
            CASE e.nivel_alerta
                WHEN 'critico' THEN 1
                WHEN 'alerta' THEN 2
                WHEN 'atencao' THEN 3
                WHEN 'ok' THEN 4
                ELSE 5
            END,
            e.dias_cobertura ASC
        """
    )
    resumo = {"critico": 0, "alerta": 0, "atencao": 0, "ok": 0, "sem_classificacao": 0}
    stock_value = 0.0
    coverage_values: list[float] = []
    suggestions: list[dict[str, Any]] = []

    for row in rows:
        status = str(row.get("nivel_alerta") or "sem_classificacao")
        if status not in resumo:
            status = "sem_classificacao"
        resumo[status] += 1
        qty = _float(row.get("quantidade"))
        stock_value += qty * _float(row.get("preco_custo") or row.get("preco_venda"))
        coverage = row.get("dias_cobertura")
        if coverage is not None:
            coverage_values.append(_float(coverage))
        if status in {"critico", "alerta", "atencao"}:
            consumo = _float(row.get("consumo_medio_dia"))
            minimo = _float(row.get("estoque_minimo"))
            target = max(minimo * 2.0, consumo * 30.0)
            suggested = max(0.0, target - qty)
            suggestions.append(
                {
                    "id_produto": row.get("id_produto"),
                    "codigo_produto": row.get("codigo_produto"),
                    "nome_produto": row.get("nome_produto"),
                    "categoria": row.get("nome_categoria"),
                    "deposito": row.get("deposito"),
                    "quantidade_atual": round(qty, 2),
                    "estoque_minimo": _round(row.get("estoque_minimo")),
                    "consumo_medio_dia": _round(row.get("consumo_medio_dia")),
                    "dias_cobertura": _round(row.get("dias_cobertura"), 1),
                    "nivel_alerta": row.get("nivel_alerta"),
                    "data_ruptura_prevista": row.get("data_ruptura_prev"),
                    "quantidade_sugerida_compra": round(suggested, 0),
                    "valor_estimado_compra": round(suggested * _float(row.get("preco_custo") or row.get("preco_venda")), 2),
                }
            )

    return {
        "total_posicoes": len(rows),
        "resumo_alertas": resumo,
        "valor_estoque_estimado": round(stock_value, 2),
        "cobertura_media_dias": round(statistics.mean(coverage_values), 1) if coverage_values else None,
        "reposicao_sugerida": suggestions[:limit],
        "sem_estoque_configurado": len(rows) == 0,
    }


def _advanced_stats(daily: list[dict[str, Any]], top_products: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_float(row.get("total_vendas")) for row in daily]
    transactions = [_int(row.get("transacoes")) for row in daily]
    weekday: dict[int, list[float]] = defaultdict(list)
    for row in daily:
        d = _safe_date(row.get("data"))
        if d:
            weekday[d.weekday()].append(_float(row.get("total_vendas")))

    weekday_names = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
    weekday_pattern = [
        {"dia_semana": weekday_names[dow], "media_vendas": round(statistics.mean(vals), 2), "amostras": len(vals)}
        for dow, vals in sorted(weekday.items())
        if vals
    ]

    revenues = sorted([_float(p.get("total_vendas")) for p in top_products], reverse=True)
    q80 = revenues[max(0, math.floor(len(revenues) * 0.2) - 1)] if revenues else 0.0
    q50 = revenues[max(0, math.floor(len(revenues) * 0.5) - 1)] if revenues else 0.0
    clusters = []
    for product in top_products:
        revenue = _float(product.get("total_vendas"))
        if revenue >= q80 and revenue > 0:
            cluster = "campeoes_de_receita"
        elif revenue >= q50 and revenue > 0:
            cluster = "alto_giro"
        elif _float(product.get("quantidade")) > 0:
            cluster = "cauda_longa"
        else:
            cluster = "sem_movimento"
        clusters.append({**product, "cluster": cluster})

    return {
        "media_vendas_dia": round(statistics.mean(values), 2) if values else 0.0,
        "mediana_vendas_dia": round(statistics.median(values), 2) if values else 0.0,
        "desvio_padrao_vendas_dia": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "media_transacoes_dia": round(statistics.mean(transactions), 2) if transactions else 0.0,
        "padrao_dia_semana": weekday_pattern,
        "clusters_produtos": clusters,
    }


def _sales_forecast(daily: list[dict[str, Any]], horizon_days: int = 30) -> dict[str, Any]:
    values = [_float(row.get("total_vendas")) for row in daily]
    dates = [_safe_date(row.get("data")) for row in daily]
    dates = [d for d in dates if d is not None]
    if not values or not dates:
        return {
            "modelo": "media_movel_sazonal",
            "status": "dados_insuficientes",
            "previsao": [],
            "historico": [],
        }

    recent_window = values[-min(14, len(values)) :]
    previous_window = values[-min(28, len(values)) : -min(14, len(values))] if len(values) > 14 else []
    recent_avg = statistics.mean(recent_window) if recent_window else 0.0
    previous_avg = statistics.mean(previous_window) if previous_window else recent_avg
    growth = ((recent_avg - previous_avg) / previous_avg * 100.0) if previous_avg > 0 else 0.0
    trend = "alta" if growth > 5 else ("queda" if growth < -5 else "estavel")

    if len(values) > 1:
        x_mean = (len(values) - 1) / 2
        y_mean = statistics.mean(values)
        numerator = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values))
        denominator = sum((idx - x_mean) ** 2 for idx in range(len(values))) or 1.0
        slope = numerator / denominator
    else:
        slope = 0.0

    by_weekday: dict[int, list[float]] = defaultdict(list)
    for row in daily:
        d = _safe_date(row.get("data"))
        if d:
            by_weekday[d.weekday()].append(_float(row.get("total_vendas")))
    global_avg = statistics.mean(values) if values else 0.0
    seasonality = {
        dow: (statistics.mean(items) / global_avg if global_avg > 0 and items else 1.0)
        for dow, items in by_weekday.items()
    }
    deviation = statistics.stdev(recent_window) if len(recent_window) > 1 else max(recent_avg * 0.1, 0.0)
    last_date = max(dates)
    forecast = []
    for day_offset in range(1, max(1, min(horizon_days, 90)) + 1):
        forecast_date = last_date + timedelta(days=day_offset)
        seasonal_factor = seasonality.get(forecast_date.weekday(), 1.0)
        projected = max(0.0, (recent_avg + slope * day_offset) * seasonal_factor)
        forecast.append(
            {
                "data": forecast_date.isoformat(),
                "vendas_previstas": round(projected, 2),
                "confianca_min": round(max(0.0, projected - deviation), 2),
                "confianca_max": round(projected + deviation, 2),
            }
        )

    return {
        "modelo": "media_movel_sazonal",
        "status": "ok",
        "horizonte_dias": len(forecast),
        "tendencia": trend,
        "crescimento_pct": round(growth, 1),
        "media_recente_dia": round(recent_avg, 2),
        "total_previsto_7_dias": round(sum(_float(row["vendas_previstas"]) for row in forecast[:7]), 2),
        "total_previsto_30_dias": round(sum(_float(row["vendas_previstas"]) for row in forecast[:30]), 2),
        "previsao": forecast,
        "historico": [
            {"data": row.get("data"), "vendas": _round(row.get("total_vendas"))}
            for row in daily[-30:]
        ],
    }


def _coverage() -> dict[str, Any]:
    sales_dates = _fetchone("SELECT MIN(data_venda) AS inicio, MAX(data_venda) AS fim FROM dw_vendas")
    last_etl = _fetchone(
        """
        SELECT tipo, status, iniciado_em, concluido_em, registros_ext, registros_ins, erro_msg
        FROM dw_etl_log
        ORDER BY id DESC
        LIMIT 1
        """
    )
    return {
        "datas_vendas": {"inicio": sales_dates.get("inicio"), "fim": sales_dates.get("fim")},
        "tabelas": {
            "vendas": _table_count("dw_vendas"),
            "itens_venda": _table_count("dw_itens_venda"),
            "clientes": _table_count("dw_clientes"),
            "produtos": _table_count("dw_produtos"),
            "estoque": _table_count("dw_estoque"),
        },
        "ultimo_etl": last_etl or None,
    }


def _unit_ranking(start: str, end: str, unit_keys: list[str]) -> list[dict[str, Any]]:
    where, params = _sales_filter_clause("v", start, end, unit_keys)
    rows = _fetchall(
        f"""
        SELECT COALESCE(v.nome_vendedor, '') AS nome_vendedor,
               SUM(v.total_liquido) AS total_vendas,
               COUNT(DISTINCT v.id_venda) AS transacoes
        FROM dw_vendas v
        WHERE {where}
        GROUP BY COALESCE(v.nome_vendedor, '')
        """,
        params,
    )
    totals_by_unit: dict[str, dict[str, Any]] = {
        option["key"]: {
            "key": option["key"],
            "label": option["label"],
            "total_vendas": 0.0,
            "transacoes": 0,
        }
        for option in _UNIT_OPTIONS
    }
    for row in rows:
        nome = str(row.get("nome_vendedor") or "").lower()
        for option in _UNIT_OPTIONS:
            if any(pattern in nome for pattern in option["patterns"]):
                item = totals_by_unit[option["key"]]
                item["total_vendas"] += _float(row.get("total_vendas"))
                item["transacoes"] += _int(row.get("transacoes"))
                break

    total_all = sum(item["total_vendas"] for item in totals_by_unit.values()) or 1.0
    ordered = sorted(totals_by_unit.values(), key=lambda item: item["total_vendas"], reverse=True)
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "total_vendas": round(item["total_vendas"], 2),
            "transacoes": item["transacoes"],
            "participacao_pct": round(item["total_vendas"] / total_all * 100.0, 1),
        }
        for item in ordered
    ]


def build_manager_snapshot(period_days: int = 90, limit: int = 15, unit_keys: list[str] | None = None) -> dict[str, Any]:
    """Return the complete manager dashboard payload."""
    period_days = max(1, min(int(period_days or 90), 3650))
    limit = max(1, min(int(limit or 15), 100))
    selected_units = _normalize_unit_keys(unit_keys)
    start, end, previous_start = _period_bounds(period_days)
    sales = _sales_rows(start, end, selected_units)
    daily = _daily_sales(start, end, selected_units)
    periods = _aggregate_periods(daily)
    top_products = _top_products(start, end, limit, selected_units)

    return {
        "ok": True,
        "gerado_em": _utc_now(),
        "resumo": _summary(period_days, start, end, previous_start, sales, selected_units),
        "series": periods,
        "produtos": {
            "top_vendas": top_products,
            "categorias": _category_mix(start, end, limit, selected_units),
        },
        "vendedores": {"ranking": _top_sellers(start, end, limit, selected_units)},
        "unidades": {
            "selected": selected_units,
            "available": [{"key": item["key"], "label": item["label"]} for item in _UNIT_OPTIONS],
            "ranking": _unit_ranking(start, end, selected_units),
        },
        "estoque": _inventory(limit),
        "estatistica": _advanced_stats(daily, top_products),
        "preditivo": _sales_forecast(daily),
        "cobertura_dados": _coverage(),
    }
