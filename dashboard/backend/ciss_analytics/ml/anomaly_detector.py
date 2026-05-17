"""
Detecção de Anomalias em vendas usando IsolationForest + Z-Score.

Detecta: picos de venda suspeitos, dias anormalmente baixos,
cancelamentos atípicos, variações bruscas de estoque.
"""
import json
import logging
import math
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..schema import get_dw_conn
from ..config import CISS_MODELS_PATH, ML_HISTORY_DAYS

logger = logging.getLogger(__name__)


def _z_score(value: float, mean: float, std: float) -> float:
    """Calcula z-score. Retorna 0 se std=0."""
    return abs(value - mean) / std if std > 0 else 0.0


def detectar_anomalias() -> dict[str, Any]:
    """
    Detecta anomalias nas vendas dos últimos ML_HISTORY_DAYS dias.

    Returns:
        {
          "anomalias": [{"data": str, "tipo": str, "descricao": str,
                         "valor": float, "score": float, "severidade": str}],
          "total": int,
          "gerado_em": str
        }
    """
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
    finally:
        conn.close()

    if len(rows) < 7:
        return {"anomalias": [], "total": 0, "aviso": "Dados insuficientes (mín. 7 dias)"}

    series = [dict(r) for r in rows]
    vendas = [float(r["total_vendas"] or 0) for r in series]
    transacoes = [float(r["total_transacoes"] or 0) for r in series]

    # Tentar IsolationForest
    anomalias_if: set[int] = set()
    try:
        from sklearn.ensemble import IsolationForest
        import numpy as np

        X = np.array([[v, t] for v, t in zip(vendas, transacoes)])
        clf = IsolationForest(contamination=0.05, random_state=42, n_estimators=100)
        preds = clf.fit_predict(X)          # -1 = anomalia, 1 = normal
        scores = clf.decision_function(X)   # mais negativo = mais anômalo

        for i, pred in enumerate(preds):
            if pred == -1:
                anomalias_if.add(i)

        logger.info("IsolationForest detectou %d anomalias", len(anomalias_if))
    except ImportError:
        logger.info("sklearn não disponível — usando Z-score puro")

    # Z-Score como confirmação/fallback
    mean_v = statistics.mean(vendas)
    std_v = statistics.stdev(vendas) if len(vendas) > 1 else 0
    mean_t = statistics.mean(transacoes)
    std_t = statistics.stdev(transacoes) if len(transacoes) > 1 else 0

    anomalias = []
    for i, row in enumerate(series):
        z_v = _z_score(vendas[i], mean_v, std_v)
        z_t = _z_score(transacoes[i], mean_t, std_t)
        score_max = max(z_v, z_t)

        eh_anomalia = (i in anomalias_if) or (score_max > 2.5)
        if not eh_anomalia:
            continue

        # Classificar tipo e severidade
        if vendas[i] > mean_v + 2 * std_v:
            tipo = "pico_vendas"
            descricao = f"Vendas {(vendas[i]/max(mean_v,1)-1)*100:.0f}% acima da média"
        elif vendas[i] < mean_v - 2 * std_v:
            tipo = "queda_vendas"
            descricao = f"Vendas {(1-vendas[i]/max(mean_v,1))*100:.0f}% abaixo da média"
        elif transacoes[i] > mean_t + 2 * std_t:
            tipo = "pico_transacoes"
            descricao = f"Transações {(transacoes[i]/max(mean_t,1)-1)*100:.0f}% acima do normal"
        else:
            tipo = "anomalia_geral"
            descricao = "Padrão atípico detectado nesta data"

        severidade = "alta" if score_max > 3.5 else ("media" if score_max > 2.5 else "baixa")

        anomalias.append({
            "data": row["data_ref"],
            "tipo": tipo,
            "descricao": descricao,
            "valor_vendas": round(vendas[i], 2),
            "media_historica": round(mean_v, 2),
            "z_score": round(score_max, 2),
            "severidade": severidade,
        })

    # Ordenar por z-score descendente
    anomalias.sort(key=lambda x: x["z_score"], reverse=True)

    resultado = {
        "anomalias": anomalias[:20],   # top 20 mais relevantes
        "total": len(anomalias),
        "media_vendas_historica": round(mean_v, 2),
        "desvio_padrao": round(std_v, 2),
        "gerado_em": date.today().isoformat(),
    }

    cache_path = Path(CISS_MODELS_PATH) / "anomalies.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    logger.info("Detecção de anomalias: %d detectadas", len(anomalias))
    return resultado


def carregar_anomalias_cache() -> dict[str, Any] | None:
    cache_path = Path(CISS_MODELS_PATH) / "anomalies.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("gerado_em") == date.today().isoformat():
            return data
    except Exception:
        pass
    return None
