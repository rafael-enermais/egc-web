# -*- coding: utf-8 -*-
"""
Conexao com o Supabase (schema `egc`) + estado compartilhado (empresa
selecionada, usuario) usado por app.py e por todas as paginas em
app/pages/. Centralizado aqui pra nao duplicar em cada pagina.
"""
from __future__ import annotations

import streamlit as st
import psycopg2

EMPRESAS_FIXAS = [
    ("ENERGIA", "Enermais Energia Ltda", "47.040.664/0001-48"),
    ("SMG", "SMG Solucoes Ltda", "18.387.666/0001-00"),
    ("ENG", "Enermais Engenharia Ltda", "50.337.899/0001-00"),
    ("RENOV", "Enermais Renovaveis Ltda", "51.671.106/0001-58"),
    ("CONST", "Enermais Construtora Ltda", "55.244.465/0001-80"),
    ("SOL", "Enermais Solucoes Ltda", "60.353.219/0001-04"),
]


@st.cache_resource(show_spinner=False)
def get_conn():
    """
    Conexao cacheada pela sessao do processo Streamlit (nao por usuario —
    st.cache_resource compartilha entre sessoes, o que e o correto pra uma
    connection pool). Precisa de st.secrets['DATABASE_URL'] configurado
    (Supabase: Settings -> Database -> Connection string, modo "Session"
    ou "Transaction pooler"; usar a role egc_app, nunca postgres/service_role).
    """
    # st.secrets pode levantar StreamlitSecretNotFoundError so de TENTAR ler
    # (nao so de faltar a chave) quando nao existe nenhum secrets.toml ainda
    # (caso normal antes do primeiro deploy) — por isso o try/except cobre
    # a leitura inteira, nao so a chave, pra nunca travar a tela com
    # traceback bruto por causa de configuracao pendente.
    try:
        database_url = st.secrets["DATABASE_URL"]
    except Exception:
        st.error(
            "Secret `DATABASE_URL` não configurado ainda. Configure em "
            "`.streamlit/secrets.toml` (local) ou em Settings → Secrets "
            "(Streamlit Cloud) com a connection string do Supabase, role `egc_app`."
        )
        st.stop()
        return None  # inalcancavel — st.stop() encerra o script aqui

    try:
        conn = psycopg2.connect(database_url)
    except Exception as exc:
        st.error(f"Não consegui conectar ao banco: {exc}")
        st.stop()
        return None

    conn.autocommit = True
    return conn


def sidebar_contexto():
    """
    Sidebar comum a todas as paginas: selecao de empresa (equivalente aos
    6 checkboxes do PAINEL) + identificacao do usuario (pra trilha de
    auditoria nas correcoes manuais — o VBA nao tinha isso, o Excel era
    de uso individual; aqui vira campo simples, sem exigir login formal
    ainda — decisao de exposicao/autenticacao fica pra Fase 8).
    Retorna (codigo_empresa, nome_empresa, usuario).
    """
    st.sidebar.header("Contexto")

    if "usuario" not in st.session_state:
        st.session_state["usuario"] = ""
    st.session_state["usuario"] = st.sidebar.text_input(
        "Seu nome", value=st.session_state["usuario"], placeholder="ex.: Maria"
    )

    nomes = [f"{nome} ({cod})" for cod, nome, _ in EMPRESAS_FIXAS]
    idx = st.sidebar.selectbox("Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes[i])
    cod, nome, cnpj = EMPRESAS_FIXAS[idx]
    st.sidebar.caption(cnpj)

    usuario = st.session_state["usuario"].strip() or None
    return cod, nome, usuario
