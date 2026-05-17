"""Enriquecimento de nomes do DW CISS.

Alguns servicos do Integrim retornam vendas apenas com IDs. Este modulo
preenche nomes a partir do catalogo local e do cadastro de pessoas.
"""

from __future__ import annotations

import logging
from typing import Any

from .ciss_client import get_client
from .schema import get_dw_conn

logger = logging.getLogger(__name__)


def _clausula_igual(campo: str, valor: str) -> dict[str, str]:
    return {"campo": campo, "valor": valor, "operador": "IGUAL", "operadorLogico": "AND"}


def _clausula_maior_igual(campo: str, valor: str) -> dict[str, str]:
    return {"campo": campo, "valor": valor, "operador": "MAIOR_IGUAL", "operadorLogico": "AND"}


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _nome_pessoa(registros: list[dict[str, Any]]) -> str:
    if not registros:
        return ""

    def score(row: dict[str, Any]) -> int:
        tipo = str(row.get("tipocadastro") or "").upper()
        if "V" in tipo:
            return 0
        if tipo in {"A", "F", "C"}:
            return 1
        return 2

    row = sorted(registros, key=score)[0]
    return str(row.get("nome") or row.get("nomefantasia") or "").strip()


def enrich_product_names() -> int:
    """Preenche nome/codigo/categoria dos itens de venda via dw_produtos."""
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                i.id_item,
                COALESCE(NULLIF(p.nome, ''), NULLIF(p.nome_produto, '')) AS nome_produto,
                COALESCE(NULLIF(p.codigo, ''), NULLIF(p.codigo_produto, '')) AS codigo_produto,
                NULLIF(p.nome_categoria, '') AS nome_categoria
            FROM dw_itens_venda i
            JOIN dw_produtos p ON p.id_produto = i.id_produto
            WHERE COALESCE(i.nome_produto, '') = ''
              AND COALESCE(NULLIF(p.nome, ''), NULLIF(p.nome_produto, '')) IS NOT NULL
            """
        ).fetchall()
        params = [
            (
                row["nome_produto"],
                row["codigo_produto"] or "",
                row["nome_categoria"] or "",
                row["id_item"],
            )
            for row in rows
            if row["nome_produto"]
        ]
        if not params:
            return 0
        conn.executemany(
            """
            UPDATE dw_itens_venda
               SET nome_produto = ?,
                   codigo_produto = COALESCE(NULLIF(codigo_produto, ''), ?),
                   nome_categoria = COALESCE(NULLIF(nome_categoria, ''), ?)
             WHERE id_item = ?
            """,
            params,
        )
        conn.commit()
        return len(params)
    finally:
        conn.close()


def _min_sale_date_without_customer() -> str:
    conn = get_dw_conn()
    try:
        row = conn.execute(
            """
            SELECT MIN(data_venda) AS data_inicio
            FROM dw_vendas
            WHERE COALESCE(id_cliente, '') = ''
            """
        ).fetchone()
        return str(row["data_inicio"] or "").strip() if row else ""
    finally:
        conn.close()


def _sale_ids_without_customer() -> set[str]:
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT id_venda
            FROM dw_vendas
            WHERE COALESCE(id_cliente, '') = ''
            """
        ).fetchall()
        return {_safe_str(row["id_venda"]) for row in rows if _safe_str(row["id_venda"])}
    finally:
        conn.close()


def _put_customer_mapping(
    mapping: dict[str, tuple[int, dict[str, str]]],
    sale_ids: set[str],
    id_venda: Any,
    row: dict[str, Any],
    priority: int,
) -> None:
    sale_id = _safe_str(id_venda)
    id_cliente = _safe_str(row.get("idclifor") or row.get("id_cliente") or row.get("idcliente"))
    if not sale_id or sale_id not in sale_ids or not id_cliente:
        return

    current = mapping.get(sale_id)
    if current and current[0] <= priority:
        return

    mapping[sale_id] = (
        priority,
        {
            "id_venda": sale_id,
            "id_cliente": id_cliente,
            "nome_cliente": _safe_str(row.get("nome") or row.get("nome_cliente")),
            "id_vendedor": _safe_str(row.get("idvendedor") or row.get("id_vendedor")),
            "nome_vendedor": _safe_str(row.get("nomevendedor") or row.get("nome_vendedor")),
            "id_empresa": _safe_str(row.get("idempresa") or row.get("id_empresa")),
        },
    )


def _apply_customer_mappings(mapping: dict[str, tuple[int, dict[str, str]]]) -> int:
    payload = [data for _, data in mapping.values()]
    if not payload:
        return 0

    params = [
        (
            row["id_cliente"],
            row["nome_cliente"],
            row["id_vendedor"],
            row["nome_vendedor"],
            row["id_empresa"],
            row["id_venda"],
        )
        for row in payload
    ]
    conn = get_dw_conn()
    try:
        conn.executemany(
            """
            UPDATE dw_vendas
               SET id_cliente = ?,
                   nome_cliente = COALESCE(NULLIF(nome_cliente, ''), ?),
                   id_vendedor = COALESCE(NULLIF(id_vendedor, ''), ?),
                   nome_vendedor = COALESCE(NULLIF(nome_vendedor, ''), ?),
                   id_empresa = COALESCE(NULLIF(id_empresa, ''), ?),
                   atualizado_em = CURRENT_TIMESTAMP
             WHERE id_venda = ?
               AND COALESCE(id_cliente, '') = ''
            """,
            params,
        )
        conn.commit()
        return len(params)
    finally:
        conn.close()


def propagate_customer_ids_to_items() -> int:
    """Copia id_cliente de dw_vendas para itens ainda sem cliente."""
    conn = get_dw_conn()
    try:
        before = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM dw_itens_venda i
            JOIN dw_vendas v ON v.id_venda = i.id_venda
            WHERE COALESCE(i.id_cliente, '') = ''
              AND COALESCE(v.id_cliente, '') != ''
            """
        ).fetchone()
        conn.execute(
            """
            UPDATE dw_itens_venda
               SET id_cliente = (
                   SELECT v.id_cliente
                   FROM dw_vendas v
                   WHERE v.id_venda = dw_itens_venda.id_venda
                   LIMIT 1
               )
             WHERE COALESCE(id_cliente, '') = ''
               AND EXISTS (
                   SELECT 1
                   FROM dw_vendas v
                   WHERE v.id_venda = dw_itens_venda.id_venda
                     AND COALESCE(v.id_cliente, '') != ''
               )
            """
        )
        conn.commit()
        return int(before["total"] or 0) if before else 0
    finally:
        conn.close()


def enrich_customer_links_from_ciss(
    data_inicio: str | None = None,
    max_pages: int | None = None,
    batch_pages: int = 25,
) -> dict[str, int | str]:
    """
    Relaciona vendas/itens ao cliente usando o cabecalho get_pedido_venda.

    get_pedido_venda_prod e uma carga de itens e nao traz cliente neste CIM.
    O cabecalho get_pedido_venda traz idclifor; o DW usa idorcamento como id_venda.
    """
    data_inicio = (data_inicio or _min_sale_date_without_customer())[:10]
    sale_ids = _sale_ids_without_customer()
    if not data_inicio or not sale_ids:
        itens = propagate_customer_ids_to_items()
        return {
            "data_inicio": data_inicio or "",
            "paginas": 0,
            "cabecalhos_lidos": 0,
            "vendas_mapeadas": 0,
            "itens_atualizados": itens,
        }

    client = get_client()
    page_limit = max_pages or 5000
    headers_read = 0
    mapped_sales = 0
    pages = 0
    pending: dict[str, tuple[int, dict[str, str]]] = {}
    clausulas = [_clausula_maior_igual("dtmovimento", data_inicio)]

    for page in range(1, page_limit + 1):
        data = client.post_service("get_pedido_venda", page=page, clausulas=clausulas)
        if not data:
            break

        rows = client._extrair_registros(data, "get_pedido_venda")
        pages = page
        headers_read += len(rows)
        for row in rows:
            _put_customer_mapping(pending, sale_ids, row.get("idorcamento"), row, priority=0)
            _put_customer_mapping(pending, sale_ids, row.get("idpedido"), row, priority=1)

        if page % batch_pages == 0:
            mapped_sales += _apply_customer_mappings(pending)
            sale_ids.difference_update(pending.keys())
            pending.clear()

        if not data.get("hasNext", False):
            break

    mapped_sales += _apply_customer_mappings(pending)
    sale_ids.difference_update(pending.keys())
    itens = propagate_customer_ids_to_items()
    result: dict[str, int | str] = {
        "data_inicio": data_inicio,
        "paginas": pages,
        "cabecalhos_lidos": headers_read,
        "vendas_mapeadas": mapped_sales,
        "itens_atualizados": itens,
    }
    logger.info("Relacionamento cliente-venda CISS concluido: %s", result)
    return result


def _seller_ids_without_name(limit: int) -> list[str]:
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT id_vendedor
            FROM dw_vendas
            WHERE COALESCE(id_vendedor, '') != ''
              AND (
                    COALESCE(nome_vendedor, '') = ''
                 OR COALESCE(nome_vendedor, '') = COALESCE(id_vendedor, '')
              )
            ORDER BY id_vendedor
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [str(row["id_vendedor"]).strip() for row in rows if row["id_vendedor"]]
    finally:
        conn.close()


def _apply_seller_names(names_by_id: dict[str, str]) -> int:
    if not names_by_id:
        return 0
    params = [(name, seller_id) for seller_id, name in names_by_id.items() if name]
    if not params:
        return 0
    conn = get_dw_conn()
    try:
        conn.executemany(
            """
            UPDATE dw_vendas
               SET nome_vendedor = ?
             WHERE id_vendedor = ?
               AND COALESCE(id_vendedor, '') != ''
            """,
            params,
        )
        conn.commit()
        return len(params)
    finally:
        conn.close()


def clear_names_without_seller_id() -> int:
    """Remove nomes em vendas sem id_vendedor para nao ranquear clientes/fornecedores."""
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT id_venda
            FROM dw_vendas
            WHERE COALESCE(id_vendedor, '') = ''
              AND COALESCE(nome_vendedor, '') != ''
            """
        ).fetchall()
        if not rows:
            return 0
        conn.execute(
            """
            UPDATE dw_vendas
               SET nome_vendedor = ''
             WHERE COALESCE(id_vendedor, '') = ''
               AND COALESCE(nome_vendedor, '') != ''
            """
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def enrich_seller_names(limit: int = 1000, fetch_from_ciss: bool = True) -> int:
    """Busca nomes de vendedores ausentes no cad_pessoas e atualiza dw_vendas."""
    seller_ids = _seller_ids_without_name(limit)
    if not seller_ids or not fetch_from_ciss:
        return 0

    client = get_client()
    names_by_id: dict[str, str] = {}
    for seller_id in seller_ids:
        try:
            data = client.post_service("cad_pessoas", clausulas=[_clausula_igual("idclifor", seller_id)])
            registros = client._extrair_registros(data, "cad_pessoas")
            nome = _nome_pessoa(registros)
            if nome:
                names_by_id[seller_id] = nome
        except Exception as exc:
            logger.warning("Falha enriquecendo vendedor %s: %s", seller_id, exc)

    return _apply_seller_names(names_by_id)


def enrich_dw_names(
    fetch_sellers: bool = True,
    seller_limit: int = 1000,
    fetch_customers: bool = True,
) -> dict[str, int]:
    """
    Executa enriquecimentos de nomes e vínculos usados pelo dashboard.

    Inclui relacionamento venda->cliente via get_pedido_venda, necessário para
    painel de clientes, RFM e métricas cliente x produto.
    """
    customer_link = (
        enrich_customer_links_from_ciss()
        if fetch_customers
        else {"vendas_mapeadas": 0, "itens_atualizados": 0}
    )
    result = {
        "itens_produto_atualizados": enrich_product_names(),
        "nomes_sem_id_vendedor_limpos": clear_names_without_seller_id(),
        "vendedores_atualizados": enrich_seller_names(limit=seller_limit, fetch_from_ciss=fetch_sellers),
        "clientes_vinculados": int(customer_link.get("vendas_mapeadas", 0)),
        "itens_cliente_propagados": int(customer_link.get("itens_atualizados", 0)),
    }
    logger.info("Enriquecimento CISS concluido: %s", result)
    return result
