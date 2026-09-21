# -*- coding: utf-8 -*-
"""
Conexao com o Supabase (schema `egc`) + estado compartilhado (empresa
selecionada, usuario) usado por app.py e por todas as paginas em
app/pages/. Centralizado aqui pra nao duplicar em cada pagina.
"""
from __future__ import annotations

import streamlit as st
import psycopg2

# Esquema de versao esclarecido pelo Rafael em 21/09/2026: enquanto o app
# nao tiver o "lancamento oficial" (1a entrega formal pra contadora usar
# de verdade), a versao fica 0.MAJOR.MINOR -- so' depois do lancamento
# vira 1.algo. Dentro do "0.", MAJOR sobe em evolucao estrutural (nova
# logica/funcao), MINOR sobe em manutencao/fix dentro da mesma estrutura
# (mesma regra de sempre, so' que agora com 1 casa a mais de folga antes
# do "1.0" oficial). Reiniciado em 0.1.0 nesta mudanca (era "0.7"/"0.8"/
# "0.9" de 1 casa so').
APP_VERSION = "0.1.0"

EMPRESAS_FIXAS = [
    ("ENERGIA", "Enermais Energia Ltda", "47.040.664/0001-48"),
    ("SMG", "SMG Solucoes Ltda", "18.387.666/0001-00"),
    ("ENG", "Enermais Engenharia Ltda", "50.337.899/0001-00"),
    ("RENOV", "Enermais Renovaveis Ltda", "51.671.106/0001-58"),
    ("CONST", "Enermais Construtora Ltda", "55.244.465/0001-80"),
    ("SOL", "Enermais Solucoes Ltda", "60.353.219/0001-04"),
]

def empresa_por_cnpj(cnpj: str) -> tuple[str, str] | None:
    """
    Resolve (codigo, nome) a partir do CNPJ detectado pelo parser num PDF.
    None se o CNPJ nao bater com nenhuma das 6 empresas cadastradas (ex.:
    "Futuro" ou qualquer CNPJ fora do grupo). Usado no Importar PDF pra
    identificar a empresa automaticamente por arquivo, em vez de depender
    de selecao manual previa na sidebar (pedido do Rafael 21/09/2026: com
    upload de PDFs de varios CNPJs de uma vez, escolher 1 empresa antes na
    sidebar nao faz sentido -- quem sabe a empresa certa e' o proprio PDF).
    """
    cnpj = (cnpj or "").strip()
    if not cnpj:
        return None
    for cod, nome, c in EMPRESAS_FIXAS:
        if c == cnpj:
            return cod, nome
    return None


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


def sidebar_contexto(usuario_logado: str) -> None:
    """
    Sidebar comum a todas as paginas: so' o rodape (contato + versao) por
    enquanto. Identificacao do usuario NAO e' campo livre — vem do login
    (Supabase Auth, ver auth.require_login()), chamado antes desta funcao
    em cada pagina.

    Mudanca de 21/09/2026 (feedback do Rafael: "esse dropdown do lado
    esquerdo, ele interfere em tudo... acredito q por pagina poderia
    definir as opcoes do dropdown, sem depender desse ao lado esquerdo"):
    o seletor de Empresa que existia AQUI foi removido. Antes, cada pagina
    lia (ou tentava ler, ou parcialmente lia) esse dropdown compartilhado
    da sidebar pra decidir o que mostrar -- isso causava efeito colateral
    real entre paginas (mudar a empresa aqui mexia sem querer no default
    do seletor proprio da Revisao/Correcao, por exemplo, porque o default
    dela dependia deste valor). Agora CADA pagina que precisa de uma
    empresa tem seu PROPRIO seletor local, sem nenhuma dependencia
    cruzada: app.py (pagina Inicio), telas/2_Revisao_Correcao.py e
    telas/3_Arquivar_Recuperar.py. Importar PDF nunca dependeu disso (a
    empresa e' resolvida por CNPJ, por arquivo).

    Nao retorna mais nada (antes retornava cod_empresa/nome_empresa —
    ninguem mais deve usar o retorno desta funcao pra decidir empresa).
    """
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
