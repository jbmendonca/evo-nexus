"""
Extração de dados do CISSPoder via API Integrim.

Nomes de serviços conforme manual Integrim:
  - cad_pessoas      → clientes/fornecedores
  - get_pedido_venda_prod → pedidos/vendas + itens
  - cad_produto      → cadastro de produtos
  - get_estoque      → posição de estoque

Filtros seguem o padrão de cláusulas JSON do Integrim:
  {"campo": "...", "valor": "...", "operadorLogico": "AND", "operador": "IGUAL"}

Operadores disponíveis: IGUAL, IGUAL_NULL, EQUAL, MENOR, MAIOR, LIKE,
                        MENOR_IGUAL, MAIOR_IGUAL
"""
import logging
from datetime import date, timedelta
from typing import Any

from .ciss_client import get_client
from .config import ML_HISTORY_DAYS

logger = logging.getLogger(__name__)


def _clausula(campo: str, valor: Any, operador: str = "IGUAL",
               logico: str = "AND") -> dict:
    """Monta uma cláusula de filtro no formato Integrim."""
    return {
        "campo": campo,
        "valor": str(valor),
        "operadorLogico": logico,
        "operador": operador,
    }


def _data_corte(dias: int) -> str:
    """Retorna data de corte no formato YYYY-MM-DD."""
    return (date.today() - timedelta(days=dias)).isoformat()


# ── Clientes / Pessoas ────────────────────────────────────────────────────────

def extrair_clientes(incremental: bool = False) -> list[dict]:
    """
    Extrai cadastro de pessoas (clientes) via serviço cad_pessoas.

    O serviço cad_pessoas retorna clientes, fornecedores e transportadores.
    Filtramos por tipo de pessoa para pegar apenas clientes.
    """
    client = get_client()
    clausulas = []

    # Filtro incremental por data de alteração (se a API suportar)
    if incremental:
        dt = _data_corte(2)   # últimas 48h como margem
        clausulas.append(_clausula("dtalteracao", dt, "MAIOR_IGUAL"))

    logger.info("Extraindo clientes (cad_pessoas) — incremental=%s", incremental)

    registros: list[dict] = []
    for page in client.get_all("cad_pessoas", clausulas=clausulas or None):
        registros.extend(page)

    logger.info("cad_pessoas: %d registros extraídos", len(registros))
    return registros


# ── Pedidos de Venda (Cabeçalho + Itens) ─────────────────────────────────────

def extrair_pedidos_venda(dias: int | None = None) -> list[dict]:
    """
    Extrai pedidos de venda via servi??o get_pedido_venda_prod.

    O servi??o retorna cabe??alho e itens juntos conforme o manual.
    Filtra por data para extra????o incremental com fallback de campo.
    """
    client = get_client()

    periodo = dias or ML_HISTORY_DAYS
    dt_inicio = _data_corte(periodo)
    logger.info("Extraindo pedidos de venda ??? desde %s", dt_inicio)

    tentativas: list[tuple[str, list[dict] | None]] = [
        ("dtalteracao", [_clausula("dtalteracao", dt_inicio, "MAIOR_IGUAL")]),
        ("dtemissao", [_clausula("dtemissao", dt_inicio, "MAIOR_IGUAL")]),
        ("sem_filtro", None),
    ]
    last_exc: Exception | None = None

    for nome_tentativa, clausulas in tentativas:
        try:
            registros: list[dict] = []
            for page in client.get_all("get_pedido_venda_prod", clausulas=clausulas):
                registros.extend(page)
            logger.info(
                "get_pedido_venda_prod: %d registros extra??dos (tentativa=%s)",
                len(registros),
                nome_tentativa,
            )
            return registros
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Falha na extra????o get_pedido_venda_prod com tentativa=%s: %s",
                nome_tentativa,
                exc,
            )

    if last_exc:
        raise last_exc
    return []


def extrair_pedidos_venda_incremental() -> list[dict]:
    """Extração incremental — últimas 48h (para sync a cada 30min)."""
    return extrair_pedidos_venda(dias=2)


# ── Produtos ──────────────────────────────────────────────────────────────────

def extrair_produtos(incremental: bool = False) -> list[dict]:
    """
    Extrai cadastro de produtos via serviço cad_produtos (plural).
    Confirmado via API: 65.556 produtos disponíveis.

    Campos reais: idproduto, descrcomproduto, descrsecao, descrgrupo,
                  descrsubgrupo, flaginativo, dtalteracao, dtcadastro, ncm, etc.
    """
    client = get_client()
    clausulas = []

    if incremental:
        dt = _data_corte(2)
        clausulas.append(_clausula("dtalteracao", dt, "MAIOR_IGUAL"))

    logger.info("Extraindo produtos (cad_produtos) — incremental=%s", incremental)

    registros: list[dict] = []
    for page in client.get_all("cad_produtos", clausulas=clausulas or None):
        registros.extend(page)

    logger.info("cad_produtos: %d registros extraídos", len(registros))
    return registros


# ── Estoque ───────────────────────────────────────────────────────────────────

def extrair_estoque(deposito: str | None = None) -> list[dict]:
    """
    Tenta extrair posição de estoque do CIM.

    NOTA: O serviço de estoque (get_estoque e variantes) retorna 404 neste CIM.
    Provavelmente precisa ser habilitado no painel Integrim Admin.

    Enquanto não disponível, o sistema usa:
      - cad_produtos para referência de produtos cadastrados
      - get_pedido_venda_prod para calcular consumo e projetar estoque

    Para habilitar: acesse http://vlxrr.dyndns.org:3046/integrim-admin/
    → Serviços por Fornecedor → selecione 'integrim' → inclua o serviço de estoque.
    """
    client = get_client()

    # Candidatos de nome do serviço a tentar
    candidatos = [
        "get_estoque", "estoque_produto", "get_saldo_produto",
        "get_posicao_estoque", "get_saldo_deposito",
    ]

    for svc in candidatos:
        try:
            clausulas = []
            if deposito:
                clausulas.append(_clausula("iddeposito", deposito, "IGUAL"))

            registros: list[dict] = []
            for page in client.get_all(svc, clausulas=clausulas or None, max_pages=2):
                registros.extend(page)
                break  # Testa apenas primeira página para ver se existe

            if registros:
                logger.info("Estoque encontrado no serviço '%s': %d registros", svc, len(registros))
                # Buscar todas as páginas
                for page in client.get_all(svc, clausulas=clausulas or None):
                    registros.extend(page)
                return registros

        except Exception as exc:
            logger.debug("Serviço '%s' indisponível: %s", svc, exc)

    logger.warning(
        "Nenhum serviço de estoque disponível no CIM. "
        "Habilite em http://vlxrr.dyndns.org:3046/integrim-admin/"
    )
    return []


# ── Consulta Pontual ──────────────────────────────────────────────────────────

def buscar_pedido_por_id(id_pedido: str) -> dict | None:
    """Busca um pedido específico pelo ID."""
    client = get_client()
    clausulas = [_clausula("idpedido", id_pedido, "IGUAL")]
    data = client.post_service("get_pedido_venda_prod", page=1, clausulas=clausulas)
    registros = client._extrair_registros(data, "get_pedido_venda_prod")
    return registros[0] if registros else None


def buscar_produto_por_codigo(codigo: str) -> dict | None:
    """Busca um produto pelo código."""
    client = get_client()
    clausulas = [_clausula("codproduto", codigo, "IGUAL")]
    data = client.post_service("cad_produto", page=1, clausulas=clausulas)
    registros = client._extrair_registros(data, "cad_produto")
    return registros[0] if registros else None


def buscar_cliente_por_id(id_cliente: str) -> dict | None:
    """Busca um cliente/pessoa pelo ID."""
    client = get_client()
    clausulas = [_clausula("idclifor", id_cliente, "IGUAL")]
    data = client.post_service("cad_pessoas", page=1, clausulas=clausulas)
    registros = client._extrair_registros(data, "cad_pessoas")
    return registros[0] if registros else None


# ── Health Check ──────────────────────────────────────────────────────────────

def testar_conexao() -> dict:
    """
    Testa conectividade com o CIM e autenticação Integrim.
    Retorna dict com status e mensagem.
    """
    client = get_client()
    try:
        ok = client.health_check()
        if ok:
            return {"ok": True, "mensagem": "Conexão com CISSPoder estabelecida com sucesso"}
        return {"ok": False, "mensagem": "Falha na autenticação com CISSPoder"}
    except PermissionError as exc:
        return {"ok": False, "mensagem": str(exc)}
    except Exception as exc:
        return {"ok": False, "mensagem": f"Erro de conectividade: {exc}"}
