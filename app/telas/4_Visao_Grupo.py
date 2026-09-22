# -*- coding: utf-8 -*-
"""
Tela VISAO GRUPO — BP/DRE consolidado multi-empresa, equivalente as abas
"BALANCO GRUPO"/"DRE GRUPO" da planilha real da contadora (conferida em
21/09/2026, "DEMONSTRATIVOS - POR EMPRESA E CONSOLIDADO - 2o Trimestre
2026.xlsx"): coluna VALOR CONSOLIDADO = soma de todas as empresas;
ENERMAIS ENERGIA sempre separada; as outras 5 (ENG, CONST, RENOV, SOL,
SMG) somadas juntas em EMPRESAS CONSOLIDADORAS; % = participacao de cada
bloco no consolidado.

Duas visoes (pedido literal do Rafael, 21/09/2026):
  - "Macro"      — so' Energia x Consolidadoras (igual a planilha).
  - "Específica" — as empresas selecionadas abertas 1 a 1, sem agrupar.

Premissa testada ao vivo ANTES de construir isto (Revisao/Correcao, SMG x
Construtora, ambas em 06/2026, unicas 2 com dado ativo nesse periodo no
momento do teste): quando 2 empresas tem a MESMA conta, o texto bate
exatamente (mesma grafia/maiusculas) -- ver db.listar_lancamentos_grupo.
Por isso o pivot abaixo agrupa por igualdade EXATA de (grupo, conta), sem
nenhuma busca por texto/substring (a colisao conhecida do projeto, tipo
"CLIENTES" vs "ADIANTAMENTOS DE CLIENTES", so afeta busca por texto curto
-- nao afeta igualdade exata).

Subtotais (ex. "TOTAL CIRCULANTE ATIVO") NAO sao calculados aqui -- ja
vem como linha propria extraida do PDF (mesma fonte que BP/DRE normal),
entao esta tela so' pivota o que ja existe em egc.lancamentos, sem
reimplementar nenhuma logica de soma/subtotal do parser ou do
REL_14_LUCRO (regra absoluta do projeto).

So' LEITURA -- nao grava nada. Correcao de valor continua sendo feita em
Revisao/Correcao; esta tela so' reflete o que ja esta' ATIVO no banco.

Mudanca "Completo" (22/09/2026, itens #6/#9 do feedback ao vivo do
Rafael, escopo escolhido explicitamente por ele via pergunta de decisao):
  - #9: nao exige mais 2+ empresas (da' pra ver 1 CNPJ sozinho); BP e DRE
    aparecem JUNTOS na mesma tela (radio exclusivo removido); periodo
    virou multiselect (nao so' 1 por vez), escalando de "tudo de todos os
    periodos" ate' "1 periodo de 1 CNPJ" no mesmo controle.
  - #6: nova secao "Resumo do grupo" no topo com KPIs prontos (Total do
    Ativo/Passivo do BP, Receita Liquida/Lucro Bruto/Lucro Liquido do
    DRE) do periodo de detalhe escolhido, com delta vs periodo anterior
    quando disponivel na selecao, + 2 graficos de evolucao (BP e DRE,
    escalas bem diferentes -- misturar num grafico so' seria enganoso)
    quando 2+ periodos estao selecionados.
  - Tabela de detalhe (macro/especifica) continua existindo embaixo,
    agora mostrando BP e DRE empilhados pro "periodo de detalhe"
    escolhido dentro da selecao de periodos.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import visao_grupo  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("🏢 Visão Grupo")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "BP/DRE consolidado das empresas do grupo — mesma lógica da planilha "
    "'BALANÇO GRUPO'/'DRE GRUPO'. Só leitura; correções continuam em Revisão/Correção."
)

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idxs_sel = st.multiselect(
    "Empresa(s) no consolidado", range(len(EMPRESAS_FIXAS)),
    default=list(range(len(EMPRESAS_FIXAS))), format_func=lambda i: nomes_emp[i],
    key="grupo_empresas_sel",
)
cods_selecionados = [EMPRESAS_FIXAS[i][0] for i in idxs_sel]
if not cods_selecionados:
    st.info("Selecione ao menos 1 empresa.")
    st.stop()

periodos_disponiveis = db.listar_periodos_grupo(conn, cods_selecionados, status="ATIVO")
if not periodos_disponiveis:
    st.info("Nenhum período ativo entre as empresas selecionadas ainda.")
    st.stop()

periodos_sel = st.multiselect(
    "Período(s) — do grupo inteiro em todos os períodos até 1 período de 1 empresa só",
    periodos_disponiveis, default=periodos_disponiveis, format_func=lambda d: d.strftime("%m/%Y"),
    key="grupo_periodos_sel",
)
if not periodos_sel:
    st.info("Selecione ao menos 1 período.")
    st.stop()
periodos_sel = sorted(periodos_sel)

# ─────────────────────── Resumo do grupo (KPIs + gráficos) ────────────────
st.divider()
st.subheader("Resumo do grupo")

periodo_detalhe = st.selectbox(
    "Período de detalhe (usado nos KPIs e na tabela abaixo)", list(reversed(periodos_sel)),
    format_func=lambda d: d.strftime("%m/%Y"), key="grupo_periodo_detalhe_sel",
)
periodo_anterior = None
idx_detalhe = periodos_sel.index(periodo_detalhe)
if idx_detalhe > 0:
    periodo_anterior = periodos_sel[idx_detalhe - 1]

lancs_bp_multi = db.listar_lancamentos_grupo_periodos(conn, periodos_sel, "BP", cods_selecionados, status="ATIVO")
lancs_dre_multi = db.listar_lancamentos_grupo_periodos(conn, periodos_sel, "DRE", cods_selecionados, status="ATIVO")

serie_bp = visao_grupo.montar_serie_kpis_grupo(lancs_bp_multi, cods_selecionados, visao_grupo.CONTAS_KPI_BP, periodos_sel)
serie_dre = visao_grupo.montar_serie_kpis_grupo(lancs_dre_multi, cods_selecionados, visao_grupo.CONTAS_KPI_DRE, periodos_sel)


def _metric(col, serie, conta, label, periodo, periodo_ant):
    valor = serie.loc[pd.Timestamp(periodo), conta] if pd.Timestamp(periodo) in serie.index else 0.0
    delta = None
    if periodo_ant is not None and pd.Timestamp(periodo_ant) in serie.index:
        delta = valor - serie.loc[pd.Timestamp(periodo_ant), conta]
    col.metric(label, f"R$ {valor:,.2f}", delta=(f"R$ {delta:,.2f}" if delta is not None else None))


k1, k2, k3, k4, k5 = st.columns(5)
_metric(k1, serie_bp, "TOTAL DO ATIVO", "Total do Ativo", periodo_detalhe, periodo_anterior)
_metric(k2, serie_bp, "TOTAL DO PASSIVO", "Total do Passivo", periodo_detalhe, periodo_anterior)
_metric(k3, serie_dre, "RECEITA OPERACIONAL LIQUIDA", "Receita Líquida", periodo_detalhe, periodo_anterior)
_metric(k4, serie_dre, "LUCRO BRUTO", "Lucro Bruto", periodo_detalhe, periodo_anterior)
_metric(k5, serie_dre, "LUCRO LIQUIDO DO EXERCICIO", "Lucro Líquido", periodo_detalhe, periodo_anterior)

if len(periodos_sel) >= 2:
    g1, g2 = st.columns(2)
    g1.caption("Evolução patrimonial (BP)")
    g1.line_chart(serie_bp.rename(columns={"TOTAL DO ATIVO": "Ativo", "TOTAL DO PASSIVO": "Passivo"}))
    g2.caption("Evolução de resultado (DRE)")
    g2.line_chart(serie_dre.rename(columns={
        "RECEITA OPERACIONAL LIQUIDA": "Receita líquida",
        "LUCRO BRUTO": "Lucro bruto",
        "LUCRO LIQUIDO DO EXERCICIO": "Lucro líquido",
    }))
else:
    st.caption("Selecione 2+ períodos pra ver os gráficos de evolução (com 1 só, os KPIs acima já mostram o valor).")

# ─────────────────────────── Detalhe por conta ─────────────────────────────
st.divider()
st.subheader(f"Detalhe por conta — {periodo_detalhe.strftime('%m/%Y')}")

visao = st.radio(
    "Visão", ["Macro (Energia × Consolidadoras)", "Específica (empresas abertas)"],
    horizontal=True, key="grupo_visao_sel",
)

algum_dado = False
for tipo_sel in ("BP", "DRE"):
    lancamentos = (lancs_bp_multi if tipo_sel == "BP" else lancs_dre_multi)
    lancamentos = [r for r in lancamentos if r["periodo"] == periodo_detalhe]
    st.markdown(f"**{tipo_sel}**")
    if not lancamentos:
        st.caption(f"Nenhum lançamento de {tipo_sel} em {periodo_detalhe.strftime('%m/%Y')} pras empresas selecionadas.")
        continue
    algum_dado = True

    pivot = visao_grupo.montar_pivot_grupo(lancamentos, cods_selecionados)

    if visao.startswith("Macro"):
        saida = visao_grupo.visao_macro(pivot, cods_selecionados)
        column_config = {
            "grupo": st.column_config.TextColumn("Grupo", disabled=True),
            "conta": st.column_config.TextColumn("Conta", disabled=True),
            "VALOR CONSOLIDADO": st.column_config.NumberColumn("Valor consolidado", format="R$ %.2f"),
            "ENERMAIS ENERGIA": st.column_config.NumberColumn("Enermais Energia", format="R$ %.2f"),
            "% ENERGIA": st.column_config.NumberColumn("% Energia", format="percent"),
            "EMPRESAS CONSOLIDADORAS": st.column_config.NumberColumn("Empresas consolidadoras", format="R$ %.2f"),
            "% CONSOLIDADORAS": st.column_config.NumberColumn("% Consolidadoras", format="percent"),
        }
    else:
        saida = visao_grupo.visao_especifica(pivot, cods_selecionados, NOME_POR_COD)
        column_config = {
            "grupo": st.column_config.TextColumn("Grupo", disabled=True),
            "conta": st.column_config.TextColumn("Conta", disabled=True),
            "VALOR CONSOLIDADO": st.column_config.NumberColumn("Valor consolidado", format="R$ %.2f"),
        }
        for cod in cods_selecionados:
            column_config[NOME_POR_COD[cod]] = st.column_config.NumberColumn(
                f"{NOME_POR_COD[cod]} ({cod})", format="R$ %.2f"
            )

    st.dataframe(saida, column_config=column_config, hide_index=True, use_container_width=True)
    st.caption(f"{len(pivot)} conta(s) distinta(s) de {tipo_sel} · {len(cods_selecionados)} empresa(s) selecionada(s).")

if not algum_dado:
    st.info(f"Nenhum lançamento (BP ou DRE) em {periodo_detalhe.strftime('%m/%Y')} pras empresas selecionadas.")
