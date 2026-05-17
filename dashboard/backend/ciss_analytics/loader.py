"""
Loader: persistencia de dados transformados no Data Warehouse CISS.

Usa UPSERT nativo por dialeto (PostgreSQL/SQLite) via SQLAlchemy Core.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from .schema import TABLES, DwConnection, get_dw_conn

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.utcnow().isoformat()


def _validate_table_and_columns(
    table_name: str,
    rows: list[dict],
    pk_column: str | tuple[str, ...],
):
    try:
        table = TABLES[table_name]
    except KeyError as exc:
        raise ValueError(f"Tabela DW nao permitida: {table_name}") from exc

    valid_columns = set(table.c.keys())
    pk_columns = (pk_column,) if isinstance(pk_column, str) else tuple(pk_column)
    for pk in pk_columns:
        if pk not in valid_columns:
            raise ValueError(f"PK invalida para {table_name}: {pk}")

    for row in rows:
        invalid = set(row) - valid_columns
        if invalid:
            cols = ", ".join(sorted(invalid))
            raise ValueError(f"Colunas invalidas para {table_name}: {cols}")
    return table, pk_columns


def _build_upsert(
    conn: DwConnection,
    table,
    pk_columns: tuple[str, ...],
    columns: tuple[str, ...],
):
    if conn.dialect_name == "postgresql":
        stmt = pg_insert(table)
    elif conn.dialect_name == "sqlite":
        stmt = sqlite_insert(table)
    else:
        raise RuntimeError(f"Dialeto nao suportado pelo DW CISS: {conn.dialect_name}")

    update_columns = [c for c in columns if c not in pk_columns]
    index_elements = [table.c[column] for column in pk_columns]
    if not update_columns:
        return stmt.on_conflict_do_nothing(index_elements=index_elements)

    return stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_={column: getattr(stmt.excluded, column) for column in update_columns},
    )


def _upsert_batch(
    conn: DwConnection,
    table_name: str,
    rows: list[dict],
    pk_column: str | tuple[str, ...] = "id",
) -> int:
    """Executa UPSERT em lote. Retorna numero de registros processados."""
    if not rows:
        return 0

    table, pk_columns = _validate_table_and_columns(table_name, rows, pk_column)
    grouped_rows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        columns = tuple(row.keys())
        if not all(pk in columns for pk in pk_columns):
            missing = [pk for pk in pk_columns if pk not in columns]
            raise ValueError(f"Registro sem PK {','.join(missing)} para {table_name}")
        grouped_rows.setdefault(columns, []).append(row)

    try:
        raw = conn.raw_connection()
        for columns, group in grouped_rows.items():
            stmt = _build_upsert(conn, table, pk_columns, columns)
            raw.execute(stmt, group)
        conn.commit()
        return len(rows)
    except SQLAlchemyError as exc:
        conn.rollback()
        logger.error("Erro no UPSERT em %s: %s", table_name, exc)
        raise


def carregar_vendas(vendas: list[dict]) -> int:
    """Persiste vendas no DW. Retorna quantidade inserida/atualizada."""
    conn = get_dw_conn()
    try:
        n = _upsert_batch(conn, "dw_vendas", vendas, "id_venda")
        logger.info("Carregadas %d vendas no DW", n)
        return n
    finally:
        conn.close()


def carregar_itens_venda(itens: list[dict]) -> int:
    """Persiste itens de venda no DW."""
    conn = get_dw_conn()
    try:
        n = _upsert_batch(conn, "dw_itens_venda", itens, "id_item")
        logger.info("Carregados %d itens de venda no DW", n)
        return n
    finally:
        conn.close()


def carregar_clientes(clientes: list[dict]) -> int:
    """Persiste clientes no DW."""
    conn = get_dw_conn()
    try:
        n = _upsert_batch(conn, "dw_clientes", clientes, "id_cliente")
        logger.info("Carregados %d clientes no DW", n)
        return n
    finally:
        conn.close()


def carregar_produtos(produtos: list[dict]) -> int:
    """Persiste produtos no DW."""
    conn = get_dw_conn()
    try:
        n = _upsert_batch(conn, "dw_produtos", produtos, "id_produto")
        logger.info("Carregados %d produtos no DW", n)
        return n
    finally:
        conn.close()


def carregar_estoque(estoque: list[dict]) -> int:
    """Persiste posicao de estoque no DW."""
    conn = get_dw_conn()
    try:
        n = _upsert_batch(conn, "dw_estoque", estoque, "id_estoque")
        logger.info("Carregados %d registros de estoque no DW", n)
        return n
    finally:
        conn.close()


def materializar_kpis_diarios() -> None:
    """
    Recalcula e materializa KPIs diarios na tabela dw_kpis_diarios.
    Executado ao final de cada ciclo ETL de vendas.
    """
    conn = get_dw_conn()
    try:
        conn.execute(
            """
            INSERT INTO dw_kpis_diarios
                (data_ref, total_vendas, total_transacoes, ticket_medio,
                 itens_vendidos, calculado_em)
            SELECT
                v.data_venda                        AS data_ref,
                SUM(v.total_liquido)                AS total_vendas,
                COUNT(DISTINCT v.id_venda)          AS total_transacoes,
                AVG(v.total_liquido)                AS ticket_medio,
                COALESCE(SUM(i.qtd_itens), 0)       AS itens_vendidos,
                CURRENT_TIMESTAMP                   AS calculado_em
            FROM dw_vendas v
            LEFT JOIN (
                SELECT id_venda, SUM(quantidade) AS qtd_itens
                FROM dw_itens_venda GROUP BY id_venda
            ) i ON i.id_venda = v.id_venda
            WHERE v.status != 'cancelada'
            GROUP BY v.data_venda
            ON CONFLICT(data_ref) DO UPDATE SET
                total_vendas = excluded.total_vendas,
                total_transacoes = excluded.total_transacoes,
                ticket_medio = excluded.ticket_medio,
                itens_vendidos = excluded.itens_vendidos,
                calculado_em = excluded.calculado_em
            """
        )
        conn.commit()
        logger.info("KPIs diarios materializados com sucesso")
    except SQLAlchemyError as exc:
        conn.rollback()
        logger.error("Erro ao materializar KPIs: %s", exc)
        raise
    finally:
        conn.close()


def _dias_desde(data_iso: str | None) -> int | None:
    if not data_iso:
        return None
    try:
        return (date.today() - date.fromisoformat(str(data_iso)[:10])).days
    except ValueError:
        return None


def _classificacao_abc(rows: list[dict[str, Any]]) -> dict[str, str]:
    totais = sorted(
        (
            (str(row.get("id_cliente", "")).strip(), float(row.get("valor_total") or 0.0))
            for row in rows
            if str(row.get("id_cliente", "")).strip()
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    soma_total = sum(valor for _, valor in totais) or 1.0
    acumulado = 0.0
    classes: dict[str, str] = {}
    for id_cliente, valor in totais:
        acumulado += valor
        pct = acumulado / soma_total
        if pct <= 0.80:
            classes[id_cliente] = "A"
        elif pct <= 0.95:
            classes[id_cliente] = "B"
        else:
            classes[id_cliente] = "C"
    return classes


def materializar_metricas_clientes() -> dict[str, int]:
    """
    Materializa métricas cliente e cliente x produto em tabelas Gold.

    Regras:
    - Resolve id_cliente da venda com fallback do item.
    - Ignora vendas canceladas.
    - Classificação ABC por valor acumulado de compras.
    """
    conn = get_dw_conn()
    try:
        fallback = conn.execute(
            """
            SELECT id_venda, MAX(NULLIF(id_cliente, '')) AS id_cliente_item
            FROM dw_itens_venda
            GROUP BY id_venda
            """
        ).fetchall()
        fallback_map = {str(r["id_venda"]): str(r["id_cliente_item"] or "") for r in fallback}

        vendas_rows = conn.execute(
            """
            SELECT
                v.id_venda,
                v.id_cliente,
                v.total_liquido,
                v.data_venda,
                COALESCE(NULLIF(c.nome, ''), NULLIF(v.nome_cliente, ''), v.id_cliente) AS nome,
                c.cidade,
                c.estado,
                c.rfm_segmento
            FROM dw_vendas v
            LEFT JOIN dw_clientes c ON c.id_cliente = v.id_cliente
            WHERE COALESCE(v.status, '') NOT IN ('cancelada', 'cancelado', 'canceled')
            """
        ).fetchall()

        vendas_por_cliente: dict[str, dict[str, Any]] = {}
        for row in vendas_rows:
            id_venda = str(row["id_venda"] or "").strip()
            id_cliente = str(row["id_cliente"] or "").strip() or fallback_map.get(id_venda, "")
            if not id_cliente:
                continue
            data = vendas_por_cliente.setdefault(
                id_cliente,
                {
                    "id_cliente": id_cliente,
                    "nome_cliente": str(row["nome"] or "").strip(),
                    "cidade": str(row["cidade"] or "").strip(),
                    "estado": str(row["estado"] or "").strip(),
                    "segmento_rfm": str(row["rfm_segmento"] or "").strip(),
                    "num_compras": 0,
                    "valor_total": 0.0,
                    "primeira_compra": None,
                    "ultima_compra": None,
                    "_vendas": set(),
                },
            )

            data["_vendas"].add(id_venda)
            data["valor_total"] += float(row["total_liquido"] or 0.0)
            venda_data = str(row["data_venda"] or "")[:10]
            if venda_data:
                if not data["primeira_compra"] or venda_data < data["primeira_compra"]:
                    data["primeira_compra"] = venda_data
                if not data["ultima_compra"] or venda_data > data["ultima_compra"]:
                    data["ultima_compra"] = venda_data

        itens_rows = conn.execute(
            """
            SELECT
                i.id_venda,
                i.id_cliente,
                i.id_produto,
                COALESCE(NULLIF(i.nome_produto, ''), p.nome, p.nome_produto, 'Produto sem nome') AS nome_produto,
                COALESCE(NULLIF(i.nome_categoria, ''), p.nome_categoria, 'Sem categoria') AS categoria,
                i.quantidade,
                i.total_item,
                i.preco_unitario,
                v.data_venda
            FROM dw_itens_venda i
            JOIN dw_vendas v ON v.id_venda = i.id_venda
            LEFT JOIN dw_produtos p ON p.id_produto = i.id_produto
            WHERE COALESCE(v.status, '') NOT IN ('cancelada', 'cancelado', 'canceled')
            """
        ).fetchall()

        itens_cliente_total: dict[str, float] = {}
        skus_cliente: dict[str, set[str]] = {}
        cliente_produto: dict[tuple[str, str], dict[str, Any]] = {}

        for row in itens_rows:
            id_venda = str(row["id_venda"] or "").strip()
            id_cliente = str(row["id_cliente"] or "").strip() or fallback_map.get(id_venda, "")
            id_produto = str(row["id_produto"] or "").strip()
            if not id_cliente or not id_produto:
                continue

            qtd = float(row["quantidade"] or 0.0)
            total_item = float(row["total_item"] or 0.0)
            preco = float(row["preco_unitario"] or 0.0)
            key = (id_cliente, id_produto)

            itens_cliente_total[id_cliente] = itens_cliente_total.get(id_cliente, 0.0) + qtd
            skus_cliente.setdefault(id_cliente, set()).add(id_produto)

            agg = cliente_produto.setdefault(
                key,
                {
                    "id_cliente": id_cliente,
                    "id_produto": id_produto,
                    "nome_produto": str(row["nome_produto"] or "").strip(),
                    "categoria": str(row["categoria"] or "").strip(),
                    "quantidade_total": 0.0,
                    "valor_total": 0.0,
                    "num_compras": 0,
                    "_vendas": set(),
                    "_ultima_compra": None,
                    "_preco_sum": 0.0,
                    "_preco_count": 0,
                },
            )
            agg["quantidade_total"] += qtd
            agg["valor_total"] += total_item
            agg["_vendas"].add(id_venda)
            if preco > 0:
                agg["_preco_sum"] += preco
                agg["_preco_count"] += 1
            venda_data = str(row["data_venda"] or "")[:10]
            if venda_data and (not agg["_ultima_compra"] or venda_data > agg["_ultima_compra"]):
                agg["_ultima_compra"] = venda_data

        clientes_rows = list(vendas_por_cliente.values())
        abc_map = _classificacao_abc(clientes_rows)
        clientes_payload: list[dict[str, Any]] = []
        for row in clientes_rows:
            id_cliente = row["id_cliente"]
            num_compras = len(row["_vendas"])
            valor_total = float(row["valor_total"] or 0.0)
            clientes_payload.append(
                {
                    "id_cliente": id_cliente,
                    "nome_cliente": row.get("nome_cliente") or "",
                    "cidade": row.get("cidade") or "",
                    "estado": row.get("estado") or "",
                    "segmento_rfm": row.get("segmento_rfm") or "",
                    "classificacao_abc": abc_map.get(id_cliente, "C"),
                    "num_compras": num_compras,
                    "itens_comprados": float(itens_cliente_total.get(id_cliente, 0.0)),
                    "valor_total": valor_total,
                    "ticket_medio": (valor_total / num_compras) if num_compras else 0.0,
                    "skus_distintos": len(skus_cliente.get(id_cliente, set())),
                    "primeira_compra": row.get("primeira_compra"),
                    "ultima_compra": row.get("ultima_compra"),
                    "dias_desde_ultima": _dias_desde(row.get("ultima_compra")),
                    "atualizado_em": _NOW(),
                }
            )

        produto_por_cliente: dict[str, list[dict[str, Any]]] = {}
        for agg in cliente_produto.values():
            id_cliente = agg["id_cliente"]
            produto_por_cliente.setdefault(id_cliente, []).append(agg)

        cliente_produto_payload: list[dict[str, Any]] = []
        for id_cliente, rows in produto_por_cliente.items():
            total_valor_cliente = sum(float(r["valor_total"]) for r in rows) or 1.0
            total_qtd_cliente = sum(float(r["quantidade_total"]) for r in rows) or 1.0
            by_valor = sorted(rows, key=lambda r: float(r["valor_total"]), reverse=True)
            by_qtd = sorted(rows, key=lambda r: float(r["quantidade_total"]), reverse=True)
            rank_valor = {r["id_produto"]: idx for idx, r in enumerate(by_valor, 1)}
            rank_qtd = {r["id_produto"]: idx for idx, r in enumerate(by_qtd, 1)}

            for row in rows:
                id_produto = row["id_produto"]
                cliente_produto_payload.append(
                    {
                        "id_cliente": id_cliente,
                        "id_produto": id_produto,
                        "nome_produto": row["nome_produto"],
                        "categoria": row["categoria"],
                        "quantidade_total": float(row["quantidade_total"]),
                        "valor_total": float(row["valor_total"]),
                        "num_compras": len(row["_vendas"]),
                        "preco_medio": (
                            row["_preco_sum"] / row["_preco_count"]
                            if row["_preco_count"]
                            else None
                        ),
                        "ultima_compra": row["_ultima_compra"],
                        "rank_valor": rank_valor.get(id_produto),
                        "rank_quantidade": rank_qtd.get(id_produto),
                        "participacao_valor_cliente": (
                            float(row["valor_total"]) / total_valor_cliente * 100.0
                        ),
                        "participacao_qtd_cliente": (
                            float(row["quantidade_total"]) / total_qtd_cliente * 100.0
                        ),
                        "atualizado_em": _NOW(),
                    }
                )

        conn.execute("DELETE FROM dw_cliente_metricas")
        conn.execute("DELETE FROM dw_cliente_produto_metricas")
        _upsert_batch(conn, "dw_cliente_metricas", clientes_payload, "id_cliente")
        _upsert_batch(
            conn,
            "dw_cliente_produto_metricas",
            cliente_produto_payload,
            ("id_cliente", "id_produto"),
        )
        logger.info(
            "Metricas clientes materializadas: %d clientes, %d cliente-produto",
            len(clientes_payload),
            len(cliente_produto_payload),
        )
        return {
            "clientes": len(clientes_payload),
            "cliente_produto": len(cliente_produto_payload),
        }
    finally:
        conn.close()


def registrar_etl(
    tipo: str,
    status: str,
    iniciado_em: str,
    registros_ext: int = 0,
    registros_ins: int = 0,
    ultima_data_ref: str | None = None,
    erro_msg: str | None = None,
) -> None:
    """Registra execucao do ETL na tabela de log."""
    conn = get_dw_conn()
    try:
        conn.execute(
            TABLES["dw_etl_log"].insert().values(
                tipo=tipo,
                iniciado_em=iniciado_em,
                concluido_em=_NOW(),
                status=status,
                registros_ext=registros_ext,
                registros_ins=registros_ins,
                ultima_data_ref=ultima_data_ref,
                erro_msg=erro_msg,
            )
        )
        conn.commit()
    finally:
        conn.close()
