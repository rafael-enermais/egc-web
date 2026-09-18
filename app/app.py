# -*- coding: utf-8 -*-
"""
EGC — Gestao Contabil EnerMais (v0.1 web)
Ponto de entrada Streamlit. Paginas em app/pages/:
  1_Importar_PDF.py
  2_Revisao_Correcao.py
  3_Arquivar_Recuperar.py
"""
import streamlit as st

from conexao import sidebar_contexto, get_conn
import db

st.set_page_config(page_title="EGC — EnerMais", page_icon="📊", layout="wide")

st.title("EGC — Gestão Contábil EnerMais")
st.caption("Importação de PDF, revisão/correção, histórico e relatórios — schema `egc` no Supabase.")

cod_empresa, nome_empresa, usuario = sidebar_contexto()

st.markdown(
    "Use o menu à esquerda para **Importar PDF**, **Revisão/Correção** ou "
    "**Arquivar/Recuperar**. O dashboard de indicadores entra na Fase 4."
)

try:
    conn = get_conn()
    periodos_ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
    periodos_inativos = db.listar_periodos(conn, cod_empresa, status="INATIVO")
    c1, c2 = st.columns(2)
    c1.metric(f"Períodos ativos — {nome_empresa}", len(periodos_ativos))
    c2.metric("Períodos arquivados", len(periodos_inativos))
    if periodos_ativos:
        st.caption("Últimos períodos ativos: " + ", ".join(p.strftime("%m/%Y") for p in periodos_ativos[:6]))
except Exception as exc:
    st.warning(f"Não foi possível consultar o banco ainda: {exc}")
