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

Mudanca de 21/09/2026 (feedback do Rafael testando ao vivo): BP e DRE do
periodo selecionado aparecem JUNTOS, um embaixo do outro -- antes era um
radio "BP ou DRE" que so mostrava 1 tipo por vez, obrigando trocar de aba
pra comparar/corrigir os dois. Agora e' 1 secao por tipo (cada uma com
seu proprio data_editor) e 1 botao "Salvar correcoes" que grava as
alteracoes dos dois de uma vez. "Adicionar conta ausente" ganhou um
seletor de Tipo (BP/DRE) porque nao tem mais um radio unico pra herdar.
"""
import sys
from pathlib import Path

import pandas as pd
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

st.caption(
    "Edite a coluna **valor** e clique em Salvar. O valor original do PDF é preservado "
    "automaticamente na 1ª correção de cada conta (coluna `pdf_original`, abaixo). "
    "BP e DRE do período aparecem juntos — não precisa trocar de aba pra comparar."
)

COLUMN_CONFIG = {
    "id": st.column_config.NumberColumn("ID", disabled=True),
    "grupo": st.column_config.TextColumn("Grupo", disabled=True),
    "conta": st.column_config.TextColumn("Conta", disabled=True),
    "corrigido": st.column_config.CheckboxColumn("✏️ Corrigido", disabled=True),
    "valor": st.column_config.NumberColumn("Valor", format="%.2f", step=0.01),
    "origem": st.column_config.TextColumn("Origem", disabled=True),
    "pdf_original": st.column_config.NumberColumn("PDF original", format="%.2f", disabled=True),
    "arquivo_pdf": st.column_config.TextColumn("Arquivo", disabled=True),
    "atualizado_em": st.column_config.DatetimeColumn("Atualizado em", disabled=True),
}

# guarda df original + df editado de cada tipo presente, pra "Salvar
# correcoes" conseguir percorrer os dois de uma vez so' no final
secoes = {}
for tipo in ("BP", "DRE"):
    lancamentos = db.listar_lancamentos(conn, cod_empresa, periodo, tipo)
    if not lancamentos:
        continue

    st.subheader(tipo)
    df = pd.DataFrame(lancamentos)
    # Coluna derivada (nao vem do banco) so pra destacar visualmente o que
    # ja foi corrigido a mao -- origem vira "MANUAL <periodo>" na 1a
    # correcao (regra de auditoria existente, ver db.salvar_correcao_manual).
    # data_editor nao aceita pandas Styler pra colorir celula, entao o
    # destaque e' esta coluna de check (mais confiavel que cor, e funciona
    # igual em qualquer tema).
    df.insert(3, "corrigido", df["origem"].astype(str).str.startswith("MANUAL"))
    df_edit = st.data_editor(
        df,
        column_config=COLUMN_CONFIG,
        use_container_width=True,
        hide_index=True,
        key=f"editor_{cod_empresa}_{periodo}_{tipo}",
    )
    secoes[tipo] = (df, df_edit)

if not secoes:
    st.info("Sem lançamentos (BP ou DRE) para este período.")
    st.stop()

st.divider()
st.subheader("Adicionar conta ausente")
st.caption("Use quando o PDF não trouxe uma conta que deveria existir (vira grupo 'AJUSTE MANUAL').")
c0, c1, c2, c3 = st.columns([1, 2, 2, 1])
tipo_add = c0.selectbox("Tipo", ["BP", "DRE"])
nova_conta = c1.text_input("Nome da conta")
novo_valor_add = c2.number_input("Valor", value=0.0, step=0.01, format="%.2f")
adicionar = c3.button("+ Adicionar")

if st.button("💾 Salvar correções", type="primary"):
    alterados = 0
    for tipo, (df, df_edit) in secoes.items():
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
            empresa_codigo=cod_empresa, tipo=tipo_add, conta=nova_conta.strip(),
        )
        st.success(f"Conta '{nova_conta}' adicionada em {tipo_add}.")
        st.rerun()
