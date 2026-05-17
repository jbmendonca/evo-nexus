"""Catalog, stock guardrails, and Integrim sale payload helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Callable, Mapping, Sequence

from .schema import get_dw_conn


ConnectionFactory = Callable[[], Any]


@dataclass(slots=True)
class ProductOffer:
    product_id: str
    codigo: str
    nome: str
    preco_venda: float
    quantidade: float
    unidade: str = "UN"
    deposito: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "codigo": self.codigo,
            "nome": self.nome,
            "preco_venda": self.preco_venda,
            "quantidade": self.quantidade,
            "unidade": self.unidade,
            "deposito": self.deposito,
        }


@dataclass(slots=True)
class SaleLine:
    product_id: str
    codigo: str
    nome: str
    quantity: float
    unit_price: float
    stock_available: float
    unidade: str = "UN"

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "codigo": self.codigo,
            "nome": self.nome,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "stock_available": self.stock_available,
            "unidade": self.unidade,
            "total": self.total,
        }


@dataclass(slots=True)
class ResolvedSale:
    lines: list[SaleLine]
    unresolved: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "unresolved": self.unresolved,
        }


@dataclass(slots=True)
class StockIssue:
    product_id: str
    nome: str
    requested: float
    available: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "nome": self.nome,
            "requested": self.requested,
            "available": self.available,
        }


@dataclass(slots=True)
class StockValidation:
    ok: bool
    issues: list[StockIssue]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}


class OutOfStockError(RuntimeError):
    """Raised when a sale payload is requested for unavailable stock."""

    def __init__(self, validation: StockValidation) -> None:
        super().__init__("Stock validation failed.")
        self.validation = validation


class DWCatalogService:
    """Reads catalog and stock from the CISS analytics DW."""

    def __init__(self, conn_factory: ConnectionFactory | None = None) -> None:
        self.conn_factory = conn_factory or get_dw_conn

    def search_catalog(self, query: str, limit: int = 5) -> list[ProductOffer]:
        term = _clean_query(query)
        if not term:
            return []
        conn = self.conn_factory()
        try:
            offers = self._search_products(conn, term, limit)
            if offers:
                return offers
            return self._search_stock(conn, term, limit)
        finally:
            _close_quietly(conn)

    def resolve_requested_items(
        self,
        requested_items: Sequence[Mapping[str, Any]],
    ) -> ResolvedSale:
        lines: list[SaleLine] = []
        unresolved: list[dict[str, Any]] = []
        for item in requested_items:
            query = _clean_query(item.get("query"))
            quantity = _safe_float(item.get("quantity"), default=1.0)
            offers = self.search_catalog(query, limit=1)
            if not offers:
                unresolved.append({"query": query, "quantity": quantity, "reason": "not_found"})
                continue
            offer = offers[0]
            lines.append(
                SaleLine(
                    product_id=offer.product_id,
                    codigo=offer.codigo,
                    nome=offer.nome,
                    quantity=quantity,
                    unit_price=offer.preco_venda,
                    stock_available=offer.quantidade,
                    unidade=offer.unidade,
                )
            )
        return ResolvedSale(lines=lines, unresolved=unresolved)

    def validate_stock(self, lines: Sequence[SaleLine]) -> StockValidation:
        if not lines:
            return StockValidation(ok=False, issues=[])
        current_stock = self._current_stock([line.product_id for line in lines])
        issues: list[StockIssue] = []
        for line in lines:
            available = current_stock.get(line.product_id, line.stock_available)
            if available < line.quantity:
                issues.append(
                    StockIssue(
                        product_id=line.product_id,
                        nome=line.nome,
                        requested=line.quantity,
                        available=available,
                    )
                )
        return StockValidation(ok=not issues, issues=issues)

    def prepare_sale_payload(
        self,
        *,
        chat_id: str,
        customer: Mapping[str, Any] | None,
        lines: Sequence[SaleLine],
        metadata: Mapping[str, Any] | None = None,
        revalidate: bool = True,
    ) -> dict[str, Any]:
        if revalidate:
            validation = self.validate_stock(lines)
            if not validation.ok:
                raise OutOfStockError(validation)

        now = datetime.now(timezone.utc).isoformat()
        customer_data = dict(customer or {})
        metadata_data = dict(metadata or {})
        idempotency_source = f"{chat_id}|{now}|{[(line.product_id, line.quantity) for line in lines]}"
        idempotency_key = "whatsapp:" + hashlib.sha256(idempotency_source.encode("utf-8")).hexdigest()[:32]

        body = {
            "origem": "whatsapp_evolution_api",
            "chat_id": chat_id,
            "cliente": customer_data,
            "data_emissao": now,
            "itens": [
                {
                    "idproduto": line.product_id,
                    "codigo": line.codigo,
                    "descricao": line.nome,
                    "quantidade": line.quantity,
                    "valor_unitario": line.unit_price,
                    "valor_total": line.total,
                    "unidade": line.unidade,
                }
                for line in lines
            ],
            "total_liquido": round(sum(line.total for line in lines), 2),
            "observacao": "Pedido originado do agente omnicanal EvoNexus.",
            "metadata": metadata_data,
        }
        return {
            "service": "post_pedido_venda",
            "method": "POST",
            "idempotency_key": idempotency_key,
            "body": body,
        }

    def _search_products(self, conn: Any, term: str, limit: int) -> list[ProductOffer]:
        product_cols = _table_columns(conn, "dw_produtos")
        if not product_cols:
            return []

        stock_cols = _table_columns(conn, "dw_estoque")
        pid = _coalesce_expr("p", product_cols, ("id_produto", "idproduto"), "''")
        codigo = _coalesce_expr(
            "p", product_cols, ("codigo", "codigo_produto", "nrcodbarprod"), "''"
        )
        nome = _coalesce_expr(
            "p",
            product_cols,
            ("nome", "nome_produto", "descrcomproduto", "descrresproduto", "descricao"),
            "''",
        )
        preco = _coalesce_expr(
            "p", product_cols, ("preco_venda", "valor_venda", "valunitbruto"), "0"
        )
        unidade = _coalesce_expr(
            "p", product_cols, ("unidade", "unidade_saida", "embalagemsaida"), "'UN'"
        )

        join_sql = ""
        qty = "0"
        deposito = "''"
        if {"id_produto", "quantidade"}.issubset(stock_cols):
            stock_pid = _coalesce_expr("e", stock_cols, ("id_produto", "idproduto"), "''")
            stock_qty = _coalesce_expr("e", stock_cols, ("quantidade", "qtdestoque"), "0")
            stock_deposito = _coalesce_expr("e", stock_cols, ("nome_deposito", "deposito"), "''")
            join_sql = (
                " LEFT JOIN ("
                f" SELECT {stock_pid} AS product_id, SUM(CAST({stock_qty} AS REAL)) AS quantidade,"
                f" MAX({stock_deposito}) AS deposito FROM dw_estoque e GROUP BY {stock_pid}"
                f" ) s ON s.product_id = {pid}"
            )
            qty = "COALESCE(s.quantidade, 0)"
            deposito = "COALESCE(s.deposito, '')"

        where = f"LOWER({nome}) LIKE ? OR LOWER({codigo}) LIKE ? OR LOWER({pid}) = ?"
        sql = (
            f"SELECT {pid} AS product_id, {codigo} AS codigo, {nome} AS nome,"
            f" CAST({preco} AS REAL) AS preco_venda, CAST({qty} AS REAL) AS quantidade,"
            f" {unidade} AS unidade, {deposito} AS deposito"
            f" FROM dw_produtos p{join_sql} WHERE {where} LIMIT ?"
        )
        like = f"%{term.lower()}%"
        rows = conn.execute(sql, (like, like, term, int(limit))).fetchall()
        return [_offer_from_row(row) for row in rows]

    def _search_stock(self, conn: Any, term: str, limit: int) -> list[ProductOffer]:
        stock_cols = _table_columns(conn, "dw_estoque")
        if not stock_cols:
            return []
        pid = _coalesce_expr("e", stock_cols, ("id_produto", "idproduto"), "''")
        codigo = _coalesce_expr("e", stock_cols, ("codigo_produto", "codigo"), "''")
        nome = _coalesce_expr("e", stock_cols, ("nome_produto", "nome"), "''")
        qty = _coalesce_expr("e", stock_cols, ("quantidade", "qtdestoque"), "0")
        deposito = _coalesce_expr("e", stock_cols, ("nome_deposito", "deposito"), "''")
        where = f"LOWER({nome}) LIKE ? OR LOWER({codigo}) LIKE ? OR LOWER({pid}) = ?"
        sql = (
            f"SELECT {pid} AS product_id, {codigo} AS codigo, {nome} AS nome,"
            f" 0.0 AS preco_venda, SUM(CAST({qty} AS REAL)) AS quantidade,"
            f" 'UN' AS unidade, MAX({deposito}) AS deposito"
            f" FROM dw_estoque e WHERE {where} GROUP BY {pid}, {codigo}, {nome} LIMIT ?"
        )
        like = f"%{term.lower()}%"
        rows = conn.execute(sql, (like, like, term, int(limit))).fetchall()
        return [_offer_from_row(row) for row in rows]

    def _current_stock(self, product_ids: Sequence[str]) -> dict[str, float]:
        ids = [str(product_id) for product_id in product_ids if product_id]
        if not ids:
            return {}
        conn = self.conn_factory()
        try:
            stock_cols = _table_columns(conn, "dw_estoque")
            if not stock_cols:
                return {}
            pid = _coalesce_expr("e", stock_cols, ("id_produto", "idproduto"), "''")
            qty = _coalesce_expr("e", stock_cols, ("quantidade", "qtdestoque"), "0")
            placeholders = ", ".join("?" for _ in ids)
            sql = (
                f"SELECT {pid} AS product_id, SUM(CAST({qty} AS REAL)) AS quantidade"
                f" FROM dw_estoque e WHERE {pid} IN ({placeholders}) GROUP BY {pid}"
            )
            return {
                str(row["product_id"]): _safe_float(row["quantidade"])
                for row in conn.execute(sql, ids).fetchall()
            }
        finally:
            _close_quietly(conn)


def _table_columns(conn: Any, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        try:
            from .schema import TABLES

            return set(TABLES[table].c.keys())
        except Exception:
            return set()
    names: set[str] = set()
    for row in rows:
        if isinstance(row, Mapping):
            names.add(str(row.get("name", "")))
        else:
            names.add(str(row[1]))
    return {name for name in names if name}


def _coalesce_expr(alias: str, columns: set[str], candidates: Sequence[str], fallback: str) -> str:
    present = [f"{alias}.{name}" for name in candidates if name in columns]
    if not present:
        return fallback
    if len(present) == 1:
        return f"COALESCE({present[0]}, {fallback})"
    return "COALESCE(" + ", ".join(present + [fallback]) + ")"


def _offer_from_row(row: Any) -> ProductOffer:
    data = dict(row)
    return ProductOffer(
        product_id=str(data.get("product_id") or ""),
        codigo=str(data.get("codigo") or ""),
        nome=str(data.get("nome") or ""),
        preco_venda=_safe_float(data.get("preco_venda")),
        quantidade=_safe_float(data.get("quantidade")),
        unidade=str(data.get("unidade") or "UN"),
        deposito=str(data.get("deposito") or ""),
        raw=data,
    )


def _clean_query(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _close_quietly(conn: Any) -> None:
    close = getattr(conn, "close", None)
    if callable(close):
        close()
