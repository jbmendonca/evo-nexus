import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
import os
from datetime import datetime, timedelta

# --- Configurações da Página ---
st.set_page_config(page_title="CISS Analytics | Inteligência de Varejo", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .kpi-card {
        background-color: #1E293B; border-radius: 10px; padding: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3); text-align: center; border-left: 5px solid #10B981;
    }
    .kpi-title { font-size: 1.1rem; color: #94A3B8; text-transform: uppercase; letter-spacing: 1px; }
    .kpi-val { font-size: 2.2rem; color: #F8FAFC; font-weight: bold; margin: 10px 0; }
    </style>
""", unsafe_allow_html=True)

# --- Conexão ao Banco ---
CISS_DW_DSN = os.environ.get("CISS_DW_DSN", "sqlite:////app/data/ciss_dw.db")

@st.cache_resource
def get_engine():
    return create_engine(CISS_DW_DSN, pool_pre_ping=True)

# --- Consultas de Dados (Data Engineering) ---
@st.cache_data(ttl=600)
def get_max_date():
    engine = get_engine()
    query = text("SELECT MAX(data_venda) FROM dw_vendas WHERE status != 'cancelada'")
    with engine.connect() as conn:
        res = conn.execute(query).scalar()
        if res:
            try:
                return datetime.strptime(res[:10], '%Y-%m-%d')
            except:
                pass
    return datetime.now()

@st.cache_data(ttl=600)
def load_kpis(dias):
    engine = get_engine()
    base_date = get_max_date()
    cutoff = (base_date - timedelta(days=dias)).strftime('%Y-%m-%d')
    query = text(f"""
        SELECT 
            COUNT(DISTINCT v.id_venda) as total_pedidos,
            SUM(v.total_liquido) as faturamento,
            SUM(v.num_itens) as itens_vendidos,
            COUNT(DISTINCT v.id_cliente) as clientes_unicos
        FROM dw_vendas v
        WHERE v.data_venda >= '{cutoff}' AND v.status != 'cancelada'
    """)
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn).iloc[0]

@st.cache_data(ttl=600)
def load_sales_trend(dias, granularidade='D'):
    engine = get_engine()
    base_date = get_max_date()
    cutoff = (base_date - timedelta(days=dias)).strftime('%Y-%m-%d')
    
    # Formatação de data no PostgreSQL (usamos substring para simplificar compatibilidade)
    if granularidade == 'D': # Dia
        date_expr = "SUBSTRING(v.data_venda, 1, 10)"
    elif granularidade == 'W': # Semana
        # Apenas agrupando a cada 7 dias ou usando date_trunc no postgres
        date_expr = "DATE_TRUNC('week', CAST(v.data_venda AS TIMESTAMP))" if 'postgres' in CISS_DW_DSN else "SUBSTRING(v.data_venda, 1, 10)"
    elif granularidade == 'M': # Mês
        date_expr = "SUBSTRING(v.data_venda, 1, 7)"
    else:
        date_expr = "SUBSTRING(v.data_venda, 1, 4)" # Ano

    query = text(f"""
        SELECT 
            {date_expr} as periodo,
            SUM(v.total_liquido) as faturamento,
            COUNT(DISTINCT v.id_venda) as pedidos
        FROM dw_vendas v
        WHERE v.data_venda >= '{cutoff}' AND v.status != 'cancelada'
        GROUP BY {date_expr}
        ORDER BY {date_expr}
    """)
    with engine.connect() as conn:
        df = pd.read_sql_query(query, conn)
        return df

@st.cache_data(ttl=600)
def load_top_products(dias, limit=10):
    engine = get_engine()
    base_date = get_max_date()
    cutoff = (base_date - timedelta(days=dias)).strftime('%Y-%m-%d')
    query = text(f"""
        SELECT 
            p.nome_produto,
            p.marca,
            SUM(i.quantidade) as qtd_vendida,
            SUM(i.total_item) as faturamento
        FROM dw_itens_venda i
        JOIN dw_vendas v ON v.id_venda = i.id_venda
        LEFT JOIN dw_produtos p ON i.id_produto = p.id_produto
        WHERE v.data_venda >= '{cutoff}' AND v.status != 'cancelada'
        GROUP BY p.nome_produto, p.marca
        ORDER BY faturamento DESC
        LIMIT {limit}
    """)
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)

@st.cache_data(ttl=600)
def load_top_brands(dias, limit=10):
    engine = get_engine()
    base_date = get_max_date()
    cutoff = (base_date - timedelta(days=dias)).strftime('%Y-%m-%d')
    query = text(f"""
        SELECT 
            COALESCE(NULLIF(p.marca, ''), 'SEM MARCA') as marca,
            SUM(i.total_item) as faturamento,
            SUM(i.quantidade) as qtd_vendida
        FROM dw_itens_venda i
        JOIN dw_vendas v ON v.id_venda = i.id_venda
        LEFT JOIN dw_produtos p ON i.id_produto = p.id_produto
        WHERE v.data_venda >= '{cutoff}' AND v.status != 'cancelada'
        GROUP BY COALESCE(NULLIF(p.marca, ''), 'SEM MARCA')
        ORDER BY faturamento DESC
        LIMIT {limit}
    """)
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)

@st.cache_data(ttl=600)
def load_top_sellers(dias, limit=10):
    engine = get_engine()
    base_date = get_max_date()
    cutoff = (base_date - timedelta(days=dias)).strftime('%Y-%m-%d')
    # id_vendedor nas vendas
    query = text(f"""
        SELECT 
            COALESCE(NULLIF(v.id_vendedor, ''), 'VENDEDOR_N/D') as vendedor_id,
            SUM(v.total_liquido) as faturamento,
            COUNT(DISTINCT v.id_venda) as pedidos,
            SUM(v.total_liquido) / NULLIF(COUNT(DISTINCT v.id_venda), 0) as ticket_medio
        FROM dw_vendas v
        WHERE v.data_venda >= '{cutoff}' AND v.status != 'cancelada'
        GROUP BY COALESCE(NULLIF(v.id_vendedor, ''), 'VENDEDOR_N/D')
        ORDER BY faturamento DESC
        LIMIT {limit}
    """)
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)

# --- Sidebar ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3121/3121612.png", width=80)
st.sidebar.title("Filtros Analíticos")
periodo_dias = st.sidebar.slider("Período (Dias)", 7, 365, 30)
granularidade = st.sidebar.selectbox("Agrupamento de Tempo", options=["D", "W", "M"], format_func=lambda x: {"D":"Diário", "W":"Semanal", "M":"Mensal"}[x])

st.sidebar.markdown("---")
st.sidebar.info("Dashboard Analítico de Varejo. Os dados são sincronizados do ERP CISS.")

if st.sidebar.button("🔄 Recarregar Dados"):
    st.cache_data.clear()
    st.rerun()

# --- Main Layout ---
st.title("📈 Pau Brasil - Dashboard Analítico")
st.markdown("Visão consolidada de performance de vendas e comportamento de catálogo.")

try:
    kpis = load_kpis(periodo_dias)
    faturamento = float(kpis['faturamento'] or 0)
    pedidos = int(kpis['total_pedidos'] or 0)
    ticket_medio = faturamento / pedidos if pedidos > 0 else 0
    itens = int(kpis['itens_vendidos'] or 0)

    # 1. KPIs Section
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f'<div class="kpi-card"><div class="kpi-title">Faturamento</div><div class="kpi-val">R$ {faturamento:,.2f}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="kpi-card"><div class="kpi-title">Pedidos Fechados</div><div class="kpi-val">{pedidos:,}</div></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="kpi-card"><div class="kpi-title">Ticket Médio</div><div class="kpi-val">R$ {ticket_medio:,.2f}</div></div>', unsafe_allow_html=True)
    c4.markdown(f'<div class="kpi-card"><div class="kpi-title">Itens Vendidos</div><div class="kpi-val">{itens:,}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. Séries Temporais
    df_trend = load_sales_trend(periodo_dias, granularidade)
    if not df_trend.empty:
        fig_trend = px.area(
            df_trend, x='periodo', y='faturamento', 
            title="Evolução de Faturamento",
            color_discrete_sequence=['#10B981'],
            markers=True
        )
        fig_trend.update_layout(template="plotly_dark", margin=dict(l=20, r=20, t=50, b=20))
        st.plotly_chart(fig_trend, use_container_width=True)

    # 3. Análises Aprofundadas (Data Science Views)
    st.markdown("### 🧬 Insights de Performance")
    t1, t2, t3 = st.tabs(["🏆 Produtos & Marcas", "👥 Vendedores", "🎯 Clusterização de Ticket"])

    with t1:
        colA, colB = st.columns(2)
        with colA:
            df_prod = load_top_products(periodo_dias, 10)
            if not df_prod.empty:
                df_prod['nome_produto'] = df_prod['nome_produto'].fillna('Desconhecido')
                fig_prod = px.bar(
                    df_prod.sort_values('faturamento', ascending=True), 
                    x='faturamento', y='nome_produto', 
                    title="Top 10 Produtos (Receita)",
                    orientation='h', color='faturamento', color_continuous_scale='Viridis'
                )
                fig_prod.update_layout(template="plotly_dark", showlegend=False)
                st.plotly_chart(fig_prod, use_container_width=True)
        with colB:
            df_brands = load_top_brands(periodo_dias, 10)
            if not df_brands.empty:
                fig_brands = px.pie(
                    df_brands, values='faturamento', names='marca', 
                    title="Market Share Interno: Top 10 Marcas",
                    hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig_brands.update_layout(template="plotly_dark")
                st.plotly_chart(fig_brands, use_container_width=True)

    with t2:
        df_sellers = load_top_sellers(periodo_dias, 15)
        if not df_sellers.empty:
            fig_sell = px.scatter(
                df_sellers, x='pedidos', y='faturamento', size='ticket_medio',
                color='vendedor_id', hover_name='vendedor_id',
                title="Performance da Equipe: Volume vs Receita (Bolha = Ticket Médio)",
                size_max=40
            )
            fig_sell.update_layout(template="plotly_dark")
            st.plotly_chart(fig_sell, use_container_width=True)
            
            st.dataframe(df_sellers.style.format({
                'faturamento': 'R$ {:,.2f}',
                'ticket_medio': 'R$ {:,.2f}'
            }), use_container_width=True)

    with t3:
        st.markdown("#### Distribuição de Vendas (Clusterização Visual)")
        st.info("💡 Cada ponto representa um vendedor. Observe outliers com alto ticket médio mas poucas vendas, ou vendedores de alto volume.")
        if not df_sellers.empty:
            # Simple clustering visual
            fig_cluster = px.density_contour(
                df_sellers, x="pedidos", y="ticket_medio",
                title="Densidade: Volume de Pedidos vs Ticket Médio"
            )
            fig_cluster.update_traces(contours_coloring="fill", colorscale="Teal")
            fig_cluster.update_layout(template="plotly_dark")
            st.plotly_chart(fig_cluster, use_container_width=True)

except Exception as e:
    st.error(f"Erro ao processar dados analíticos: {e}")
    st.code(str(e))
