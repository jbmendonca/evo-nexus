"""
Configurações do módulo CISS Analytics — Integrim/CISSPoder.

Lê variáveis de ambiente. As credenciais ficam no .env da VPS,
NUNCA hardcoded no código-fonte.
"""
import os
from pathlib import Path
from sqlalchemy.engine import make_url

# ── CIM (CISS Integration Module) ────────────────────────────────────────────
# Endereço e porta do CIM fornecidos pelo cliente
CISS_CIM_HOST: str = os.environ.get("CISS_CIM_HOST", "vlxrr.dyndns.org")
CISS_CIM_PORT: str = os.environ.get("CISS_CIM_PORT", "3046")

# Base URL montada automaticamente
CISS_BASE_URL: str = f"http://{CISS_CIM_HOST}:{CISS_CIM_PORT}"

# ── Credenciais OAuth2 ────────────────────────────────────────────────────────
# Credenciais do usuário no ERP CISSPoder
CISS_USERNAME: str = os.environ.get("CISS_USERNAME", "")
CISS_PASSWORD: str = os.environ.get("CISS_PASSWORD", "")

# Valores fixos do protocolo Integrim (não mudam entre clientes)
CISS_CLIENT_ID: str     = "cisspoder-oauth"
CISS_CLIENT_SECRET: str = "poder7547"
CISS_GRANT_TYPE: str    = "password"

# ── Endpoints Integrim ────────────────────────────────────────────────────────
CISS_AUTH_PATH:    str = "/cisspoder-auth/oauth/token"
CISS_SERVICE_PATH: str = "/cisspoder-service"

# ── Paginação ─────────────────────────────────────────────────────────────────
# Integrim limita 100 registros/página (hard limit da API)
CISS_PAGE_SIZE: int = 100

# ── Timeouts ──────────────────────────────────────────────────────────────────
CISS_TIMEOUT_AUTH: int    = int(os.environ.get("CISS_TIMEOUT_AUTH", "30"))
CISS_TIMEOUT_SERVICE: int = int(os.environ.get("CISS_TIMEOUT_SERVICE", "60"))

# ── Data Warehouse ────────────────────────────────────────────────────────────
_base = Path(__file__).resolve().parent.parent.parent
CISS_DW_PATH: str     = os.environ.get("CISS_DW_PATH",
                            str(_base / "data" / "ciss_dw.db"))
CISS_DW_DSN: str      = os.environ.get("CISS_DW_DSN",
                            os.environ.get("CISS_DATABASE_URL", ""))
CISS_MODELS_PATH: str = os.environ.get("CISS_MODELS_PATH",
                            str(_base / "data" / "ciss_models"))
CISS_DW_POOL_SIZE: int = int(os.environ.get("CISS_DW_POOL_SIZE", "5"))
CISS_DW_POOL_MAX_OVERFLOW: int = int(os.environ.get("CISS_DW_POOL_MAX_OVERFLOW", "10"))
CISS_DW_POOL_TIMEOUT: int = int(os.environ.get("CISS_DW_POOL_TIMEOUT", "30"))
CISS_DW_READONLY_USER: str = os.environ.get("CISS_DW_READONLY_USER", "")
CISS_DW_READONLY_PASSWORD: str = os.environ.get("CISS_DW_READONLY_PASSWORD", "")


def _sqlite_dsn_from_path(path: str) -> str:
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"


def get_ciss_dw_dsn() -> str:
    """
    Resolve o DSN do DW CISS.

    Precedencia: CISS_DW_DSN, CISS_DATABASE_URL, fallback SQLite via CISS_DW_PATH.
    """
    dsn = os.environ.get("CISS_DW_DSN") or os.environ.get("CISS_DATABASE_URL")
    if dsn:
        return dsn
    return _sqlite_dsn_from_path(os.environ.get("CISS_DW_PATH", CISS_DW_PATH))


def is_ciss_dw_postgres() -> bool:
    """Retorna True quando o DSN resolvido aponta para PostgreSQL."""
    return make_url(get_ciss_dw_dsn()).get_backend_name().startswith("postgresql")

# ── Agendamento ETL ───────────────────────────────────────────────────────────
ETL_FULL_HOUR_UTC: int       = int(os.environ.get("CISS_ETL_FULL_HOUR", "2"))
ETL_STOCK_INTERVAL_MIN: int  = int(os.environ.get("CISS_ETL_STOCK_INTERVAL_MIN", "60"))
ETL_SALES_INTERVAL_MIN: int  = int(os.environ.get("CISS_ETL_SALES_INTERVAL_MIN", "30"))

# ── ML ────────────────────────────────────────────────────────────────────────
ML_HISTORY_DAYS: int  = int(os.environ.get("CISS_ML_HISTORY_DAYS", "90"))
ML_FORECAST_DAYS: int = int(os.environ.get("CISS_ML_FORECAST_DAYS", "30"))

STOCK_ALERT_DAYS_CRITICAL:  int = int(os.environ.get("CISS_STOCK_ALERT_CRITICAL", "3"))
STOCK_ALERT_DAYS_WARNING:   int = int(os.environ.get("CISS_STOCK_ALERT_WARNING", "7"))
STOCK_ALERT_DAYS_ATTENTION: int = int(os.environ.get("CISS_STOCK_ALERT_ATTENTION", "15"))


def validate_config() -> list[str]:
    """Retorna lista de erros de configuração (vazia se OK)."""
    errors = []
    if not CISS_USERNAME:
        errors.append("CISS_USERNAME não configurado")
    if not CISS_PASSWORD:
        errors.append("CISS_PASSWORD não configurado")
    if not CISS_CIM_HOST:
        errors.append("CISS_CIM_HOST não configurado")
    return errors
