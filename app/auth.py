# -*- coding: utf-8 -*-
"""
Controle de acesso via Supabase Auth — mesmo mecanismo usado no RADAR
(Authentication > Users do projeto Supabase, sign-in por e-mail/senha).

Decisao (18/09/2026): o app fica PUBLICO no Streamlit Community Cloud
(repo publico no GitHub, sem limite de apps), e quem controla quem entra
e' o login aqui, nao a visibilidade do repo. So valida credencial — a
leitura/gravacao de dados continua via DATABASE_URL/psycopg2 (role
egc_app), sem mudanca de arquitetura.

Precisa de dois secrets novos (Settings -> Secrets no Streamlit Cloud, ou
.streamlit/secrets.toml local): SUPABASE_URL e SUPABASE_ANON_KEY (a
chave "anon"/"public", NUNCA a "service_role") — mesmo projeto Supabase
usado pelo DATABASE_URL (xnfmfxsijgcrthcmxreh). Se o RADAR ja usa login
Supabase Auth nesse mesmo projeto, sao os MESMOS dois valores — nao
precisa gerar nada novo, so reaproveitar.

Nota de seguranca: como e' o mesmo projeto Supabase do RADAR, qualquer
usuario cadastrado em Authentication > Users la' tambem consegue logar
aqui (o pool de usuarios e' do projeto, nao do app). Se isso nao for
desejado, a forma de restringir e' checar o dominio do e-mail ou um
campo em user_metadata dentro de require_login() — nao implementado
ainda, decisao em aberto.
"""
from __future__ import annotations

import streamlit as st
from supabase import create_client


def _config(chave: str) -> str:
    try:
        if chave in st.secrets:
            return st.secrets[chave]
    except Exception:
        pass
    raise KeyError(chave)


@st.cache_resource(show_spinner=False)
def _auth_client():
    try:
        url = _config("SUPABASE_URL")
        anon_key = _config("SUPABASE_ANON_KEY")
    except KeyError as exc:
        st.error(
            f"Secret `{exc.args[0]}` não configurado ainda. Configure em "
            "`.streamlit/secrets.toml` (local) ou em Settings → Secrets "
            "(Streamlit Cloud) — use a chave `anon`/`public`, NUNCA a `service_role`."
        )
        st.stop()
        return None  # inalcancavel
    return create_client(url, anon_key)


def require_login() -> str:
    """
    Bloqueia a tela ate ter login valido (st.stop() se nao autenticado).
    Chamar logo apos st.set_page_config() no TOPO de app.py e de CADA
    pagina em app/pages/ — o Streamlit reroda cada pagina do zero a cada
    navegacao, st.session_state e' a unica coisa que persiste entre elas.

    Retorna o e-mail do usuario autenticado, usado como identificacao na
    trilha de auditoria das correcoes manuais (substitui o campo de nome
    livre que existia antes — mais confiavel, nao da pra digitar nome de
    outra pessoa).
    """
    if "auth_session" not in st.session_state:
        st.session_state["auth_session"] = None

    if st.session_state["auth_session"] is None:
        client = _auth_client()
        st.title("EGC — Gestão Contábil EnerMais")
        st.subheader("Login")
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            try:
                resp = client.auth.sign_in_with_password({"email": email, "password": senha})
                st.session_state["auth_session"] = resp.session
                st.rerun()
            except Exception as exc:
                st.error(f"Login inválido: {exc}")
        st.stop()
        return ""  # inalcancavel

    sessao = st.session_state["auth_session"]
    st.sidebar.caption(f"Logado como **{sessao.user.email}**")
    if st.sidebar.button("Sair"):
        st.session_state["auth_session"] = None
        st.rerun()

    return sessao.user.email
