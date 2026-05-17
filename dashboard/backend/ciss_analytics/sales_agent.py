"""Sales agent orchestration for CISS omnichannel sales."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import threading
import unicodedata
from typing import Any, Mapping, Protocol, Sequence

import requests

from .integrim_sales import DWCatalogService, OutOfStockError
from .omnichannel import NormalizationResult, normalize_evolution_webhook


HANDOFF_MESSAGE = "Estou transferindo seu atendimento para um consultor humano."


@dataclass(slots=True)
class RequestedItem:
    query: str
    quantity: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "quantity": self.quantity}


@dataclass(slots=True)
class SalesIntent:
    intent: str
    items: list[RequestedItem] = field(default_factory=list)
    confidence: float = 0.0
    handoff_reason: str | None = None
    customer: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "items": [item.to_dict() for item in self.items],
            "confidence": self.confidence,
            "handoff_reason": self.handoff_reason,
            "customer": self.customer,
            "raw": self.raw,
        }


@dataclass(slots=True)
class AgentResponse:
    ok: bool
    status: str
    message: str
    chat_id: str = ""
    reply: str = ""
    automation_suspended: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "chat_id": self.chat_id,
            "reply": self.reply,
            "automation_suspended": self.automation_suspended,
            "data": self.data,
            "actions": self.actions,
        }
        if self.error:
            payload["error"] = self.error
        return payload


class SalesLLMProvider(Protocol):
    def analyze(self, text: str, context: Mapping[str, Any]) -> SalesIntent:
        """Return a structured sales intent for the inbound message."""


class HeuristicSalesLLM:
    """Deterministic local fallback used when no external LLM is configured."""

    def analyze(self, text: str, context: Mapping[str, Any]) -> SalesIntent:
        reason = detect_handoff_reason(text)
        if reason:
            return SalesIntent(intent="handoff", confidence=0.95, handoff_reason=reason)

        normalized = _ascii_lower(text)
        intent = "catalog_query"
        if any(word in normalized for word in ("confirmo", "fechar", "finalizar", "comprar", "vou levar")):
            intent = "confirm_order"
        elif any(word in normalized for word in ("preco", "valor", "orcamento", "tem ", "tem?", "vende")):
            intent = "catalog_query"
        elif any(word in normalized for word in ("oi", "ola", "bom dia", "boa tarde", "boa noite")):
            intent = "needs_information"

        return SalesIntent(intent=intent, items=_extract_requested_items(text), confidence=0.55)


class HTTPSalesLLMProvider:
    """Generic HTTP JSON provider for a primary or secondary sales LLM."""

    def __init__(self, *, url: str, api_key: str = "", timeout: float = 10.0) -> None:
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def analyze(self, text: str, context: Mapping[str, Any]) -> SalesIntent:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = requests.post(
            self.url,
            json={"text": text, "context": dict(context)},
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return intent_from_mapping(response.json())


class FallbackSalesLLM:
    """Tries the primary provider and falls back to the secondary provider."""

    def __init__(
        self,
        primary: SalesLLMProvider,
        secondary: SalesLLMProvider | None = None,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.last_provider = ""

    def analyze(self, text: str, context: Mapping[str, Any]) -> SalesIntent:
        try:
            intent = self.primary.analyze(text, context)
            self.last_provider = self.primary.__class__.__name__
            return intent
        except Exception as primary_exc:
            if self.secondary is None:
                raise primary_exc
            intent = self.secondary.analyze(text, {**dict(context), "primary_error": str(primary_exc)})
            self.last_provider = self.secondary.__class__.__name__
            intent.raw = {**intent.raw, "fallback_from": self.primary.__class__.__name__}
            return intent


class InMemorySuspensionStore:
    """Tracks chats where automation is suspended for human handoff."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    def suspend(self, chat_id: str, reason: str) -> None:
        with self._lock:
            self._data[chat_id] = {"reason": reason}

    def resume(self, chat_id: str) -> None:
        with self._lock:
            self._data.pop(chat_id, None)

    def is_suspended(self, chat_id: str) -> bool:
        with self._lock:
            return chat_id in self._data

    def reason(self, chat_id: str) -> str:
        with self._lock:
            return str(self._data.get(chat_id, {}).get("reason") or "")


class SalesAgent:
    """Coordinates webhook normalization, LLM intent, catalog lookup, and stock guards."""

    def __init__(
        self,
        *,
        catalog: DWCatalogService | None = None,
        llm: SalesLLMProvider | None = None,
        suspension_store: InMemorySuspensionStore | None = None,
    ) -> None:
        self.catalog = catalog or DWCatalogService()
        self.llm = llm or build_llm_from_env()
        self.suspension_store = suspension_store or InMemorySuspensionStore()

    def handle_webhook(self, payload: dict[str, Any]) -> AgentResponse:
        normalized = normalize_evolution_webhook(payload)
        return self.process_normalized(normalized)

    def process_normalized(self, normalized: NormalizationResult) -> AgentResponse:
        incoming = normalized.incoming
        chat_id = incoming.chat_id if incoming else ""

        if normalized.status == "needs_transcription":
            return AgentResponse(
                ok=True,
                status="needs_transcription",
                message=normalized.message,
                chat_id=chat_id,
                reply="Recebemos seu audio. Um consultor continuara assim que houver transcricao.",
                data={"normalization": normalized.to_dict()},
            )
        if normalized.status != "ok":
            return AgentResponse(
                ok=normalized.status == "ignored",
                status=normalized.status,
                message=normalized.message,
                chat_id=chat_id,
                data={"normalization": normalized.to_dict()},
                error=normalized.error,
            )
        if incoming is None:
            return AgentResponse(
                ok=False,
                status="invalid_payload",
                message="Normalized message is missing.",
                error="missing_incoming_message",
            )

        if self.suspension_store.is_suspended(chat_id):
            return AgentResponse(
                ok=True,
                status="suspended",
                message="Automation is suspended for this chat.",
                chat_id=chat_id,
                reply="Seu atendimento ja esta com um consultor humano.",
                automation_suspended=True,
                data={"reason": self.suspension_store.reason(chat_id)},
            )

        deterministic_reason = detect_handoff_reason(incoming.text)
        if deterministic_reason:
            return self._handoff(chat_id, deterministic_reason, incoming.text)

        context = {"chat_id": chat_id, "sender_name": incoming.sender_name, "channel": incoming.channel}
        try:
            intent = self.llm.analyze(incoming.text, context)
        except Exception as exc:
            return AgentResponse(
                ok=False,
                status="llm_unavailable",
                message="Sales LLM providers are unavailable.",
                chat_id=chat_id,
                reply="Nao consegui continuar o atendimento automatico agora. Vou transferir para um consultor.",
                error=str(exc),
            )

        if intent.intent == "handoff":
            return self._handoff(chat_id, intent.handoff_reason or "complex_intent", incoming.text)

        if not intent.items:
            return AgentResponse(
                ok=True,
                status="needs_information",
                message="No product request was detected.",
                chat_id=chat_id,
                reply="Me diga o produto e a quantidade para eu consultar estoque e preco.",
                data={"intent": intent.to_dict()},
            )

        resolved = self.catalog.resolve_requested_items([item.to_dict() for item in intent.items])
        if resolved.unresolved:
            return AgentResponse(
                ok=True,
                status="catalog_not_found",
                message="Some requested products were not found in the catalog.",
                chat_id=chat_id,
                reply="Nao encontrei esse item no catalogo. Vou precisar de mais detalhes do produto.",
                data={"intent": intent.to_dict(), "resolved": resolved.to_dict()},
            )

        stock = self.catalog.validate_stock(resolved.lines)
        if not stock.ok:
            return AgentResponse(
                ok=True,
                status="out_of_stock",
                message="Stock validation failed before sale confirmation.",
                chat_id=chat_id,
                reply="Esse item nao tem estoque suficiente agora. Vou chamar um consultor para ver alternativas.",
                data={
                    "intent": intent.to_dict(),
                    "resolved": resolved.to_dict(),
                    "stock": stock.to_dict(),
                },
            )

        if intent.intent == "confirm_order":
            try:
                sale_payload = self.catalog.prepare_sale_payload(
                    chat_id=chat_id,
                    customer={"name": incoming.sender_name, "whatsapp": incoming.sender_id or chat_id},
                    lines=resolved.lines,
                    metadata={"source_message_id": incoming.message_id},
                    revalidate=True,
                )
            except OutOfStockError as exc:
                return AgentResponse(
                    ok=True,
                    status="out_of_stock",
                    message="Stock changed before Integrim sale payload generation.",
                    chat_id=chat_id,
                    data={"stock": exc.validation.to_dict()},
                )
            return AgentResponse(
                ok=True,
                status="sale_payload_ready",
                message="Sale payload prepared after stock validation.",
                chat_id=chat_id,
                reply="Pedido conferido com estoque disponivel. Vou seguir para a proxima etapa.",
                data={
                    "intent": intent.to_dict(),
                    "resolved": resolved.to_dict(),
                    "stock": stock.to_dict(),
                    "integrim_sale": sale_payload,
                },
                actions=[{"type": "prepare_integrim_sale", "payload": sale_payload}],
            )

        return AgentResponse(
            ok=True,
            status="quote_ready",
            message="Catalog and stock data prepared for reply.",
            chat_id=chat_id,
            reply=_quote_reply(resolved.lines),
            data={"intent": intent.to_dict(), "resolved": resolved.to_dict(), "stock": stock.to_dict()},
        )

    def _handoff(self, chat_id: str, reason: str, text: str) -> AgentResponse:
        self.suspension_store.suspend(chat_id, reason)
        return AgentResponse(
            ok=True,
            status="handoff",
            message="Human handoff requested.",
            chat_id=chat_id,
            reply=HANDOFF_MESSAGE,
            automation_suspended=True,
            data={"reason": reason, "text": text},
            actions=[{"type": "handoff_to_human", "reason": reason, "chat_id": chat_id}],
        )


def build_llm_from_env() -> SalesLLMProvider:
    primary = _provider_from_env("CISS_SALES_LLM_PRIMARY", default="heuristic")
    secondary_name = os.environ.get("CISS_SALES_LLM_SECONDARY", "").strip()
    secondary = _provider_from_env("CISS_SALES_LLM_SECONDARY", default="") if secondary_name else None
    return FallbackSalesLLM(primary=primary, secondary=secondary)


def intent_from_mapping(data: Mapping[str, Any]) -> SalesIntent:
    items_raw = data.get("items") or data.get("produtos") or []
    items: list[RequestedItem] = []
    if isinstance(items_raw, Sequence) and not isinstance(items_raw, (str, bytes)):
        for item in items_raw:
            if not isinstance(item, Mapping):
                continue
            query = str(item.get("query") or item.get("produto") or item.get("name") or "").strip()
            if query:
                items.append(RequestedItem(query=query, quantity=_safe_quantity(item.get("quantity") or item.get("quantidade"))))
    return SalesIntent(
        intent=str(data.get("intent") or data.get("tipo") or "catalog_query"),
        items=items,
        confidence=_safe_quantity(data.get("confidence"), default=0.0),
        handoff_reason=str(data.get("handoff_reason") or data.get("reason") or "") or None,
        customer=dict(data.get("customer") or {}),
        raw=dict(data),
    )


def detect_handoff_reason(text: str) -> str | None:
    normalized = _ascii_lower(text)
    discount_limit = _safe_quantity(os.environ.get("CISS_SALES_MAX_DISCOUNT_PCT"), default=0.0)
    discount_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%.*desconto", normalized)
    if discount_match and _safe_quantity(discount_match.group(1)) > discount_limit:
        return "discount_request"

    keyword_groups = {
        "discount_request": ("desconto", "preco melhor", "cobre oferta", "por menos"),
        "complaint": ("reclamacao", "reclamar", "defeito", "troca", "devolucao", "garantia"),
        "human_request": ("humano", "atendente", "consultor", "gerente"),
        "complex_intent": ("licitacao", "faturado", "credito", "prazo especial", "nota fiscal"),
    }
    for reason, keywords in keyword_groups.items():
        if any(keyword in normalized for keyword in keywords):
            return reason
    return None


def _provider_from_env(var_name: str, *, default: str) -> SalesLLMProvider:
    provider = os.environ.get(var_name, default).strip().lower()
    if provider in ("", "heuristic", "local"):
        return HeuristicSalesLLM()
    if provider == "http":
        prefix = var_name
        url = os.environ.get(f"{prefix}_URL", "").strip()
        if not url:
            raise ValueError(f"{prefix}_URL is required when {var_name}=http")
        api_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
        timeout = _safe_quantity(os.environ.get(f"{prefix}_TIMEOUT"), default=10.0)
        return HTTPSalesLLMProvider(url=url, api_key=api_key, timeout=timeout)
    raise ValueError(f"Unsupported sales LLM provider: {provider}")


def _extract_requested_items(text: str) -> list[RequestedItem]:
    normalized = _ascii_lower(text)
    cleaned = re.sub(r"[?!.,;:]", " ", normalized)
    matches = re.findall(r"(\d+(?:[.,]\d+)?)\s+([a-z0-9][a-z0-9\s-]{2,60})", cleaned)
    items: list[RequestedItem] = []
    for qty_raw, query_raw in matches:
        query = _clean_product_query(query_raw)
        if query:
            items.append(RequestedItem(query=query, quantity=_safe_quantity(qty_raw, default=1.0)))
    if items:
        return items[:5]

    query = _clean_product_query(cleaned)
    return [RequestedItem(query=query, quantity=1.0)] if query else []


def _clean_product_query(value: str) -> str:
    stop_words = {
        "quero",
        "queria",
        "preciso",
        "comprar",
        "confirmo",
        "fechar",
        "finalizar",
        "pedido",
        "orcamento",
        "orcar",
        "preco",
        "valor",
        "quanto",
        "tem",
        "vende",
        "voces",
        "voce",
        "por",
        "favor",
        "unidade",
        "unidades",
        "saco",
        "sacos",
        "peca",
        "pecas",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "um",
        "uma",
        "o",
        "a",
    }
    words = [word for word in re.split(r"\s+", value) if word and word not in stop_words]
    return " ".join(words).strip()


def _quote_reply(lines: Sequence[Any]) -> str:
    parts = [
        f"{line.quantity:g} {line.unidade} {line.nome} - R$ {line.unit_price:.2f}"
        for line in lines
    ]
    return "Encontrei: " + "; ".join(parts) + ". Posso confirmar o pedido?"


def _ascii_lower(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.lower()


def _safe_quantity(value: Any, default: float = 1.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


_DEFAULT_STORE = InMemorySuspensionStore()
_DEFAULT_AGENT: SalesAgent | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_agent() -> SalesAgent:
    global _DEFAULT_AGENT
    if _DEFAULT_AGENT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_AGENT is None:
                _DEFAULT_AGENT = SalesAgent(suspension_store=_DEFAULT_STORE)
    return _DEFAULT_AGENT
