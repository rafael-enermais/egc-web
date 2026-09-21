# -*- coding: utf-8 -*-
"""
Tela REVISAO / CORRECAO MANUAL — equivalente a REVISAO_IMPORTACAO +
SalvarCorrecoesManuais do VBA (ver PROJETO_EGC_v3.0.md secao 7).

Regras preservadas:
  - Trilha de auditoria: ao corrigir, origem vira "MANUAL <periodo>" e o
    valor ORIGINAL do PDF fica guardado em pdf_original — só na 1ª edição,
    nunca sobrescrito depois.
  - Se a conta corrigida não existir (PDF não trouxe o campo), cria linha
    nova com grupo "AJUSTE MANUAL".
  - Validação leve: valor precisa ser numérico (equivalente a
    TextoPareceValorMonetario do VBA).
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn  # noqa: E402
import db  # noqa: E402

st.title("✏️ Revisão / Correção manual")

usuario = usuario_atual()

cod_empresa, nome_empresa, usuario = sidebar_contexto(usuario)
conn = get_conn()

periodos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
if not periodos:
    st.info(f"Nenhum período ativo para **{nome_empresa}** ainda. Importe um PDF primeiro.")
    st.stop()

periodo = st.selectbox("Período", periodos, format_func=lambda d: d.strftime("%m/%Y"))
tipo = st.radio("Tipo", ["BP", "DRE"], horizontal=True)

lancamentos = db.listar_lancamentos(conn, cod_empresa, periodo, tipo)
if not lancamentos:
    st.info(f"Sem lançamentos de {tipo} para este período.")
    st.stop()

st.caption(
    "Edite a coluna **valor** e clique em Salvar. O valor original do PDF é preservado "
    "automaticamente na 1ª correção de cada conta (coluna `pdf_original`, abaixo)."
)

import pandas as pd

df = pd.DataFrame(lancamentos)
# Coluna derivada (nao vem do banco) so pra destacar visualmente o que ja
# foi corrigido a mao -- origem vira "MANUAL <periodo>" na 1a correcao
# (regra de auditoria existente, ver db.salvar_correcao_manual). data_editor
# nao aceita pandas Styler pra colorir celula, entao o destaque e' esta
# coluna de check (mais confiavel que cor, e funciona igual em qualquer tema).
df.insert(3, "corrigido", df["origem"].astype(str).str.startswith("MANUAL"))
df_edit = st.data_editor(
    df,
    column_config={
        "id": st.column_config.NumberColumn("ID", disabled=True),
        "grupo": st.column_config.TextColumn("Grupo", disabled=True),
        "conta": st.column_config.TextColumn("Conta", disabled=True),
        "corrigido": st.column_config.CheckboxColumn("✏️ Corrigido", disabled=True),
        "valor": st.column_config.NumberColumn("Valor", format="%.2f", step=0.01),
        "origem": st.column_config.TextColumn("Origem", disabled=True),
        "pdf_original": st.column_config.NumberColumn("PDF original", format="%.2f", disabled=True),
        "arquivo_pdf": st.column_config.TextColumn("Arquivo", disabled=True),
        "atualizado_em": st.column_config.DatetimeColumn("Atualizado em", disabled=True),
    },
    use_container_width=True,
    hide_index=True,
    key=f"editor_{cod_empresa}_{periodo}_{tipo}",
)

st.divider()
st.subheader("Adicionar conta ausente")
st.caption("Use quando o PDF não trouxe uma conta que deveria existir (vira grupo 'AJUSTE MANUAL').")
c1, c2, c3 = st.columns([2, 2, 1])
nova_conta = c1.text_input("Nome da conta")
novo_valor_add = c2.number_input("Valor", value=0.0, step=0.01, format="%.2f")
adicionar = c3.button("+ Adicionar")

if st.button("💾 Salvar correções", type="primary"):
    alterados = 0
    for _, row_orig in df.iterrows():
        row_novo = df_edit.loc[df_edit["id"] == row_orig["id"]].iloc[0]
        if float(row_novo["valor"]) != float(row_orig["valor"]):
            db.salvar_correcao_manual(
                conn, int(row_orig["id"]), float(row_novo["valor"]), periodo, usuario
            )
            alterados += 1
    if alterados:
        st.success(f"{alterados} conta(s) corrigida(s) e gravada(s).")
        st.rerun()
    else:
        st.info("Nenhum valor foi alterado.")

if adicionar:
    if not nova_conta.strip():
        st.error("Informe o nome da conta.")
    else:
        db.salvar_correcao_manual(
            conn, None, float(novo_valor_add), periodo, usuario,
            empresa_codigo=cod_empresa, tipo=tipo, conta=nova_conta.strip(),
        )
        st.success(f"Conta '{nova_conta}' adicionada.")
        st.rerun()
