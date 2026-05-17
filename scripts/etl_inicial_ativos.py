#!/usr/bin/env python3
"""
ETL Inicial CISS → PostgreSQL
Carrega apenas registros ATIVOS: Produtos e Clientes.

Filtro "ativo" no Integrim:
  - Produtos: flaginativo = "F"  (F = nao inativo = ativo)
  - Clientes: flaginativo = "F"  + tipocadastro in ("F","A") = clientes ativos

Credenciais lidas de env vars (configuradas no .env da VPS):
  CISS_CIM_HOST, CISS_CIM_PORT, CISS_USERNAME, CISS_PASSWORD
  CISS_DW_DSN (DSN PostgreSQL)

Uso:
  python scripts/etl_inicial_ativos.py [--somente-produtos] [--somente-clientes] [--dry-run]
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime

# Adiciona o backend ao path para importar os modulos
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(_script_dir, "..", "dashboard", "backend")
sys.path.insert(0, _backend_dir)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("etl_inicial")


# ── Verificacao de config ──────────────────────────────────────────────────────

def verificar_config():
    """Verifica se as variaveis de ambiente estao configuradas."""
    from ciss_analytics.config import CISS_CIM_HOST, CISS_CIM_PORT, CISS_USERNAME, CISS_PASSWORD, get_ciss_dw_dsn
    erros = []
    if not CISS_USERNAME:
        erros.append("CISS_USERNAME nao definido")
    if not CISS_PASSWORD:
        erros.append("CISS_PASSWORD nao definido")
    if not CISS_CIM_HOST:
        erros.append("CISS_CIM_HOST nao definido")
    dsn = get_ciss_dw_dsn()
    if "sqlite" in dsn:
        logger.warning("AVISO: CISS_DW_DSN aponta para SQLite — use PostgreSQL em producao!")
    else:
        logger.info("DW DSN: %s", dsn.split("@")[-1] if "@" in dsn else dsn[:40])

    if erros:
        for e in erros:
            logger.error("CONFIG: %s", e)
        sys.exit(1)

    logger.info("Config OK — CIM: %s:%s | Usuario: %s", CISS_CIM_HOST, CISS_CIM_PORT, CISS_USERNAME)
    return dsn


# ── Teste de conexao ───────────────────────────────────────────────────────────

def testar_conexao_api():
    """Autentica na API e valida retorno."""
    from ciss_analytics.ciss_client import get_client
    logger.info("=== TESTE DE CONEXAO INTEGRIM ===")
    client = get_client()
    token = client._get_token()
    logger.info("Token obtido com sucesso: %s...", token[:20])
    return client


# ── ETL Produtos (apenas ativos) ───────────────────────────────────────────────

def etl_produtos(dry_run: bool = False) -> dict:
    """
    Extrai produtos ATIVOS do CISS e carrega no DW.

    Filtro Integrim: flaginativo = "F" (F = nao inativo = ativo).
    O transformer tambem filtra ativo=1 (via _flag_bool).
    """
    from ciss_analytics.ciss_client import get_client
    from ciss_analytics.transformer import transform_produtos
    from ciss_analytics.loader import carregar_produtos
    from ciss_analytics.loader import registrar_etl

    inicio = datetime.utcnow().isoformat()
    logger.info("")
    logger.info("=== ETL PRODUTOS (somente ativos) ===")

    client = get_client()

    # Clausula Integrim: flaginativo IGUAL "F" (ativo)
    clausulas_ativos = [
        {
            "campo": "flaginativo",
            "valor": "F",
            "operadorLogico": "AND",
            "operador": "IGUAL",
        }
    ]

    registros_raw = []
    paginas = 0
    inicio_ts = time.time()

    logger.info("Buscando produtos ativos via cad_produtos (flaginativo=F)...")
    for page_data in client.get_all("cad_produtos", clausulas=clausulas_ativos):
        registros_raw.extend(page_data)
        paginas += 1
        elapsed = time.time() - inicio_ts
        logger.info(
            "  Pagina %d — acumulado: %d registros brutos (%.1fs)",
            paginas, len(registros_raw), elapsed
        )

    logger.info("Extracao concluida: %d registros brutos em %d paginas", len(registros_raw), paginas)

    # Transformar — o transformer filtra ativo=1 novamente (dupla garantia)
    produtos = transform_produtos(registros_raw)
    # Filtro adicional: somente ativo=1 (garante que apenas ativos entrem)
    produtos_ativos = [p for p in produtos if p.get("ativo", 0) == 1]

    logger.info(
        "Transformacao: %d produtos ativos de %d brutos (%.1f%% retidos)",
        len(produtos_ativos), len(registros_raw),
        100 * len(produtos_ativos) / max(len(registros_raw), 1)
    )

    if dry_run:
        logger.info("[DRY RUN] Nao carregando no DW. Amostra (5 primeiros):")
        for p in produtos_ativos[:5]:
            logger.info("  Produto: id=%s nome=%s secao=%s", p.get("id_produto"), p.get("nome", "")[:50], p.get("nome_secao", "")[:30])
        return {"extraidos": len(registros_raw), "transformados": len(produtos_ativos), "carregados": 0}

    # Carregar no DW
    logger.info("Carregando %d produtos no PostgreSQL...", len(produtos_ativos))
    n_carregados = carregar_produtos(produtos_ativos)

    registrar_etl(
        tipo="etl_produtos_ativos_inicial",
        status="ok",
        iniciado_em=inicio,
        registros_ext=len(registros_raw),
        registros_ins=n_carregados,
    )

    logger.info("ETL Produtos concluido: %d inseridos/atualizados", n_carregados)
    return {"extraidos": len(registros_raw), "transformados": len(produtos_ativos), "carregados": n_carregados}


# ── ETL Clientes (apenas ativos) ───────────────────────────────────────────────

def etl_clientes(dry_run: bool = False) -> dict:
    """
    Extrai clientes ATIVOS do CISS e carrega no DW.

    Filtro Integrim: flaginativo = "F" (ativo).
    Transformer filtra tipocadastro in (F, A) = clientes (nao fornecedores).
    """
    from ciss_analytics.ciss_client import get_client
    from ciss_analytics.transformer import transform_clientes
    from ciss_analytics.loader import carregar_clientes, registrar_etl

    inicio = datetime.utcnow().isoformat()
    logger.info("")
    logger.info("=== ETL CLIENTES (somente ativos) ===")

    client = get_client()

    # Clausula Integrim: flaginativo IGUAL "F" (ativo)
    clausulas_ativos = [
        {
            "campo": "flaginativo",
            "valor": "F",
            "operadorLogico": "AND",
            "operador": "IGUAL",
        }
    ]

    registros_raw = []
    paginas = 0
    inicio_ts = time.time()

    logger.info("Buscando clientes ativos via cad_pessoas (flaginativo=F)...")
    for page_data in client.get_all("cad_pessoas", clausulas=clausulas_ativos):
        registros_raw.extend(page_data)
        paginas += 1
        elapsed = time.time() - inicio_ts
        logger.info(
            "  Pagina %d — acumulado: %d registros brutos (%.1fs)",
            paginas, len(registros_raw), elapsed
        )

    logger.info("Extracao concluida: %d registros brutos em %d paginas", len(registros_raw), paginas)

    # Transformar — filtra tipocadastro="F"/"A" (clientes) + converte ativo
    clientes = transform_clientes(registros_raw)
    # Filtro adicional: somente ativo=1
    clientes_ativos = [c for c in clientes if c.get("ativo", 0) == 1]

    logger.info(
        "Transformacao: %d clientes ativos de %d brutos",
        len(clientes_ativos), len(registros_raw)
    )

    if dry_run:
        logger.info("[DRY RUN] Nao carregando no DW. Amostra (5 primeiros):")
        for c in clientes_ativos[:5]:
            logger.info("  Cliente: id=%s nome=%s cidade=%s", c.get("id_cliente"), c.get("nome", "")[:50], c.get("cidade", ""))
        return {"extraidos": len(registros_raw), "transformados": len(clientes_ativos), "carregados": 0}

    # Carregar no DW
    logger.info("Carregando %d clientes no PostgreSQL...", len(clientes_ativos))
    n_carregados = carregar_clientes(clientes_ativos)

    registrar_etl(
        tipo="etl_clientes_ativos_inicial",
        status="ok",
        iniciado_em=inicio,
        registros_ext=len(registros_raw),
        registros_ins=n_carregados,
    )

    logger.info("ETL Clientes concluido: %d inseridos/atualizados", n_carregados)
    return {"extraidos": len(registros_raw), "transformados": len(clientes_ativos), "carregados": n_carregados}


# ── Verificacao DW ─────────────────────────────────────────────────────────────

def verificar_dw() -> None:
    """Inicializa o schema do DW e reporta status."""
    from ciss_analytics.schema import init_dw, get_dw_engine
    from ciss_analytics.config import get_ciss_dw_dsn

    dsn = get_ciss_dw_dsn()
    logger.info("")
    logger.info("=== INICIALIZANDO DATA WAREHOUSE ===")
    logger.info("DSN: %s", dsn.split("@")[-1] if "@" in dsn else dsn[:60])

    conn = init_dw()
    engine = get_dw_engine()
    logger.info("DW inicializado com dialeto: %s", engine.dialect.name)

    # Verifica tabelas
    result = conn.execute("SELECT COUNT(*) FROM dw_produtos")
    row = result.fetchone()
    prod_count = row[0] if row else 0

    result2 = conn.execute("SELECT COUNT(*) FROM dw_clientes")
    row2 = result2.fetchone()
    cli_count = row2[0] if row2 else 0

    conn.close()
    logger.info("Estado atual do DW: produtos=%d | clientes=%d", prod_count, cli_count)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETL Inicial CISS → PostgreSQL (apenas ativos)")
    parser.add_argument("--somente-produtos", action="store_true", help="Roda apenas ETL de produtos")
    parser.add_argument("--somente-clientes", action="store_true", help="Roda apenas ETL de clientes")
    parser.add_argument("--dry-run", action="store_true", help="Exibe dados sem gravar no DW")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  ETL INICIAL CISS → PostgreSQL (Ativos)")
    logger.info("  Integrim: vlxrr.dyndns.org:3046")
    logger.info("=" * 60)

    # 1. Verificar configuracao
    dsn = verificar_config()

    # 2. Testar conexao com a API
    testar_conexao_api()

    # 3. Inicializar schema do DW
    verificar_dw()

    resultados = {}

    # 4. ETL conforme argumentos
    fazer_produtos = not args.somente_clientes
    fazer_clientes = not args.somente_produtos

    inicio_total = time.time()

    if fazer_produtos:
        try:
            resultados["produtos"] = etl_produtos(dry_run=args.dry_run)
        except Exception as exc:
            logger.error("ERRO ETL Produtos: %s", exc, exc_info=True)
            resultados["produtos"] = {"erro": str(exc)}

    if fazer_clientes:
        try:
            resultados["clientes"] = etl_clientes(dry_run=args.dry_run)
        except Exception as exc:
            logger.error("ERRO ETL Clientes: %s", exc, exc_info=True)
            resultados["clientes"] = {"erro": str(exc)}

    elapsed = time.time() - inicio_total

    # 5. Verificar estado final do DW
    if not args.dry_run:
        verificar_dw()

    # 6. Resumo
    logger.info("")
    logger.info("=" * 60)
    logger.info("  RESUMO DO ETL (%.1fs)", elapsed)
    logger.info("=" * 60)
    for entidade, res in resultados.items():
        if "erro" in res:
            logger.error("  %s: ERRO — %s", entidade.upper(), res["erro"])
        else:
            logger.info(
                "  %s: extraidos=%d | ativos=%d | carregados=%d",
                entidade.upper(),
                res.get("extraidos", 0),
                res.get("transformados", 0),
                res.get("carregados", 0),
            )
    logger.info("=" * 60)

    # Exit code 1 se houve erro
    if any("erro" in r for r in resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
