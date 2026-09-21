# -*- coding: utf-8 -*-
"""
EGC — Gestao Contabil EnerMais (v0.1 web)
Ponto de entrada Streamlit.

Fix (21/09/2026): antes disso, as paginas em app/pages/ eram descobertas
automaticamente pelo Streamlit (convencao de pasta) e apareciam no menu
lateral MESMO pra quem nao tinha logado ainda (o menu e' desenhado pelo
framework antes do require_login() de cada pagina rodar) — so' o CONTEUDO
ficava bloqueado, nao a lista de paginas. Trocado pra st.navigation()/
st.Page(): as paginas so' sao registradas (e so' entao aparecem no menu)
DEPOIS que require_login() confirma a sessao, replicando o padrao "login
-> app completo aparece" dos outros apps (RADAR etc.), que nao tem esse
vazamento por serem single-page.
"""
import streamlit as st

from auth import require_login
from conexao import sidebar_contexto, get_conn
import db

st.set_page_config(page_title="EGC — EnerMais", page_icon="📊", layout="wide")

# Bloqueia aqui (st.stop() dentro de require_login) se nao autenticado.
# st.navigation() so' e' chamado DEPOIS desta linha — por isso a lista de
# paginas nunca existe pra quem nao passou do login. O e-mail retornado e'
# reaproveitado dentro de pagina_inicio() (nao chama require_login() de
# novo la' — isso duplicava o "Logado como" na sidebar).
usuario_logado = require_login()


def pagina_inicio():
    usuario = usuario_logado

    st.title("EGC — Gestão Contábil EnerMais")
    st.caption("Importação de PDF, revisão/correção, histórico e relatórios — schema `egc` no Supabase.")

    cod_empresa, nome_empresa, usuario = sidebar_contexto(usuario)

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


paginas = [
    st.Page(pagina_inicio, title="Início", icon="📊", default=True, url_path="inicio"),
    st.Page("pages/1_Importar_PDF.py", title="Importar PDF", icon="📥"),
    st.Page("pages/2_Revisao_Correcao.py", title="Revisão/Correção", icon="✏️"),
    st.Page("pages/3_Arquivar_Recuperar.py", title="Arquivar/Recuperar", icon="🗄️"),
]
pg = st.navigation(paginas)
pg.run()
