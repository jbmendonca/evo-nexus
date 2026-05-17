"""
Scheduler do ETL CISS — executa pipelines em background via threads.

Roda como daemon thread dentro do processo Flask do EvoNexus,
seguindo o padrão já estabelecido em heartbeat_dispatcher.py.
"""
import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_scheduler_started = False
_lock = threading.Lock()


def _run_etl_vendas() -> None:
    """Executa pipeline ETL completo de vendas + itens."""
    from .extractor import extrair_pedidos_venda
    from .transformer import transform_itens_venda, transform_vendas_do_itens
    from .loader import (
        carregar_vendas,
        carregar_itens_venda,
        materializar_kpis_diarios,
        materializar_metricas_clientes,
        registrar_etl,
    )
    from .name_enrichment import enrich_customer_links_from_ciss, enrich_dw_names
    from datetime import datetime as dt

    inicio = dt.utcnow().isoformat()
    logger.info("[ETL] Iniciando pipeline de vendas...")
    try:
        raw = extrair_pedidos_venda()
        itens = transform_itens_venda(raw)
        n_i = carregar_itens_venda(itens)

        vendas = transform_vendas_do_itens(itens)
        n_v = carregar_vendas(vendas)
        data_inicio = min(
            (str(item.get("data_venda") or "")[:10] for item in itens if item.get("data_venda")),
            default="",
        )
        customer_links = enrich_customer_links_from_ciss(data_inicio=data_inicio or None)
        enrichment = enrich_dw_names(fetch_sellers=True, seller_limit=5000)

        materializar_kpis_diarios()
        metricas_clientes = materializar_metricas_clientes()

        # Regenerar insights no cache
        try:
            from .ml.insight_engine import gerar_insights
            gerar_insights()
        except Exception as exc:
            logger.warning("[ETL] Insights não gerados: %s", exc)

        registrar_etl(
            tipo="vendas",
            status="success",
            iniciado_em=inicio,
            registros_ext=len(raw),
            registros_ins=n_v,
            ultima_data_ref=datetime.now(timezone.utc).date().isoformat(),
        )
        logger.info(
            "[ETL] Pipeline vendas OK: %d vendas, %d itens, clientes=%s, customer_links=%s, enrichment=%s",
            n_v,
            n_i,
            metricas_clientes,
            customer_links,
            enrichment,
        )
    except Exception as exc:
        registrar_etl(tipo="vendas", status="error", iniciado_em=inicio, erro_msg=str(exc))
        logger.error("[ETL] Pipeline vendas ERRO: %s", exc, exc_info=True)


def _run_etl_estoque() -> None:
    """Executa pipeline ETL de estoque."""
    from .extractor import extrair_estoque
    from .transformer import transform_estoque
    from .loader import carregar_estoque, registrar_etl
    from .ml.stock_alert import gerar_alertas_estoque
    from datetime import datetime as dt

    inicio = dt.utcnow().isoformat()
    logger.info("[ETL] Iniciando pipeline de estoque...")
    try:
        raw = extrair_estoque()
        estoque = transform_estoque(raw)
        n = carregar_estoque(estoque)
        gerar_alertas_estoque()

        registrar_etl(
            tipo="estoque",
            status="success",
            iniciado_em=inicio,
            registros_ext=len(raw),
            registros_ins=n,
        )
        logger.info("[ETL] Pipeline estoque OK: %d registros", n)
    except Exception as exc:
        registrar_etl(tipo="estoque", status="error", iniciado_em=inicio, erro_msg=str(exc))
        logger.error("[ETL] Pipeline estoque ERRO: %s", exc, exc_info=True)


def _run_etl_full() -> None:
    """ETL completo (vendas + clientes + produtos + estoque)."""
    from .extractor import extrair_clientes, extrair_produtos
    from .transformer import transform_clientes, transform_produtos
    from .loader import carregar_clientes, carregar_produtos, materializar_metricas_clientes, registrar_etl
    from datetime import datetime as dt

    logger.info("[ETL] Iniciando ETL completo (02h UTC)...")
    _run_etl_vendas()
    # _run_etl_estoque() # Temporariamente desativado pois a API retorna 404

    inicio = dt.utcnow().isoformat()
    try:
        raw_c = extrair_clientes()
        clientes = transform_clientes(raw_c)
        carregar_clientes(clientes)

        raw_p = extrair_produtos()
        produtos = transform_produtos(raw_p)
        carregar_produtos(produtos)

        # Retreinar segmentação
        from .ml.customer_segmentation import segmentar_clientes
        segmentar_clientes()
        materializar_metricas_clientes()

        # Detectar anomalias
        from .ml.anomaly_detector import detectar_anomalias
        detectar_anomalias()

        registrar_etl(
            tipo="full",
            status="success",
            iniciado_em=inicio,
            registros_ext=len(raw_c) + len(raw_p),
            registros_ins=len(clientes) + len(produtos),
        )
        logger.info("[ETL] ETL completo finalizado com sucesso")
    except Exception as exc:
        registrar_etl(tipo="full", status="error", iniciado_em=inicio, erro_msg=str(exc))
        logger.error("[ETL] ETL completo ERRO: %s", exc, exc_info=True)


def _scheduler_loop() -> None:
    """
    Loop principal do scheduler.
    Verifica a cada minuto qual pipeline deve ser executado.
    """
    from .config import ETL_FULL_HOUR_UTC, ETL_STOCK_INTERVAL_MIN, ETL_SALES_INTERVAL_MIN

    last_full_day: str | None = None
    last_stock_min: int = -1
    last_sales_min: int = -1

    logger.info("[Scheduler CISS] Iniciado — ETL completo às %02d:00 UTC", ETL_FULL_HOUR_UTC)

    while True:
        try:
            now = datetime.now(timezone.utc)
            current_min = now.hour * 60 + now.minute
            today = now.date().isoformat()

            # ETL completo diário
            if now.hour == ETL_FULL_HOUR_UTC and now.minute < 5 and last_full_day != today:
                last_full_day = today
                t = threading.Thread(target=_run_etl_full, daemon=True, name="ciss-etl-full")
                t.start()

            # ETL de estoque (intervalo configurável)
            elif current_min % ETL_STOCK_INTERVAL_MIN == 0 and current_min != last_stock_min:
                last_stock_min = current_min
                t = threading.Thread(target=_run_etl_estoque, daemon=True, name="ciss-etl-stock")
                t.start()

            # ETL de vendas (intervalo configurável)
            elif current_min % ETL_SALES_INTERVAL_MIN == 0 and current_min != last_sales_min:
                last_sales_min = current_min
                t = threading.Thread(target=_run_etl_vendas, daemon=True, name="ciss-etl-vendas")
                t.start()

        except Exception as exc:
            logger.error("[Scheduler CISS] Erro no loop: %s", exc, exc_info=True)

        time.sleep(60)


def start_ciss_scheduler() -> None:
    """
    Inicia o scheduler CISS como daemon thread.
    Idempotente — ignora chamadas duplicadas.
    """
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    # Inicializar DW na primeira execução
    try:
        from .schema import init_dw
        init_dw()
    except Exception as exc:
        logger.warning("[Scheduler CISS] Erro ao inicializar DW: %s", exc)

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="ciss-scheduler")
    t.start()
    logger.info("[Scheduler CISS] Thread iniciada")
