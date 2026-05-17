"""
ciss_analytics — Pacote de Analytics e ML para dados do sistema CISS Web.

Módulos:
    config          — Configurações e variáveis de ambiente
    ciss_client     — Cliente HTTP para a API REST do CISS
    extractor       — Extração de dados de cada endpoint CISS
    transformer     — Normalização e limpeza dos dados
    loader          — Persistência no Data Warehouse SQLite
    schema          — Modelos SQLAlchemy do DW
    scheduler       — Agendamento automático do ETL
    ml/             — Modelos de Machine Learning
        sales_forecaster
        customer_segmentation
        anomaly_detector
        stock_alert
        insight_engine
"""
