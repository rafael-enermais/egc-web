# -*- coding: utf-8 -*-
"""
Tela VISAO GRUPO — BP/DRE consolidado multi-empresa, equivalente as abas
"BALANCO GRUPO"/"DRE GRUPO" da planilha real da contadora (conferida em
21/09/2026, "DEMONSTRATIVOS - POR EMPRESA E CONSOLIDADO - 2o Trimestre
2026.xlsx"): coluna VALOR CONSOLIDADO = soma de todas as empresas;
ENERMAIS ENERGIA sempre separada; as outras 5 (ENG, CONST, RENOV, SOL,
SMG) somadas juntas em EMPRESAS CONSOLIDADORAS; % = participacao de cada
bloco no consolidado.

Duas visoes (pedido literal do Rafael, 21/09/2026 -- fila confirmada:
"Visão Grupo (BP/DRE consolidado multi-empresa + toggle macro/específico)
→ rodapé → dashboard de projeção → chat estilo TIA.go/Viaj.ai"):
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
COD_ENERGIA = "ENERGIA"
CODS_CONSOLIDADORAS = ["ENG", "CONST", "RENOV", "SOL", "SMG"]

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
if len(cods_selecionados) < 2:
    st.info("Selecione ao menos 2 empresas pra montar um consolidado.")
    st.stop()

periodos = db.listar_periodos_grupo(conn, cods_selecionados, status="ATIVO")
if not periodos:
    st.info("Nenhum período ativo entre as empresas selecionadas ainda.")
    st.stop()

c1, c2, c3 = st.columns([1, 1, 1.6])
periodo_sel = c1.selectbox(
    "Período", periodos, format_func=lambda d: d.strftime("%m/%Y"), key="grupo_periodo_sel"
)
tipo_sel = c2.radio("Tipo", ["BP", "DRE"], horizontal=True, key="grupo_tipo_sel")
visao = c3.radio(
    "Visão", ["Macro (Energia × Consolidadoras)", "Específica (empresas abertas)"],
    horizontal=True, key="grupo_visao_sel",
)

lancamentos = db.listar_lancamentos_grupo(conn, periodo_sel, tipo_sel, cods_selecionados, status="ATIVO")
if not lancamentos:
    st.info(
        f"Nenhum lançamento de {tipo_sel} em {periodo_sel.strftime('%m/%Y')} "
        "pras empresas selecionadas."
    )
    st.stop()

df = pd.DataFrame(lancamentos)
# psycopg2 devolve NUMERIC do Postgres como decimal.Decimal (nao float) --
# mesma razao pela qual Revisao_Correcao.py faz float(row["valor"]) antes
# de comparar/gravar. Sem este cast, pivot_table gera colunas dtype=object
# (Decimal) que o reindex(fill_value=0.0) mistura com float puro, e
# ".sum(axis=1)" quebra com TypeError (Decimal + float nao e permitido em
# Python). Bug real, pego so' na verificacao ao vivo pos-deploy (nao
# reproduzido nos testes de db.py porque la' o valor injetado no mock ja'
# era float, nunca Decimal de verdade).
df["valor"] = df["valor"].astype(float)
pivot = df.pivot_table(
    index=["grupo", "conta"], columns="empresa_codigo", values="valor", aggfunc="sum", fill_value=0.0
)
# reindex: garante 1 coluna por empresa selecionada mesmo quando ela nao
# tem NENHUM lancamento neste periodo+tipo (senao a coluna nem apareceria)
pivot = pivot.reindex(columns=cods_selecionados, fill_value=0.0).reset_index()

pivot["VALOR CONSOLIDADO"] = pivot[cods_selecionados].sum(axis=1)


def _pct(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    # 0/0 -> NaN, x/0 -> inf/-inf; ambos viram 0 (sem participacao definida
    # quando o consolidado da conta e' 0)
    return (numerador / denominador).replace([float("inf"), float("-inf")], 0.0).fillna(0.0)


if visao.startswith("Macro"):
    cods_consol_presentes = [c for c in CODS_CONSOLIDADORAS if c in cods_selecionados]
    tem_energia = COD_ENERGIA in cods_selecionados

    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"]].copy()
    saida["ENERMAIS ENERGIA"] = pivot[COD_ENERGIA] if tem_energia else 0.0
    saida["% ENERGIA"] = _pct(saida["ENERMAIS ENERGIA"], saida["VALOR CONSOLIDADO"])
    saida["EMPRESAS CONSOLIDADORAS"] = (
        pivot[cods_consol_presentes].sum(axis=1) if cods_consol_presentes else 0.0
    )
    saida["% CONSOLIDADORAS"] = _pct(saida["EMPRESAS CONSOLIDADORAS"], saida["VALOR CONSOLIDADO"])

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
    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"] + cods_selecionados].copy()
    saida = saida.rename(columns=NOME_POR_COD)
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

st.caption(
    f"{len(pivot)} conta(s) distinta(s) · {len(cods_selecionados)} empresa(s) selecionada(s) · "
    f"{tipo_sel} {periodo_sel.strftime('%m/%Y')}."
)
