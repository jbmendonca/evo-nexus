"""Question-answering agent grounded in CISS DW analytics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import unicodedata
from typing import Any
from typing import Mapping, Protocol

import requests

from .manager_analytics import build_manager_snapshot
from .schema import TABLES, get_dw_conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value.lower()).strip()
    return value


def _money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:.1f}%".replace(".", ",")


def _topic(question: str) -> str:
    q = _normalize(question)
    if any(word in q for word in ("produto", "sku", "item", "vendem", "vende", "mais vendido")):
        return "produtos"
    if any(word in q for word in ("vendedor", "vendedores", "consultor", "time comercial")):
        return "vendedores"
    if any(word in q for word in ("estoque", "reposicao", "repor", "comprar", "fornecedor", "acabando", "ruptura")):
        return "estoque"
    if any(word in q for word in ("diaria", "diario", "semanal", "mensal", "anual", "periodo", "evolucao")):
        return "series"
    if any(word in q for word in ("previsao", "preditivo", "tendencia", "futuro", "proximos")):
        return "preditivo"
    if any(word in q for word in ("media", "mediana", "cluster", "anomalia", "desvio", "estatistica")):
        return "estatistica"
    if any(word in q for word in ("cliente", "clientes", "rfm", "segmento", "churn")):
        return "clientes"
    return "resumo"


def _extract_json_object(text: str) -> dict[str, Any]:
    value = (text or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", value)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


class DataScientistLLMProvider(Protocol):
    def respond(
        self,
        *,
        question: str,
        topic: str,
        snapshot: Mapping[str, Any],
        fallback_answer: str,
    ) -> tuple[str, float, list[str], str]:
        """Returns answer, confidence, suggested questions and provider label."""


class HeuristicDataScientistLLM:
    """Deterministic local provider when external LLM is unavailable."""

    def respond(
        self,
        *,
        question: str,
        topic: str,
        snapshot: Mapping[str, Any],
        fallback_answer: str,
    ) -> tuple[str, float, list[str], str]:
        _ = question, topic, snapshot
        return fallback_answer, 0.72, DEFAULT_SUGGESTED_QUESTIONS, "heuristic"


class OpenAIDataScientistLLM:
    """OpenAI-compatible chat completion provider."""

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def respond(
        self,
        *,
        question: str,
        topic: str,
        snapshot: Mapping[str, Any],
        fallback_answer: str,
    ) -> tuple[str, float, list[str], str]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": _user_prompt(
                        question=question,
                        topic=topic,
                        snapshot=snapshot,
                        fallback_answer=fallback_answer,
                    ),
                },
            ],
        }
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _extract_json_object(str(content))
        answer = str(parsed.get("answer") or "").strip() or fallback_answer
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.86
        suggestions_raw = parsed.get("suggested_questions")
        suggestions: list[str] = []
        if isinstance(suggestions_raw, list):
            suggestions = [str(item).strip() for item in suggestions_raw if str(item).strip()]
        if not suggestions:
            suggestions = DEFAULT_SUGGESTED_QUESTIONS
        return answer, max(0.55, min(confidence, 0.98)), suggestions[:6], f"openai:{self.model}"


def _system_prompt() -> str:
    return (
        "Voce e o agente Cientista-de-Dados da Pau Brasil. "
        "Especialista em CISS, vendas, produtos, vendedores e estoque. "
        "Responda com foco executivo: diagnostico, insight acionavel, conselho pratico e risco. "
        "Nunca invente dado fora do contexto recebido. "
        "Retorne JSON estrito com: answer (string), confidence (0-1), suggested_questions (lista)."
    )


def _user_prompt(*, question: str, topic: str, snapshot: Mapping[str, Any], fallback_answer: str) -> str:
    resumo = snapshot.get("resumo", {})
    produtos = snapshot.get("produtos", {}).get("top_vendas", [])[:5]
    vendedores = snapshot.get("vendedores", {}).get("ranking", [])[:5]
    unidades = snapshot.get("unidades", {}).get("ranking", [])[:5]
    estoque = snapshot.get("estoque", {})
    return (
        "Pergunta do empresario:\n"
        f"{question}\n\n"
        f"Topico detectado: {topic}\n"
        "Contexto consolidado do DW CISS:\n"
        f"- Resumo: {json.dumps(resumo, ensure_ascii=False)}\n"
        f"- Top produtos: {json.dumps(produtos, ensure_ascii=False)}\n"
        f"- Top vendedores: {json.dumps(vendedores, ensure_ascii=False)}\n"
        f"- Ranking unidades: {json.dumps(unidades, ensure_ascii=False)}\n"
        f"- Estoque: {json.dumps(estoque, ensure_ascii=False)}\n"
        f"- Resposta base validada: {fallback_answer}\n\n"
        "Melhore a resposta com linguagem consultiva e objetiva para dono/gerente.\n"
        "Entregue apenas JSON."
    )


def _build_data_scientist_llm() -> DataScientistLLMProvider:
    mode = os.environ.get("CISS_DATA_AGENT_LLM", "auto").strip().lower()
    api_key = (os.environ.get("CISS_DATA_AGENT_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (os.environ.get("CISS_DATA_AGENT_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    base_url = (os.environ.get("CISS_DATA_AGENT_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
    timeout_raw = os.environ.get("CISS_DATA_AGENT_TIMEOUT", "20").strip()
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 20.0

    if mode == "heuristic":
        return HeuristicDataScientistLLM()
    if mode == "openai":
        if not api_key:
            return HeuristicDataScientistLLM()
        return OpenAIDataScientistLLM(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
    if api_key:
        return OpenAIDataScientistLLM(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
    return HeuristicDataScientistLLM()


def _answer_products(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    products = snapshot["produtos"]["top_vendas"][:5]
    if not products:
        return "Ainda nao ha vendas suficientes para ranquear produtos.", {"produtos": []}
    lines = [
        f"{idx}. {p['nome_produto']} vendeu {_money(p['total_vendas'])} ({p['quantidade']} un.; {p['transacoes']} vendas)"
        for idx, p in enumerate(products, start=1)
    ]
    top = products[0]
    answer = (
        f"O produto lider e {top['nome_produto']}, com {_money(top['total_vendas'])} no periodo. "
        "Ranking principal:\n" + "\n".join(lines)
    )
    return answer, {"produtos": products}


def _answer_sellers(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    sellers = snapshot["vendedores"]["ranking"][:5]
    if not sellers:
        return "Ainda nao ha vendedor associado nas vendas do periodo.", {"vendedores": []}
    lines = [
        f"{idx}. {s['nome_vendedor']} vendeu {_money(s['total_vendas'])} em {s['transacoes']} vendas"
        for idx, s in enumerate(sellers, start=1)
    ]
    top = sellers[0]
    answer = (
        f"O vendedor com maior venda e {top['nome_vendedor']}, com {_money(top['total_vendas'])}. "
        "Ranking:\n" + "\n".join(lines)
    )
    return answer, {"vendedores": sellers}


def _answer_inventory(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    inventory = snapshot["estoque"]
    suggestions = inventory.get("reposicao_sugerida", [])[:8]
    summary = inventory.get("resumo_alertas", {})
    if inventory.get("sem_estoque_configurado"):
        return (
            "O DW ainda nao tem posicao de estoque carregada. As vendas ja permitem medir giro, "
            "mas a decisao de compra precisa do servico de estoque do CISS/Integrim habilitado.",
            {"estoque": inventory},
        )
    if not suggestions:
        return (
            f"Estoque sem reposicao critica no momento. Resumo: {summary.get('critico', 0)} criticos, "
            f"{summary.get('alerta', 0)} em alerta e {summary.get('atencao', 0)} em atencao.",
            {"estoque": inventory},
        )
    lines = [
        f"{idx}. {p['nome_produto']}: comprar {p['quantidade_sugerida_compra']} un. ({p['nivel_alerta']})"
        for idx, p in enumerate(suggestions, start=1)
    ]
    answer = (
        f"Ha {summary.get('critico', 0)} itens criticos e {summary.get('alerta', 0)} em alerta. "
        "Prioridade de compra:\n" + "\n".join(lines)
    )
    return answer, {"reposicao_sugerida": suggestions, "resumo_alertas": summary}


def _answer_series(snapshot: dict[str, Any], question: str) -> tuple[str, dict[str, Any]]:
    q = _normalize(question)
    if "anual" in q:
        key = "anual"
    elif "mensal" in q:
        key = "mensal"
    elif "semanal" in q:
        key = "semanal"
    else:
        key = "diario"
    series = snapshot["series"].get(key, [])
    if not series:
        return f"Ainda nao ha serie {key} disponivel para o periodo.", {"serie": []}
    last = series[-1]
    answer = (
        f"A serie {key} tem {len(series)} pontos. O ultimo periodo ({last.get('periodo') or last.get('data')}) "
        f"registrou {_money(last['total_vendas'])}, {last['transacoes']} vendas e ticket medio de {_money(last['ticket_medio'])}."
    )
    return answer, {"tipo": key, "serie": series[-12:]}


def _answer_predictive(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    summary = snapshot["resumo"]
    stats = snapshot["estatistica"]
    predictive = snapshot.get("preditivo", {})
    growth = predictive.get("crescimento_pct", summary.get("crescimento_pct", 0))
    trend = predictive.get("tendencia") or ("alta" if growth > 5 else ("queda" if growth < -5 else "estabilidade"))
    weekday = sorted(stats.get("padrao_dia_semana", []), key=lambda x: x["media_vendas"], reverse=True)
    best_day = weekday[0]["dia_semana"] if weekday else "sem padrao definido"
    total_30 = predictive.get("total_previsto_30_dias")
    forecast_sentence = (
        f" A previsao de 30 dias soma {_money(total_30)}." if total_30 is not None else ""
    )
    answer = (
        f"A leitura preditiva indica {trend} no periodo, com variacao de {_pct(growth)} contra o periodo anterior. "
        f"O melhor dia medio de vendas e {best_day}.{forecast_sentence} Use os alertas de estoque junto com os produtos de alto giro "
        "para antecipar compras antes da ruptura."
    )
    return answer, {"resumo": summary, "preditivo": predictive, "padrao_dia_semana": weekday[:7]}


def _answer_stats(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    stats = snapshot["estatistica"]
    clusters = stats.get("clusters_produtos", [])[:8]
    counts: dict[str, int] = {}
    for row in stats.get("clusters_produtos", []):
        counts[row["cluster"]] = counts.get(row["cluster"], 0) + 1
    answer = (
        f"Media diaria de vendas: {_money(stats['media_vendas_dia'])}. "
        f"Mediana diaria: {_money(stats['mediana_vendas_dia'])}. "
        f"Desvio padrao diario: {_money(stats['desvio_padrao_vendas_dia'])}. "
        f"Clusters de produtos: {counts}."
    )
    return answer, {"estatistica": stats, "clusters_amostra": clusters}


def _answer_customers(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    coverage = snapshot["cobertura_dados"]
    active = snapshot["resumo"].get("clientes_ativos", 0)
    answer = (
        f"Foram identificados {active} clientes ativos no periodo. "
        f"O DW possui {coverage['tabelas'].get('clientes', 0)} clientes carregados. "
        "A segmentacao RFM fica disponivel quando as vendas tiverem id_cliente preenchido."
    )
    return answer, {"clientes_ativos": active, "cobertura": coverage}


def _answer_summary(snapshot: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    summary = snapshot["resumo"]
    answer = (
        f"No periodo de {summary['periodo_dias']} dias, a Pau Brasil vendeu {_money(summary['total_vendas'])} "
        f"em {summary['transacoes']} vendas. Ticket medio: {_money(summary['ticket_medio'])}; "
        f"ticket mediano: {_money(summary['ticket_mediano'])}; crescimento: {_pct(summary['crescimento_pct'])}."
    )
    return answer, {"resumo": summary}


DEFAULT_SUGGESTED_QUESTIONS = [
    "Quais produtos mais vendem?",
    "Qual vendedor vendeu mais este mes?",
    "Qual loja/callcenter faturou mais no periodo?",
    "O que preciso comprar para repor estoque?",
    "Qual a media e mediana diaria de vendas?",
]


def _record(question: str, normalized: str, topic: str, answer: str, confidence: float) -> None:
    conn = get_dw_conn()
    try:
        conn.execute(
            TABLES["dw_data_agent_memory"].insert().values(
                question=question,
                normalized_question=normalized,
                topic=topic,
                answer=answer,
                confidence=confidence,
                created_at=_now(),
            )
        )
        conn.commit()
    finally:
        conn.close()


def answer_question(question: str, period_days: int = 90) -> dict[str, Any]:
    """Answer a manager question using curated CISS analytics."""
    question = (question or "").strip()
    if not question:
        return {
            "ok": False,
            "topic": "invalid",
            "answer": "Envie uma pergunta sobre vendas, produtos, vendedores, estoque, previsao ou estatisticas.",
            "data": {},
        }

    snapshot = build_manager_snapshot(period_days=period_days, limit=20)
    normalized = _normalize(question)
    topic = _topic(question)
    handlers = {
        "produtos": lambda: _answer_products(snapshot),
        "vendedores": lambda: _answer_sellers(snapshot),
        "estoque": lambda: _answer_inventory(snapshot),
        "series": lambda: _answer_series(snapshot, question),
        "preditivo": lambda: _answer_predictive(snapshot),
        "estatistica": lambda: _answer_stats(snapshot),
        "clientes": lambda: _answer_customers(snapshot),
        "resumo": lambda: _answer_summary(snapshot),
    }
    base_answer, data = handlers.get(topic, handlers["resumo"])()
    llm = _build_data_scientist_llm()
    try:
        answer, confidence, suggested_questions, provider = llm.respond(
            question=question,
            topic=topic,
            snapshot=snapshot,
            fallback_answer=base_answer,
        )
    except Exception:
        answer = base_answer
        confidence = 0.7 if topic == "resumo" else 0.84
        suggested_questions = DEFAULT_SUGGESTED_QUESTIONS
        provider = "heuristic:fallback"

    _record(question, normalized, topic, answer, confidence)
    return {
        "ok": True,
        "agent_name": "Cientista-de-Dados",
        "agent_provider": provider,
        "topic": topic,
        "confidence": confidence,
        "answer": answer,
        "data": data,
        "suggested_questions": suggested_questions,
        "snapshot_generated_at": snapshot["gerado_em"],
    }


def recent_questions(limit: int = 20) -> list[dict[str, Any]]:
    rows = _fetch_agent_rows(max(1, min(limit, 100)))
    return rows


def _fetch_agent_rows(limit: int) -> list[dict[str, Any]]:
    conn = get_dw_conn()
    try:
        rows = conn.execute(
            """
            SELECT question, topic, answer, confidence, created_at
            FROM dw_data_agent_memory
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
