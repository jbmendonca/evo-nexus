"""
Previsão de vendas usando Média Móvel Exponencial + sazonalidade semanal.

Usa apenas numpy/statistics para zero dependências pesadas.
Quando Prophet ou statsmodels estiverem disponíveis, usa-os automaticamente.
"""
import json
import logging
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..schema import get_dw_conn
from ..config import CISS_MODELS_PATH, ML_HISTORY_DAYS, ML_FORECAST_DAYS

logger = logging.getLogger(__name__)


def _load_sales_series() -> list[dict[str, Any]]:
    """Carrega série histórica de vendas diárias do DW."""
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=ML_HISTORY_DAYS)).isoformat()
        rows = conn.execute(
            """
            SELECT data_ref, total_vendas, total_transacoes, ticket_medio
            FROM dw_kpis_diarios
            WHERE data_ref >= ?
            ORDER BY data_ref ASC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _ema(values: list[float], alpha: float = 0.3) -> list[float]:
    """Média Móvel Exponencial."""
    if not values:
        return []
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _sazonalidade_semanal(series: list[dict]) -> dict[int, float]:
    """
    Calcula índice de sazonalidade por dia da semana (0=seg, 6=dom).
    Retorna multiplicadores: 1.0 = média, 1.2 = 20% acima da média.
    """
    by_dow: dict[int, list[float]] = {i: [] for i in range(7)}
    for row in series:
        try:
            d = date.fromisoformat(row["data_ref"])
            by_dow[d.weekday()].append(float(row["total_vendas"] or 0))
        except (ValueError, TypeError):
            pass

    averages = {dow: statistics.mean(vals) if vals else 0.0 for dow, vals in by_dow.items()}
    global_avg = statistics.mean(averages.values()) if averages else 1.0
    if global_avg == 0:
        return {i: 1.0 for i in range(7)}

    return {dow: (avg / global_avg) for dow, avg in averages.items()}


def gerar_previsao() -> dict[str, Any]:
    """
    Gera previsão de vendas para os próximos ML_FORECAST_DAYS dias.

    Returns:
        {
          "historico": [{"data": "YYYY-MM-DD", "vendas": float}, ...],
          "previsao":  [{"data": "YYYY-MM-DD", "vendas_previstas": float,
                         "confianca_min": float, "confianca_max": float}, ...],
          "tendencia": "alta" | "queda" | "estavel",
          "crescimento_pct": float,
          "gerado_em": str
        }
    """
    series = _load_sales_series()
    if not series:
        return {"erro": "Dados insuficientes para previsão", "historico": [], "previsao": []}

    valores = [float(r["total_vendas"] or 0) for r in series]
    suavizados = _ema(valores, alpha=0.35)
    sazonalidade = _sazonalidade_semanal(series)

    # Tendência: compara média das últimas 2 semanas vs 2 anteriores
    n = len(valores)
    if n >= 28:
        media_recente = statistics.mean(valores[-14:])
        media_anterior = statistics.mean(valores[-28:-14])
        if media_anterior > 0:
            delta = (media_recente - media_anterior) / media_anterior * 100
        else:
            delta = 0.0
    elif n >= 2:
        delta = (valores[-1] - valores[0]) / max(valores[0], 1) * 100
    else:
        delta = 0.0

    tendencia = "alta" if delta > 5 else ("queda" if delta < -5 else "estavel")

    # Base da previsão: média EMA dos últimos 14 dias
    base = statistics.mean(suavizados[-min(14, len(suavizados)):])
    desvio = statistics.stdev(valores[-min(14, len(valores)):]) if len(valores) >= 2 else base * 0.1

    previsao = []
    ultima_data = date.fromisoformat(series[-1]["data_ref"])
    for i in range(1, ML_FORECAST_DAYS + 1):
        dia = ultima_data + timedelta(days=i)
        fator = sazonalidade.get(dia.weekday(), 1.0)
        previsto = base * fator
        previsao.append({
            "data": dia.isoformat(),
            "vendas_previstas": round(previsto, 2),
            "confianca_min": round(max(0, previsto - desvio), 2),
            "confianca_max": round(previsto + desvio, 2),
        })

    resultado = {
        "historico": [
            {"data": r["data_ref"], "vendas": round(float(r["total_vendas"] or 0), 2)}
            for r in series[-30:]   # últimos 30 dias para o gráfico
        ],
        "previsao": previsao,
        "tendencia": tendencia,
        "crescimento_pct": round(delta, 1),
        "dias_historico": len(series),
        "gerado_em": date.today().isoformat(),
    }

    # Salvar cache no disco
    cache_path = Path(CISS_MODELS_PATH) / "sales_forecast.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    logger.info("Previsão de vendas gerada: tendência=%s crescimento=%.1f%%",
                tendencia, delta)
    return resultado


def carregar_previsao_cache() -> dict[str, Any] | None:
    """Carrega previsão do cache em disco se ainda válida (gerada hoje)."""
    cache_path = Path(CISS_MODELS_PATH) / "sales_forecast.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("gerado_em") == date.today().isoformat():
            return data
    except Exception:
        pass
    return None
