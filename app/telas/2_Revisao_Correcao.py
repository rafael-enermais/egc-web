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

Mudanca de 21/09/2026 (1ª leva, feedback ao vivo): BP e DRE do periodo
selecionado aparecem JUNTOS, um embaixo do outro -- antes era um radio
"BP ou DRE" que so mostrava 1 tipo por vez.

Mudanca de 21/09/2026 (2ª leva, feedback ao vivo sobre os prints): a
selecao de empresa NAO depende mais so' do dropdown da sidebar (que so'
permite 1 de cada vez) -- esta pagina ganhou seu proprio seletor de
empresa(s) E periodo(s), os DOIS multiselect, permitindo revisar/corrigir
mais de 1 CNPJ e/ou mais de 1 periodo ao mesmo tempo (pedido literal do
Rafael: "podendo selecionar mais de 1, podendo analisar mais de 1 CNPJ ou
mais de 1 periodo"). Cada combinacao empresa+periodo vira um expander com
BP+DRE dentro (like antes). "Salvar correcoes" continua sendo 1 botao so'
que grava tudo que foi editado em qualquer combinacao aberta. "Adicionar
conta ausente" ganhou selecao de Empresa+Periodo+Tipo (antes so' Tipo),
ja que agora podem existir varias combinacoes na tela ao mesmo tempo.

Mudanca de 21/09/2026 (3ª leva): o multiselect de Empresa(s) desta pagina
usava o dropdown da SIDEBAR como valor "default" -- na pratica isso fazia
o Streamlit tratar o widget como "novo" toda vez que a sidebar mudava
(porque o default mudava, e o multiselect nao tinha key explicita), o que
resetava a selecao desta pagina sem o usuario mexer em nada aqui ("esse
dropdown do lado esquerdo... interfere em tudo", Rafael). Removida
totalmente a dependencia da sidebar: key explicita + default fixo (1a
empresa da lista), sem nenhuma leitura do estado de outra pagina.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("✏️ Revisão / Correção manual")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "Selecione 1 ou mais empresas e 1 ou mais períodos pra revisar/corrigir junto — não "
    "precisa mais trocar de tela ou selecionar 1 de cada vez pra comparar."
)

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idxs_sel = st.multiselect(
    "Empresa(s)", range(len(EMPRESAS_FIXAS)), default=[0], format_func=lambda i: nomes_emp[i],
    key="revisao_empresas_sel",
)
cods_selecionados = [EMPRESAS_FIXAS[i][0] for i in idxs_sel]
if not cods_selecionados:
    st.info("Selecione ao menos 1 empresa.")
    st.stop()

# uniao dos periodos ATIVOS das empresas selecionadas -- cada empresa pode
# ter um conjunto diferente de periodos importados
periodos_por_empresa = {cod: db.listar_periodos(conn, cod, status="ATIVO") for cod in cods_selecionados}
todos_periodos = sorted({p for lst in periodos_por_empresa.values() for p in lst})
if not todos_periodos:
    nomes_sel = ", ".join(NOME_POR_COD.get(c, c) for c in cods_selecionados)
    st.info(f"Nenhum período ativo pra {nomes_sel} ainda. Importe um PDF primeiro.")
    st.stop()

# guarda contra o caso de trocar a selecao de empresa(s) e a selecao de
# periodo(s) anterior (guardada em session_state pela key) ter algum
# periodo que nao existe mais entre as opcoes novas -- Streamlit reclama
# se o valor guardado nao for mais um subconjunto das opcoes atuais
#
# Nota 21/09/2026: esse padrao (escrever em session_state[key] logo antes
# de um widget keyed que tambem tem default=) faz o Streamlit logar um
# WARNING interno ("was created with a default value but also had its
# value set via the Session State API"). Testado e confirmado inofensivo:
# nao e' excecao, nao afeta o valor final do widget entre reruns (o
# default so' vale na 1a renderizacao; o clamp so' toca estado ja
# existente) -- so' um log verboso do proprio Streamlit por nao conseguir
# distinguir "valor persistido do proprio widget" de "setado via API" aqui.
if "revisao_periodos_sel" in st.session_state:
    st.session_state["revisao_periodos_sel"] = [
        p for p in st.session_state["revisao_periodos_sel"] if p in todos_periodos
    ]
periodos_sel = st.multiselect(
    "Período(s)", todos_periodos, default=todos_periodos[-1:], format_func=lambda d: d.strftime("%m/%Y"),
    key="revisao_periodos_sel",
)
if not periodos_sel:
    st.info("Selecione ao menos 1 período.")
    st.stop()

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

# guarda df original + df editado de CADA combinacao (empresa, periodo,
# tipo) presente, pra "Salvar correcoes" percorrer tudo de uma vez so' no
# final, nao importa quantas combinacoes estao abertas na tela
secoes = {}
combinacoes = [(cod, periodo) for cod in cods_selecionados for periodo in periodos_sel
               if periodo in periodos_por_empresa[cod]]

if not combinacoes:
    st.info("Nenhuma das empresas selecionadas tem os períodos escolhidos ativos.")
    st.stop()

expandir_unica = len(combinacoes) == 1
for cod, periodo in combinacoes:
    nome = NOME_POR_COD.get(cod, cod)
    with st.expander(f"{nome} ({cod}) — {periodo.strftime('%m/%Y')}", expanded=expandir_unica):
        teve_algo = False
        for tipo in ("BP", "DRE"):
            lancamentos = db.listar_lancamentos(conn, cod, periodo, tipo)
            if not lancamentos:
                continue
            teve_algo = True

            st.markdown(f"**{tipo}**")
            df = pd.DataFrame(lancamentos)
            # Coluna derivada (nao vem do banco) so pra destacar visualmente
            # o que ja foi corrigido a mao -- origem vira "MANUAL <periodo>"
            # na 1a correcao (regra de auditoria, ver db.salvar_correcao_manual).
            df.insert(3, "corrigido", df["origem"].astype(str).str.startswith("MANUAL"))
            df_edit = st.data_editor(
                df,
                column_config=COLUMN_CONFIG,
                use_container_width=True,
                hide_index=True,
                key=f"editor_{cod}_{periodo}_{tipo}",
            )
            secoes[(cod, periodo, tipo)] = (df, df_edit)

        if not teve_algo:
            st.caption("Sem lançamentos (BP ou DRE) para este período.")

if not secoes:
    st.info("Sem lançamentos (BP ou DRE) nas combinações selecionadas.")
    st.stop()

st.divider()
st.subheader("Adicionar conta ausente")
st.caption("Use quando o PDF não trouxe uma conta que deveria existir (vira grupo 'AJUSTE MANUAL').")
c0, c1, c2, c3, c4 = st.columns([2, 1, 1, 2, 1])
cod_add = c0.selectbox("Empresa", cods_selecionados, format_func=lambda c: f"{NOME_POR_COD.get(c, c)} ({c})")
periodos_desta_empresa = [p for p in periodos_sel if p in periodos_por_empresa[cod_add]]
periodo_add = c1.selectbox("Período", periodos_desta_empresa, format_func=lambda d: d.strftime("%m/%Y"))
tipo_add = c2.selectbox("Tipo", ["BP", "DRE"])
nova_conta = c3.text_input("Nome da conta")
novo_valor_add = c4.number_input("Valor", value=0.0, step=0.01, format="%.2f")
adicionar = st.button("+ Adicionar")

if st.button("💾 Salvar correções", type="primary"):
    alterados = 0
    for (cod, periodo, tipo), (df, df_edit) in secoes.items():
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
            conn, None, float(novo_valor_add), periodo_add, usuario,
            empresa_codigo=cod_add, tipo=tipo_add, conta=nova_conta.strip(),
        )
        st.success(f"Conta '{nova_conta}' adicionada em {NOME_POR_COD.get(cod_add, cod_add)} — {tipo_add}.")
        st.rerun()
