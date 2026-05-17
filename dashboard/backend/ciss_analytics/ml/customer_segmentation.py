"""
Segmentação de Clientes por RFM (Recência, Frequência, Monetário).

Usa KMeans do scikit-learn quando disponível. Fallback para segmentação
por regras (quartis) quando scikit-learn não está instalado.
"""
import json
import logging
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..schema import get_dw_conn
from ..config import CISS_MODELS_PATH, ML_HISTORY_DAYS

logger = logging.getLogger(__name__)

# Definição dos segmentos por nome
_SEGMENTOS = {
    "campiao":       "🏆 Campeão",
    "leal":          "⭐ Cliente Leal",
    "potencial":     "🌱 Alto Potencial",
    "em_risco":      "⚠️ Em Risco",
    "perdido":       "💤 Inativo",
    "novo":          "🆕 Novo Cliente",
}


def _calcular_rfm_raw() -> list[dict[str, Any]]:
    """Calcula scores RFM brutos para todos os clientes ativos."""
    conn = get_dw_conn()
    try:
        cutoff = (date.today() - timedelta(days=ML_HISTORY_DAYS)).isoformat()
        hoje = date.today().isoformat()
        
        # Cross-dialect logic for days difference
        if conn.dialect_name == "postgresql":
            recencia_sql = f"CAST('{hoje}'::date - MAX(v.data_venda)::date AS INTEGER)"
        else:
            recencia_sql = f"CAST(julianday('{hoje}') - julianday(MAX(v.data_venda)) AS INTEGER)"

        rows = conn.execute(
            f"""
            SELECT
                v.id_cliente,
                c.nome,
                c.cidade,
                c.estado,
                -- Recência: dias desde última compra
                {recencia_sql} AS recencia,
                -- Frequência: número de compras distintas
                COUNT(DISTINCT v.id_venda)      AS frequencia,
                -- Monetário: total gasto
                SUM(v.total_liquido)            AS monetario,
                MAX(v.data_venda)               AS ultima_compra,
                MIN(v.data_venda)               AS primeira_compra
            FROM dw_vendas v
            LEFT JOIN dw_clientes c ON c.id_cliente = v.id_cliente
            WHERE v.data_venda >= '{cutoff}'
              AND v.status != 'cancelada'
              AND v.id_cliente != ''
            GROUP BY v.id_cliente, c.nome, c.cidade, c.estado
            HAVING COUNT(DISTINCT v.id_venda) > 0
            ORDER BY monetario DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _quartil_score(valor: float, todos: list[float], invertido: bool = False) -> int:
    """
    Classifica valor em quartil (1-4). invertido=True para recência
    (menor recência = melhor = score 4).
    """
    if not todos:
        return 2
    sorted_vals = sorted(todos)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q2 = sorted_vals[n // 2]
    q3 = sorted_vals[min(3 * n // 4, n - 1)]

    if invertido:
        if valor <= q1:
            return 4
        if valor <= q2:
            return 3
        if valor <= q3:
            return 2
        return 1
    else:
        if valor >= q3:
            return 4
        if valor >= q2:
            return 3
        if valor >= q1:
            return 2
        return 1


def _classificar_segmento(r: int, f: int, m: int) -> str:
    """Classifica cliente em segmento baseado nos scores RFM (1-4 cada)."""
    score = r + f + m

    if r >= 4 and f >= 4 and m >= 4:
        return "campiao"
    if r >= 3 and f >= 3:
        return "leal"
    if f >= 3 and m >= 3 and r <= 2:
        return "em_risco"
    if r >= 3 and f <= 2:
        return "novo"
    if score >= 9:
        return "potencial"
    if r <= 1 and f <= 1:
        return "perdido"
    return "potencial"


def segmentar_clientes() -> dict[str, Any]:
    """
    Executa segmentação RFM completa e atualiza o DW.

    Returns:
        {
          "total_clientes": int,
          "segmentos": {nome: {"count": int, "total_vendas": float}},
          "top_clientes": [lista dos top 20 por valor],
          "gerado_em": str
        }
    """
    clientes_rfm = _calcular_rfm_raw()
    if not clientes_rfm:
        return {"erro": "Sem dados de clientes para segmentar", "segmentos": {}}

    recencias   = [c["recencia"]   for c in clientes_rfm]
    frequencias = [c["frequencia"] for c in clientes_rfm]
    monetarios  = [c["monetario"]  for c in clientes_rfm]

    # Tentar usar KMeans via scikit-learn
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        import numpy as np

        X = np.array([[c["recencia"], c["frequencia"], c["monetario"]] for c in clientes_rfm])
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        k = min(5, len(clientes_rfm))
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(X_scaled)

        # Mapear clusters para nomes de segmento baseado nas médias
        cluster_stats = {}
        for i in range(k):
            mask = labels == i
            cluster_stats[i] = {
                "r": float(np.mean(X[mask, 0])),
                "f": float(np.mean(X[mask, 1])),
                "m": float(np.mean(X[mask, 2])),
                "count": int(mask.sum()),
            }

        # Ordenar por monetário decrescente e atribuir segmento
        sorted_clusters = sorted(cluster_stats.items(), key=lambda x: x[1]["m"], reverse=True)
        seg_names = ["campiao", "leal", "potencial", "em_risco", "perdido"]
        cluster_to_seg = {c[0]: seg_names[min(i, len(seg_names)-1)]
                          for i, c in enumerate(sorted_clusters)}

        for i, c in enumerate(clientes_rfm):
            c["rfm_segmento"] = cluster_to_seg.get(labels[i], "potencial")
            c["rfm_r"] = int(_quartil_score(c["recencia"], recencias, invertido=True))
            c["rfm_f"] = int(_quartil_score(c["frequencia"], frequencias))
            c["rfm_m"] = int(_quartil_score(c["monetario"], monetarios))

        logger.info("Segmentação KMeans concluída para %d clientes", len(clientes_rfm))

    except ImportError:
        # Fallback: segmentação por quartis (sem sklearn)
        logger.info("scikit-learn não disponível — usando segmentação por quartis")
        for c in clientes_rfm:
            r = _quartil_score(c["recencia"], recencias, invertido=True)
            f = _quartil_score(c["frequencia"], frequencias)
            m = _quartil_score(c["monetario"], monetarios)
            c["rfm_r"] = r
            c["rfm_f"] = f
            c["rfm_m"] = m
            c["rfm_segmento"] = _classificar_segmento(r, f, m)

    # Atualizar DW com segmentos
    conn = get_dw_conn()
    try:
        for c in clientes_rfm:
            conn.execute(
                """
                UPDATE dw_clientes SET
                    rfm_recencia = ?,
                    rfm_frequencia = ?,
                    rfm_monetario = ?,
                    rfm_segmento = ?,
                    rfm_atualizado = datetime('now')
                WHERE id_cliente = ?
                """,
                (
                    c.get("recencia"),
                    c.get("frequencia"),
                    c.get("monetario"),
                    c.get("rfm_segmento"),
                    c["id_cliente"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Agregar resultado por segmento
    contagem: dict[str, dict] = {}
    for c in clientes_rfm:
        seg = c.get("rfm_segmento", "potencial")
        if seg not in contagem:
            contagem[seg] = {"count": 0, "total_vendas": 0.0, "nome_display": _SEGMENTOS.get(seg, seg)}
        contagem[seg]["count"] += 1
        contagem[seg]["total_vendas"] += float(c.get("monetario") or 0)

    top_clientes = sorted(clientes_rfm, key=lambda x: x.get("monetario") or 0, reverse=True)[:20]

    resultado = {
        "total_clientes": len(clientes_rfm),
        "segmentos": contagem,
        "top_clientes": [
            {
                "id_cliente": c["id_cliente"],
                "nome": c.get("nome", ""),
                "cidade": c.get("cidade", ""),
                "estado": c.get("estado", ""),
                "total_compras": round(float(c.get("monetario") or 0), 2),
                "frequencia": c.get("frequencia", 0),
                "ultima_compra": c.get("ultima_compra", ""),
                "segmento": _SEGMENTOS.get(c.get("rfm_segmento", ""), ""),
            }
            for c in top_clientes
        ],
        "gerado_em": date.today().isoformat(),
    }

    cache_path = Path(CISS_MODELS_PATH) / "customer_segments.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    logger.info("Segmentação RFM concluída: %d clientes, %d segmentos",
                len(clientes_rfm), len(contagem))
    return resultado


def carregar_segmentos_cache() -> dict[str, Any] | None:
    """Carrega segmentos do cache em disco se ainda válido (gerado hoje)."""
    cache_path = Path(CISS_MODELS_PATH) / "customer_segments.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("gerado_em") == date.today().isoformat():
            return data
    except Exception:
        pass
    return None
