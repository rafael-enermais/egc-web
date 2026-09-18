# -*- coding: utf-8 -*-
"""
Tela ARQUIVAR / RECUPERAR — equivalente a ArquivarImportacao/
RecuperarImportacao do VBA (PROJETO_EGC_v3.0.md secao 8).

Regra preservada: os dois botões só ALTERNAM o status (ATIVO <-> INATIVO)
da empresa+período escolhido — nunca apagam nada. "Desfazer última
importação" foi removido do sistema antigo por decisão de segurança
(risco de exclusão permanente) e não existe aqui também, de propósito.

Melhoria em relação ao VBA: como aqui pdf_original vive na mesma linha
de egc.lancamentos (não numa aba separada), um ciclo Arquivar -> Recuperar
NUNCA perde o valor original do PDF, mesmo em período com correção manual
— a limitação conhecida do sistema antigo (seção 7 do handoff) não existe
nesta versão.
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conexao import sidebar_contexto, get_conn  # noqa: E402
import db  # noqa: E402

st.set_page_config(page_title="Arquivar/Recuperar — EGC", page_icon="🗄️", layout="wide")
st.title("🗄️ Arquivar / Recuperar importação")

cod_empresa, nome_empresa, usuario = sidebar_contexto()
conn = get_conn()

col_arq, col_rec = st.columns(2)

with col_arq:
    st.subheader("Arquivar")
    st.caption(f"Períodos ATIVOS de **{nome_empresa}** — inativa (não apaga).")
    ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
    if not ativos:
        st.info("Nenhum período ativo.")
    else:
        escolhidos = st.multiselect(
            "Selecione o(s) período(s) para arquivar",
            ativos,
            format_func=lambda d: d.strftime("%m/%Y"),
            key="arquivar_sel",
        )
        if escolhidos and st.button("📦 Arquivar selecionado(s)", type="primary"):
            total = 0
            for p in escolhidos:
                total += db.arquivar_periodo(conn, cod_empresa, p)
            st.success(f"{total} lançamento(s) arquivado(s) em {len(escolhidos)} período(s).")
            st.rerun()

with col_rec:
    st.subheader("Recuperar")
    st.caption(f"Períodos ARQUIVADOS de **{nome_empresa}** — reativa.")
    inativos = db.listar_periodos(conn, cod_empresa, status="INATIVO")
    if not inativos:
        st.info("Nenhum período arquivado.")
    else:
        escolhidos_r = st.multiselect(
            "Selecione o(s) período(s) para recuperar",
            inativos,
            format_func=lambda d: d.strftime("%m/%Y"),
            key="recuperar_sel",
        )
        if escolhidos_r and st.button("♻️ Recuperar selecionado(s)", type="primary"):
            total = 0
            for p in escolhidos_r:
                total += db.recuperar_periodo(conn, cod_empresa, p)
            st.success(f"{total} lançamento(s) recuperado(s) em {len(escolhidos_r)} período(s).")
            st.rerun()
