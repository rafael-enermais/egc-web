# -*- coding: utf-8 -*-
"""
Tela ASSISTENTE — Dashboard + Chat lado a lado, 6o (ultimo) item da fila
combinada com Rafael (Visao Grupo -> rodape -> Dashboard de Projecao ->
chat estilo TIA.go/Viaj.ai). Layout escolhido por ele em 22/09/2026
(AskUserQuestion): "Dashboard + chat lado a lado", replicando o layout de
$HOME/mnt/Projetos/TIA.go/arquivos/app_tiago.py (app de referencia dele,
ja em producao) -- nao uma pagina so' de chat.

Escopo do chat (combinado com Rafael 22/09/2026): so' BP/DRE (1 empresa)
+ Visao Grupo (consolidado) -- Dashboard de Projecao FICA DE FORA de
proposito (ver docstring de app/chat_egc.py e app/consultas_chat.py pro
motivo: metodo estatistico hoje e' basico demais com so' 1 periodo real
de historico pra virar "previsao respondida pelo chat" sem enganar).

Coluna esquerda (dashboard): consulta rapida de BP/DRE de 1 empresa —
REUSA consultas_chat.consultar_bp_dre (a MESMA funcao que o chat chama
como ferramenta), sem logica duplicada. Quando o chat acabou de fazer
alguma consulta, a ULTIMA aparece tambem aqui em cima (mesma ideia do
dash_extra reativo do app_tiago.py, so' que sem grafico Plotly — o
volume de dado do EGC ainda e' pequeno demais (poucos periodos) pra um
grafico valer a pena; se crescer, dá pra evoluir depois, sem precisar
reescrever nada disso).

Coluna direita (chat): mesmo padrao de 2 fases do app_tiago.py (grava a
pergunta + rerun; processa na recarga seguinte, sem pergunta pendente) —
evita o bug de ordem ja documentado la (nunca renderiza a mensagem nova
"na mao" fora do loop). client Anthropic so' e' criado se o secret
ANTHROPIC_API_KEY estiver configurado (Rafael configura direto no
Streamlit Cloud, nunca passado pra mim em chat) -- sem ele, a tela
funciona normal (dashboard funciona), so' o chat fica desabilitado com
aviso.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import chat_egc  # noqa: E402
import consultas_chat  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}
EMPRESAS_CODIGOS = [cod for cod, _nome, _cnpj in EMPRESAS_FIXAS]

st.title("🤖 Assistente EGC")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "Pergunte sobre BP, DRE ou Visão Grupo das 6 empresas — o assistente só responde "
    "com dado que já está importado no sistema, nunca inventa número."
)


def _cliente_anthropic():
    """
    None se o secret nao estiver configurado ainda (Rafael configura
    direto no Streamlit Cloud) -- mesmo padrao defensivo de
    conexao.get_conn() pro DATABASE_URL, so' que aqui NAO trava a tela
    com st.stop() (o dashboard da esquerda funciona sem chat).
    """
    try:
        chave = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return None
    if not chave:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=chave)


col_dash, col_chat = st.columns([3, 2])

# ─────────────────────────────────────────────
#  COLUNA ESQUERDA — dashboard
# ─────────────────────────────────────────────
with col_dash:
    st.subheader("Dashboard")

    ultima = st.session_state.get("assistente_ultima_ferramenta")
    if ultima:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**Última consulta do chat** — `{ultima['nome']}`")
            if c2.button("✕", key="assistente_limpar_ultima", help="Limpar"):
                st.session_state.assistente_ultima_ferramenta = None
                st.rerun()
            resultado = ultima["resultado"]
            if "erro" in resultado:
                st.warning(resultado["erro"])
                if resultado.get("periodos_disponiveis"):
                    st.caption("Períodos disponíveis: " + ", ".join(resultado["periodos_disponiveis"]))
            elif "contas" in resultado and resultado["contas"]:
                st.dataframe(pd.DataFrame(resultado["contas"]), hide_index=True, use_container_width=True)
            elif "periodos_por_empresa" in resultado:
                st.json(resultado["periodos_por_empresa"])
            else:
                st.caption("Sem dado pra exibir dessa consulta.")
        st.divider()

    st.markdown("**Consulta rápida**")
    nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
    idx_empresa = st.selectbox(
        "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="assistente_empresa_sel",
    )
    cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx_empresa]
    tipo_sel = st.radio("Tipo", ["BP", "DRE"], key="assistente_tipo_sel", horizontal=True)

    resultado_rapido = consultas_chat.consultar_bp_dre(conn, cod_empresa, tipo_sel)
    if "erro" in resultado_rapido:
        st.info(resultado_rapido["erro"])
    else:
        st.caption(f"{nome_empresa} · {resultado_rapido['periodo']} · {resultado_rapido['quantidade_contas']} conta(s)")
        st.dataframe(
            pd.DataFrame(resultado_rapido["contas"]),
            column_config={"valor": st.column_config.NumberColumn("Valor", format="R$ %.2f")},
            hide_index=True, use_container_width=True,
        )

# ─────────────────────────────────────────────
#  COLUNA DIREITA — chat
# ─────────────────────────────────────────────
with col_chat:
    st.subheader("Chat")

    client = _cliente_anthropic()
    if client is None:
        st.warning(
            "Secret `ANTHROPIC_API_KEY` não configurado ainda. Configure em "
            "Settings → Secrets (Streamlit Cloud) pra habilitar o chat — o "
            "dashboard ao lado funciona normalmente sem isso."
        )
        st.stop()

    if "assistente_mensagens" not in st.session_state:
        st.session_state.assistente_mensagens = []

    historico_box = st.container(height=440)
    with historico_box:
        for m in st.session_state.assistente_mensagens:
            with st.chat_message(m["role"]):
                st.markdown(m["content"].replace("$", "\\$"))  # $ quebra Markdown/LaTeX -- so' na exibicao, nao no historico guardado
    pergunta = st.chat_input("Pergunte sobre BP, DRE ou Visão Grupo...")

    # Padrao de 2 fases (mesmo do app_tiago.py -- evita bug de ordem
    # documentado la): fase 1 so' grava a pergunta e recarrega; fase 2
    # roda na recarga seguinte, sem pergunta nova pendente.
    if pergunta:
        st.session_state.assistente_mensagens.append({"role": "user", "content": pergunta})
        st.rerun()

    if st.session_state.assistente_mensagens and st.session_state.assistente_mensagens[-1]["role"] == "user":
        with st.spinner("Consultando..."):
            system_prompt = chat_egc.montar_system_prompt(usuario, EMPRESAS_CODIGOS)
            r = chat_egc.responder(
                client, conn, st.session_state.assistente_mensagens, system_prompt, EMPRESAS_CODIGOS,
            )
        st.session_state.assistente_mensagens.append({"role": "assistant", "content": r["texto"]})
        if r["ferramentas_usadas"]:
            st.session_state.assistente_ultima_ferramenta = r["ferramentas_usadas"][-1]
        st.rerun()
