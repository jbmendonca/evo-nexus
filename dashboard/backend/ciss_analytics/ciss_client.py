"""
Cliente OAuth2 para a API Integrim / CISSPoder.

Protocolo conforme manual oficial:
  - Auth:     POST /cisspoder-auth/oauth/token (x-www-form-urlencoded)
  - Serviços: POST /cisspoder-service/<nome_servico> (JSON + Bearer)
  - Paginação: campo `page` + `hasNext` (100 registros/página)
  - Token:    validade 24h — renovação automática

Credenciais ficam SOMENTE no .env (CISS_USERNAME / CISS_PASSWORD).
"""
import json
import logging
import os
import time
import threading
from typing import Any, Generator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    CISS_BASE_URL,
    CISS_AUTH_PATH,
    CISS_SERVICE_PATH,
    CISS_USERNAME,
    CISS_PASSWORD,
    CISS_CLIENT_ID,
    CISS_CLIENT_SECRET,
    CISS_GRANT_TYPE,
    CISS_PAGE_SIZE,
    CISS_TIMEOUT_AUTH,
    CISS_TIMEOUT_SERVICE,
)

logger = logging.getLogger(__name__)


def _build_session() -> requests.Session:
    """Sessão requests com retry em falhas de rede/5xx."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class CISSClient:
    """
    Cliente para a API Integrim/CISSPoder.

    Gerencia ciclo de vida do token OAuth2 (renovação automática),
    paginação transparente e throttling respeitoso.

    Uso:
        client = CISSClient()
        for page in client.get_all("cad_pessoas"):
            for pessoa in page:
                processar(pessoa)
    """

    def __init__(self) -> None:
        self._session = _build_session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._last_req_at: float = 0.0
        self._min_interval: float = 0.1  # 100ms entre requests

    # ── Autenticação ──────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        """
        Obtém novo token OAuth2 via POST x-www-form-urlencoded.
        Conforme seção 2 do manual Integrim.
        """
        url = f"{CISS_BASE_URL}{CISS_AUTH_PATH}"
        payload = {
            "client_id":     CISS_CLIENT_ID,
            "client_secret": CISS_CLIENT_SECRET,
            "grant_type":    CISS_GRANT_TYPE,
            "username":      CISS_USERNAME,
            "password":      CISS_PASSWORD,
        }

        logger.info("CISSPoder: autenticando em %s", url)
        resp = self._session.post(
            url,
            data=payload,                                   # x-www-form-urlencoded
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=CISS_TIMEOUT_AUTH,
        )

        if resp.status_code == 401:
            raise PermissionError(
                "CISSPoder: credenciais inválidas. "
                "Verifique CISS_USERNAME e CISS_PASSWORD."
            )
        if resp.status_code == 403:
            raise PermissionError(
                "CISSPoder: acesso bloqueado (403). "
                "Período de carência Integrim expirado — libere o fornecedor "
                "na interface administrativa: "
                f"http://{CISS_BASE_URL}/integrim-admin/"
            )

        resp.raise_for_status()
        data = resp.json()

        token = data.get("access_token")
        if not token:
            raise ValueError(f"CISSPoder: token não retornado. Resposta: {data}")

        # Token válido por 24h — subtrai 5min como margem de segurança
        expires_in = data.get("expires_in", 86400)
        self._token_expires_at = time.monotonic() + expires_in - 300

        logger.info(
            "CISSPoder: token obtido com sucesso (expira em %ds)", expires_in
        )
        return token

    def _get_token(self) -> str:
        """Retorna token válido, renovando se necessário (thread-safe)."""
        with self._lock:
            if not self._token or time.monotonic() >= self._token_expires_at:
                self._token = self._authenticate()
            return self._token

    # ── Requisições de Serviço ────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Garante intervalo mínimo entre requests."""
        elapsed = time.monotonic() - self._last_req_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_req_at = time.monotonic()

    def _service_url(self, service: str) -> str:
        """Monta URL completa para um serviço Integrim."""
        return f"{CISS_BASE_URL}{CISS_SERVICE_PATH}/{service.lower()}"

    def post_service(
        self,
        service: str,
        page: int = 1,
        clausulas: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Chama um serviço do Integrim via POST JSON.

        Conforme seção 3 do manual:
          POST /cisspoder-service/<service>
          Authorization: Bearer <token>
          Content-Type: application/json
          Body: { "page": N, "clausulas": [...] }

        Args:
            service:   nome do serviço (ex: "cad_pessoas", "get_pedido_venda_prod")
            page:      número da página (começa em 1)
            clausulas: lista de filtros no formato Integrim

        Returns:
            dict com os dados retornados pela API
        """
        self._throttle()
        token = self._get_token()
        url = self._service_url(service)

        body: dict[str, Any] = {"page": page}
        if clausulas:
            body["clausulas"] = clausulas

        logger.debug("CISSPoder POST %s página=%d filtros=%d",
                     service, page, len(clausulas or []))

        resp = self._session.post(
            url,
            json=body,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=CISS_TIMEOUT_SERVICE,
        )

        # Token expirado durante a requisição — renova e tenta uma vez
        if resp.status_code == 401:
            logger.info("CISSPoder: token expirado durante requisição — renovando")
            with self._lock:
                self._token = None
            token = self._get_token()
            resp = self._session.post(
                url,
                json=body,
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=CISS_TIMEOUT_SERVICE,
            )

        if resp.status_code == 403:
            raise PermissionError(
                f"CISSPoder: sem permissão para o serviço '{service}'. "
                "Verifique as permissões do fornecedor na interface Integrim."
            )

        if resp.status_code == 404:
            logger.warning("CISSPoder: serviço '%s' não encontrado (404)", service)
            return {}

        resp.raise_for_status()

        try:
            return resp.json()
        except Exception:
            logger.error("CISSPoder: resposta não-JSON de %s: %s", service, resp.text[:200])
            return {}

    def get_all(
        self,
        service: str,
        clausulas: list[dict] | None = None,
        max_pages: int | None = None,
    ) -> Generator[list[dict], None, None]:
        """
        Itera por todas as páginas de um serviço Integrim.

        O Integrim usa:
          - 100 registros por página (hard limit)
          - campo `hasNext` (True = há próxima página)

        Yields:
            Lista de registros de cada página
        """
        page_limit = max_pages or int(os.environ.get("CISS_MAX_PAGES", "5000"))
        for page in range(1, page_limit + 1):
            data = self.post_service(service, page=page, clausulas=clausulas)

            if not data:
                break

            # O Integrim retorna os dados em uma chave que varia por serviço.
            # Tentamos extrair a lista de registros de forma genérica.
            registros = self._extrair_registros(data, service)

            if registros:
                yield registros

            total = data.get("total", 0)
            has_next = data.get("hasNext", False)

            logger.info(
                "CISSPoder %s: página %d — %d registros (total=%s, hasNext=%s)",
                service, page, len(registros), total, has_next
            )

            if not has_next:
                break

    @staticmethod
    def _extrair_registros(data: dict, service: str) -> list[dict]:
        """
        Extrai a lista de registros da resposta Integrim.

        A API retorna dados em chaves variáveis conforme o serviço.
        Estratégia: encontrar o primeiro valor que seja uma lista de dicts.
        """
        if not data:
            return []

        # Chaves candidatas comuns
        for key in ["dados", "data", "registros", "itens", "content", "list",
                    "result", "results", service.lower()]:
            val = data.get(key)
            if isinstance(val, list):
                return val

        # Fallback: busca qualquer valor lista
        for val in data.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val

        # Se o próprio data for uma lista (resposta flat)
        if isinstance(data, list):
            return data  # type: ignore[return-value]

        return []

    def health_check(self) -> bool:
        """Verifica se a API Integrim está acessível tentando autenticar."""
        try:
            self._get_token()
            return True
        except Exception as exc:
            logger.warning("CISSPoder health check falhou: %s", exc)
            return False


# ── Singleton thread-safe ─────────────────────────────────────────────────────
_client: CISSClient | None = None
_client_lock = threading.Lock()


def get_client() -> CISSClient:
    """Retorna instância singleton do CISSClient (thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = CISSClient()
    return _client
