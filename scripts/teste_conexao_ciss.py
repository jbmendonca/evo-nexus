#!/usr/bin/env python3
"""Teste rapido de conexao com API Integrim e PostgreSQL DW."""
import sys, os
sys.path.insert(0, "dashboard/backend")

try:
    from ciss_analytics.ciss_client import get_client
    from ciss_analytics.schema import init_dw, get_dw_engine
    from ciss_analytics.config import get_ciss_dw_dsn, CISS_CIM_HOST, CISS_CIM_PORT, CISS_USERNAME

    print(f"CIM: {CISS_CIM_HOST}:{CISS_CIM_PORT} | User: {CISS_USERNAME}")
    print(f"DSN: {get_ciss_dw_dsn()}")

    print("Testando API Integrim...")
    client = get_client()
    ok = client.health_check()
    print(f"API Integrim: {'OK' if ok else 'FALHOU'}")

    print("Testando PostgreSQL DW...")
    conn = init_dw()
    engine = get_dw_engine()
    result = conn.execute("SELECT COUNT(*) FROM dw_produtos")
    row = result.fetchone()
    print(f"PostgreSQL OK | dw_produtos: {row[0] if row else 0} registros")
    conn.close()
    print("TESTE_OK")
except Exception as e:
    print(f"ERRO: {e}", file=sys.stderr)
    import traceback; traceback.print_exc()
    sys.exit(1)
