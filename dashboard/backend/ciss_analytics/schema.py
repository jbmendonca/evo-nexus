"""
Schema do Data Warehouse CISS.

O DW usa SQLAlchemy Core para suportar PostgreSQL em producao e manter
SQLite como fallback local/testes.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    func,
    inspect,
)
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool
from sqlalchemy.schema import CreateColumn

from .config import (
    CISS_DW_POOL_MAX_OVERFLOW,
    CISS_DW_POOL_SIZE,
    CISS_DW_POOL_TIMEOUT,
    CISS_MODELS_PATH,
    get_ciss_dw_dsn,
)

logger = logging.getLogger(__name__)

metadata = MetaData()

dw_vendas = Table(
    "dw_vendas",
    metadata,
    Column("id_venda", String, primary_key=True),
    Column("numero_nf", String),
    Column("data_venda", String, nullable=False),
    Column("hora_venda", String),
    Column("id_cliente", String),
    Column("nome_cliente", String),
    Column("id_vendedor", String),
    Column("nome_vendedor", String),
    Column("id_empresa", String),
    Column("num_itens", Integer),
    Column("total_bruto", Float, nullable=False, server_default="0"),
    Column("total_desconto", Float, nullable=False, server_default="0"),
    Column("total_liquido", Float, nullable=False, server_default="0"),
    Column("status", String),
    Column("tipo_pagamento", String),
    Column("canal_venda", String),
    Column("atualizado_em", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_itens_venda = Table(
    "dw_itens_venda",
    metadata,
    Column("id_item", String, primary_key=True),
    Column("id_venda", String, nullable=False, index=True),
    Column("id_cliente", String, index=True),
    Column("id_produto", String, nullable=False, index=True),
    Column("codigo_produto", String),
    Column("nome_produto", String),
    Column("id_vendedor", String),
    Column("data_venda", String),
    Column("quantidade", Float, nullable=False, server_default="0"),
    Column("preco_unitario", Float, nullable=False, server_default="0"),
    Column("desconto_item", Float, nullable=False, server_default="0"),
    Column("desconto_pct", Float),
    Column("desconto_val", Float),
    Column("total_item", Float, nullable=False, server_default="0"),
    Column("val_lucro", Float),
    Column("per_margem", Float),
    Column("observacao", Text),
    Column("id_empresa", String),
    Column("id_categoria", String),
    Column("nome_categoria", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_clientes = Table(
    "dw_clientes",
    metadata,
    Column("id_cliente", String, primary_key=True),
    Column("nome", String, nullable=False),
    Column("nome_fantasia", String),
    Column("documento", String),
    Column("tipo_pessoa", String),
    Column("tipo_cadastro", String),
    Column("cpf_cnpj", String),
    Column("email", String),
    Column("telefone", String),
    Column("cidade", String),
    Column("estado", String),
    Column("bairro", String),
    Column("endereco", String),
    Column("cep", String),
    Column("atividade", String),
    Column("situacao_financ", String),
    Column("limite_credito", Float),
    Column("segmento", String),
    Column("data_cadastro", String),
    Column("data_atualizacao", String),
    Column("ativo", Integer, nullable=False, server_default="1"),
    Column("rfm_recencia", Integer),
    Column("rfm_frequencia", Integer),
    Column("rfm_monetario", Float),
    Column("rfm_segmento", String, index=True),
    Column("rfm_atualizado", String),
    Column("atualizado_em", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_produtos = Table(
    "dw_produtos",
    metadata,
    Column("id_produto", String, primary_key=True),
    Column("codigo", String),
    Column("codigo_produto", String),
    Column("nome", String, nullable=False),
    Column("nome_produto", String),
    Column("descricao", Text),
    Column("id_categoria", String, index=True),
    Column("nome_categoria", String),
    Column("nome_secao", String),
    Column("nome_subcategoria", String),
    Column("marca", String),
    Column("referencia", String),
    Column("ncm", String),
    Column("preco_custo", Float),
    Column("preco_venda", Float),
    Column("unidade", String),
    Column("unidade_entrada", String),
    Column("unidade_saida", String),
    Column("peso_liquido", Float),
    Column("peso_bruto", Float),
    Column("bloqueado_venda", Integer),
    Column("data_cadastro", String),
    Column("data_atualizacao", String),
    Column("id_secao", String),
    Column("id_grupo", String),
    Column("id_subgrupo", String),
    Column("ativo", Integer, nullable=False, server_default="1"),
    Column("atualizado_em", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_estoque = Table(
    "dw_estoque",
    metadata,
    Column("id_estoque", String, primary_key=True),
    Column("id_produto", String, nullable=False, index=True),
    Column("codigo_produto", String),
    Column("nome_produto", String),
    Column("id_deposito", String),
    Column("nome_deposito", String),
    Column("quantidade", Float, nullable=False, server_default="0"),
    Column("estoque_minimo", Float, nullable=False, server_default="0"),
    Column("estoque_maximo", Float),
    Column("consumo_medio_dia", Float),
    Column("dias_cobertura", Float),
    Column("nivel_alerta", String, index=True),
    Column("data_ruptura_prev", String),
    Column("ml_atualizado", String),
    Column("atualizado_em", String, nullable=False),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_etl_log = Table(
    "dw_etl_log",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("tipo", String, nullable=False),
    Column("iniciado_em", String, nullable=False),
    Column("concluido_em", String),
    Column("status", String, nullable=False, server_default="running"),
    Column("registros_ext", Integer, nullable=False, server_default="0"),
    Column("registros_ins", Integer, nullable=False, server_default="0"),
    Column("ultima_data_ref", String),
    Column("erro_msg", Text),
)

dw_insights_cache = Table(
    "dw_insights_cache",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("tipo", String, nullable=False, unique=True),
    Column("payload", Text, nullable=False),
    Column("gerado_em", String, nullable=False),
    Column("valido_ate", String, nullable=False),
)

dw_kpis_diarios = Table(
    "dw_kpis_diarios",
    metadata,
    Column("data_ref", String, primary_key=True),
    Column("total_vendas", Float, nullable=False, server_default="0"),
    Column("total_transacoes", Integer, nullable=False, server_default="0"),
    Column("ticket_medio", Float, nullable=False, server_default="0"),
    Column("novos_clientes", Integer, nullable=False, server_default="0"),
    Column("itens_vendidos", Integer, nullable=False, server_default="0"),
    Column("calculado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_cliente_metricas = Table(
    "dw_cliente_metricas",
    metadata,
    Column("id_cliente", String, primary_key=True),
    Column("nome_cliente", String),
    Column("cidade", String),
    Column("estado", String),
    Column("segmento_rfm", String, index=True),
    Column("classificacao_abc", String, index=True),
    Column("num_compras", Integer, nullable=False, server_default="0"),
    Column("itens_comprados", Float, nullable=False, server_default="0"),
    Column("valor_total", Float, nullable=False, server_default="0"),
    Column("ticket_medio", Float, nullable=False, server_default="0"),
    Column("skus_distintos", Integer, nullable=False, server_default="0"),
    Column("primeira_compra", String),
    Column("ultima_compra", String),
    Column("dias_desde_ultima", Integer),
    Column("atualizado_em", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_cliente_produto_metricas = Table(
    "dw_cliente_produto_metricas",
    metadata,
    Column("id_cliente", String, primary_key=True),
    Column("id_produto", String, primary_key=True),
    Column("nome_produto", String),
    Column("categoria", String),
    Column("quantidade_total", Float, nullable=False, server_default="0"),
    Column("valor_total", Float, nullable=False, server_default="0"),
    Column("num_compras", Integer, nullable=False, server_default="0"),
    Column("preco_medio", Float),
    Column("ultima_compra", String),
    Column("rank_valor", Integer),
    Column("rank_quantidade", Integer),
    Column("participacao_valor_cliente", Float),
    Column("participacao_qtd_cliente", Float),
    Column("atualizado_em", String),
    Column("importado_em", String, nullable=False, server_default=func.current_timestamp()),
)

dw_data_agent_memory = Table(
    "dw_data_agent_memory",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("question", Text, nullable=False),
    Column("normalized_question", Text),
    Column("topic", String, index=True),
    Column("answer", Text, nullable=False),
    Column("confidence", Float, nullable=False, server_default="0"),
    Column("created_at", String, nullable=False, server_default=func.current_timestamp()),
)

TABLES: dict[str, Table] = {table.name: table for table in metadata.sorted_tables}

_engine: Engine | None = None
_engine_dsn: str | None = None
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _Row(Mapping[str, Any]):
    """Row compativel com sqlite3.Row e com dict(row)."""

    def __init__(self, values: Sequence[Any], keys: Sequence[str]) -> None:
        self._values = tuple(values)
        self._keys = tuple(keys)
        self._by_key = dict(zip(self._keys, self._values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._by_key[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self):
        return self._keys


class _Result:
    def __init__(self, result: Any) -> None:
        self._result = result

    def _wrap(self, row: Any) -> _Row | None:
        if row is None:
            return None
        return _Row(tuple(row), tuple(row._mapping.keys()))

    def fetchone(self) -> _Row | None:
        return self._wrap(self._result.fetchone())

    def fetchall(self) -> list[_Row]:
        return [self._wrap(row) for row in self._result.fetchall()]


class DwConnection:
    """Pequeno adapter para preservar a API sqlite usada pelo modulo CISS."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.dialect_name = connection.dialect.name
        self._closed = False

    def execute(self, statement: Any, parameters: Any = None) -> _Result:
        if isinstance(statement, str):
            sql, params = self._prepare_driver_sql(statement, parameters)
            result = self._connection.exec_driver_sql(sql, params)
        else:
            result = self._connection.execute(statement, parameters or {})
        return _Result(result)

    def executemany(self, statement: str, seq_of_parameters: Iterable[Any]) -> _Result:
        sql, params = self._prepare_driver_sql(statement, list(seq_of_parameters))
        return _Result(self._connection.exec_driver_sql(sql, params))

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "DwConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rollback()
        self.close()

    def raw_connection(self) -> Connection:
        return self._connection

    def _prepare_driver_sql(self, statement: str, parameters: Any) -> tuple[str, Any]:
        sql = statement
        params = parameters if parameters is not None else ()
        if self.dialect_name == "postgresql":
            sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP")
            if "?" in sql:
                sql = sql.replace("?", "%s")
        return sql, params


def _is_postgres_dsn(dsn: str) -> bool:
    return make_url(dsn).get_backend_name().startswith("postgresql")


def _is_sqlite_dsn(dsn: str) -> bool:
    return make_url(dsn).get_backend_name() == "sqlite"


def _create_engine(dsn: str) -> Engine:
    if _is_postgres_dsn(dsn):
        return create_engine(
            dsn,
            poolclass=QueuePool,
            pool_size=CISS_DW_POOL_SIZE,
            max_overflow=CISS_DW_POOL_MAX_OVERFLOW,
            pool_timeout=CISS_DW_POOL_TIMEOUT,
            pool_pre_ping=True,
            future=True,
        )

    engine = create_engine(dsn, future=True)
    if _is_sqlite_dsn(dsn):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA cache_size=-32000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

    return engine


def get_dw_engine() -> Engine:
    """Retorna engine cacheada, recriando-a se o DSN mudou."""
    global _engine, _engine_dsn
    dsn = get_ciss_dw_dsn()
    if _engine is None or _engine_dsn != dsn:
        if _engine is not None:
            _engine.dispose()
        _engine = _create_engine(dsn)
        _engine_dsn = dsn
    return _engine


def dispose_dw_engine() -> None:
    """Fecha o pool atual. Usado por testes e reloads de configuracao."""
    global _engine, _engine_dsn
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_dsn = None


def init_dw() -> DwConnection:
    """Inicializa o DW e retorna uma conexao aberta."""
    dsn = get_ciss_dw_dsn()
    if _is_sqlite_dsn(dsn):
        database = make_url(dsn).database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)

    Path(CISS_MODELS_PATH).mkdir(parents=True, exist_ok=True)

    engine = get_dw_engine()
    metadata.create_all(engine)
    _ensure_missing_columns(engine)
    conn = engine.connect()
    logger.info("DW CISS inicializado com dialeto: %s", engine.dialect.name)
    return DwConnection(conn)


def get_dw_conn() -> DwConnection:
    """Abre uma nova conexao com o DW, inicializando o schema se necessario."""
    engine = get_dw_engine()
    try:
        metadata.create_all(engine)
        _ensure_missing_columns(engine)
        return DwConnection(engine.connect())
    except SQLAlchemyError:
        logger.exception("Erro abrindo conexao com DW CISS")
        raise


def _ensure_missing_columns(engine: Engine) -> None:
    """Adiciona colunas novas em tabelas existentes sem recriar dados."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    if not existing_tables:
        return

    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns or column.primary_key:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {CreateColumn(column).compile(dialect=engine.dialect)}"
                logger.info("DW CISS adicionando coluna ausente: %s.%s", table.name, column.name)
                conn.exec_driver_sql(ddl)


def build_readonly_user_sql(
    username: str | None = None,
    password: str | None = None,
    database: str | None = None,
) -> list[str]:
    """
    Gera SQL opcional para criar usuario read-only no PostgreSQL.

    Os identificadores sao validados para evitar SQL injection. A senha e
    tratada como literal SQL e deve vir de segredo/ambiente.
    """
    from .config import CISS_DW_READONLY_PASSWORD, CISS_DW_READONLY_USER

    user = username or CISS_DW_READONLY_USER
    pwd = password or CISS_DW_READONLY_PASSWORD
    db_name = database or make_url(get_ciss_dw_dsn()).database
    if not user or not pwd:
        return []
    for identifier in (user, db_name):
        if not identifier or not _IDENTIFIER_RE.match(identifier):
            raise ValueError(f"Identificador PostgreSQL invalido: {identifier!r}")
    escaped_password = pwd.replace("'", "''")
    return [
        f"CREATE USER {user} WITH PASSWORD '{escaped_password}';",
        f"GRANT CONNECT ON DATABASE {db_name} TO {user};",
        f"GRANT USAGE ON SCHEMA public TO {user};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {user};",
    ]


__all__ = [
    "DwConnection",
    "TABLES",
    "metadata",
    "init_dw",
    "get_dw_conn",
    "get_dw_engine",
    "dispose_dw_engine",
    "build_readonly_user_sql",
]
