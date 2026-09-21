# -*- coding: utf-8 -*-
"""
Conexao com o Supabase (schema `egc`) + estado compartilhado (empresa
selecionada, usuario) usado por app.py e por todas as paginas em
app/pages/. Centralizado aqui pra nao duplicar em cada pagina.
"""
from __future__ import annotations

import streamlit as st
import psycopg2

APP_VERSION = "0.7"  # bump: decimo em manutencao/fix, inteiro em evolucao estrutural (regra do Rafael)

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


def sidebar_contexto(usuario_logado: str):
    """
    Sidebar comum a todas as paginas: selecao de empresa (equivalente aos
    6 checkboxes do PAINEL). Identificacao do usuario NAO e' mais campo
    livre — vem do login (Supabase Auth, ver auth.require_login()),
    chamado antes desta funcao em cada pagina. Isso fecha a trilha de
    auditoria: nao da mais pra digitar o nome de outra pessoa.
    Retorna (codigo_empresa, nome_empresa, usuario_logado).
    """
    st.sidebar.header("Contexto")

    nomes = [f"{nome} ({cod})" for cod, nome, _ in EMPRESAS_FIXAS]
    idx = st.sidebar.selectbox("Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes[i])
    cod, nome, cnpj = EMPRESAS_FIXAS[idx]
    st.sidebar.caption(cnpj)

    # Rodape (contato + versao) -- mesmo padrao de conteudo do RADAR
    # (rafael.nakahara@enermais.com.br + v{VERSAO}), so que aqui na sidebar
    # (nao no fim do conteudo principal) porque o app e multipage e o
    # conteudo principal muda de tela; a sidebar e o unico lugar comum a
    # todas as telas. "position: fixed" foi evitado de proposito -- o
    # RADAR ja descobriu que o Streamlit corta isso (fica relativo a um
    # container interno, nao a janela), entao aqui tambem e' bloco normal
    # que flui no fim do conteudo da sidebar.
    st.sidebar.markdown(
        f"""
        <div style="margin-top: 2rem; padding-top: 0.6rem;
                    border-top: 1px solid rgba(245,246,250,0.15);
                    font-size: 0.7rem; color: rgba(245,246,250,0.5);
                    line-height: 1.4;">
            EGC — Gestão Contábil EnerMais · v{APP_VERSION}<br>
            rafael.nakahara@enermais.com.br
        </div>
        """,
        unsafe_allow_html=True,
    )

    return cod, nome, usuario_logado
