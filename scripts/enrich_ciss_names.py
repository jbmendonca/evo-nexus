#!/usr/bin/env python3
"""Enriquece nomes de produtos e vendedores no DW CISS."""

from __future__ import annotations

import argparse
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.join(_script_dir, "..", "dashboard", "backend")
sys.path.insert(0, _backend_dir)

from ciss_analytics.name_enrichment import enrich_dw_names  # noqa: E402
from ciss_analytics.schema import init_dw  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Enriquece nomes no DW CISS")
    parser.add_argument("--no-sellers", action="store_true", help="Nao consultar cad_pessoas para vendedores")
    parser.add_argument("--seller-limit", type=int, default=1000, help="Maximo de IDs de vendedores por execucao")
    args = parser.parse_args()

    init_dw().close()
    result = enrich_dw_names(fetch_sellers=not args.no_sellers, seller_limit=args.seller_limit)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

