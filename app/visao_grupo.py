# -*- coding: utf-8 -*-
"""
Logica de agregacao da Visao Grupo -- modulo puro (sem streamlit/db, mesma
filosofia de projecao.py/parser_egc.py/validacoes.py: testavel isolado com
dado sintetico). Extraido de app/telas/4_Visao_Grupo.py em 22/09/2026 pra
ser reaproveitado tambem pela ferramenta "consultar_visao_grupo" do chat
(6_Assistente.py) -- MESMA logica testada, sem duplicar (regra do Rafael:
preservar o que ja funciona, evitar reescrever).

3 funcoes, na ordem em que sao chamadas:
  1. montar_pivot_grupo -- pivota lancamentos "achatados" (1 linha por
     empresa+grupo+conta+valor, formato de db.listar_lancamentos_grupo)
     em 1 linha por (grupo,conta) x 1 coluna por empresa + VALOR CONSOLIDADO.
  2. visao_macro -- Energia separada x Empresas Consolidadoras somadas +
     % de participacao (igual a planilha real "BALANCO GRUPO"/"DRE GRUPO").
  3. visao_especifica -- as empresas selecionadas abertas 1 a 1, sem agrupar
     (so' renomeia coluna de codigo pra nome).

Premissa (ja testada ao vivo antes da 1a versao desta tela, 21/09/2026):
pivot por igualdade EXATA de (grupo,conta) e' seguro -- nomes de conta
batem exatamente entre empresas quando compartilhados, sem colisao.
"""
from __future__ import annotations

import pandas as pd

COD_ENERGIA = "ENERGIA"
CODS_CONSOLIDADORAS = ["ENG", "CONST", "RENOV", "SOL", "SMG"]

# Contas-chave usadas como KPI do resumo da Visao Grupo "Completo" (22/09/2026,
# pedido do Rafael: "mais informacoes, detalhes, graficos, numeros prontos" +
# "a visao poder alcancar tudo de todos os periodos"). Nomes EXATOS de saida
# do parser (parser_egc.py, dicionario CONTAS_BP/CONTAS_DRE, campo nome_saida
# -- conferido no codigo antes de usar, nao assumido): TOTAL DO ATIVO/PASSIVO
# ja e' usado em validacoes.checar_fechamento_bp; as 3 linhas de DRE sao as
# que o parser SEMPRE tenta ter (calculadas se o SPED nao trouxer explicito,
# ver parser_egc.calcular_derivados_dre).
CONTAS_KPI_BP = ["TOTAL DO ATIVO", "TOTAL DO PASSIVO"]
CONTAS_KPI_DRE = ["RECEITA OPERACIONAL LIQUIDA", "LUCRO BRUTO", "LUCRO LIQUIDO DO EXERCICIO"]


def montar_pivot_grupo(lancamentos: list[dict], cods_selecionados: list[str]) -> pd.DataFrame:
    """
    lancamentos: [{"empresa_codigo", "grupo", "conta", "valor"}, ...] (formato
    de db.listar_lancamentos_grupo -- valor pode vir Decimal do psycopg2).
    Retorna DataFrame com colunas: grupo, conta, 1 coluna por codigo em
    cods_selecionados, VALOR CONSOLIDADO. Lista vazia -> DataFrame vazio
    (mesmas colunas esperadas, 0 linhas) pra quem chama decidir o que fazer
    (a tela para com st.stop(), a ferramenta do chat devolve lista vazia).
    """
    if not lancamentos:
        vazio = pd.DataFrame(columns=["grupo", "conta", "VALOR CONSOLIDADO"] + list(cods_selecionados))
        return vazio

    df = pd.DataFrame(lancamentos)
    # psycopg2 devolve NUMERIC do Postgres como decimal.Decimal (nao float) --
    # sem este cast, pivot_table gera colunas dtype=object (Decimal) que o
    # reindex(fill_value=0.0) mistura com float puro, e ".sum(axis=1)" quebra
    # com TypeError. Bug real ja corrigido na 1a versao desta tela (22/09/2026).
    df["valor"] = df["valor"].astype(float)
    pivot = df.pivot_table(
        index=["grupo", "conta"], columns="empresa_codigo", values="valor", aggfunc="sum", fill_value=0.0
    )
    # reindex: garante 1 coluna por empresa selecionada mesmo quando ela nao
    # tem NENHUM lancamento neste periodo+tipo (senao a coluna nem apareceria)
    pivot = pivot.reindex(columns=cods_selecionados, fill_value=0.0).reset_index()
    pivot["VALOR CONSOLIDADO"] = pivot[cods_selecionados].sum(axis=1)
    return pivot


def _pct(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    # 0/0 -> NaN, x/0 -> inf/-inf; ambos viram 0 (sem participacao definida
    # quando o consolidado da conta e' 0)
    return (numerador / denominador).replace([float("inf"), float("-inf")], 0.0).fillna(0.0)


def visao_macro(pivot: pd.DataFrame, cods_selecionados: list[str]) -> pd.DataFrame:
    """Energia separada x Empresas Consolidadoras somadas, com % de participacao."""
    cods_consol_presentes = [c for c in CODS_CONSOLIDADORAS if c in cods_selecionados]
    tem_energia = COD_ENERGIA in cods_selecionados

    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"]].copy()
    saida["ENERMAIS ENERGIA"] = pivot[COD_ENERGIA] if tem_energia else 0.0
    saida["% ENERGIA"] = _pct(saida["ENERMAIS ENERGIA"], saida["VALOR CONSOLIDADO"])
    saida["EMPRESAS CONSOLIDADORAS"] = (
        pivot[cods_consol_presentes].sum(axis=1) if cods_consol_presentes else 0.0
    )
    saida["% CONSOLIDADORAS"] = _pct(saida["EMPRESAS CONSOLIDADORAS"], saida["VALOR CONSOLIDADO"])
    return saida


def visao_especifica(pivot: pd.DataFrame, cods_selecionados: list[str], nome_por_cod: dict) -> pd.DataFrame:
    """As empresas selecionadas abertas 1 a 1 (colunas renomeadas de codigo pra nome), sem agrupar."""
    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"] + cods_selecionados].copy()
    saida = saida.rename(columns=nome_por_cod)
    return saida


def calcular_completude_grupo(
    periodos: list, lancs_bp: list[dict], lancs_dre: list[dict],
    empresas: list[tuple[str, str, str]],
) -> pd.DataFrame:
    """
    Matriz de completude de dados por empresa x periodo -- usada no painel
    de pendencias da Inicio (23/09/2026, retomando item deferido em
    22/09: "quais pendencias ainda faltam alem do gerador de
    relatorios?"). Zero query nova: lancs_bp/lancs_dre vem de
    db.listar_lancamentos_grupo_periodos (mesma funcao ja usada no resumo
    do grupo desta tela e na Inicio), so' reaproveita pra marcar presenca/
    ausencia por (empresa, periodo).

    periodos: lista de periodos a cobrir (normalmente
    db.listar_periodos_grupo -- uniao com status ATIVO entre as empresas).
    empresas: lista (codigo, nome, cnpj), mesmo formato de
    conexao.EMPRESAS_FIXAS.

    Retorna 1 linha por (periodo, empresa) -- colunas: Periodo (date),
    Empresa (nome), empresa_codigo, tem_bp (bool), tem_dre (bool), Status
    ("Completo"/"So' BP"/"So' DRE"/"Faltando"). Lista de periodos vazia
    devolve DataFrame vazio com as mesmas colunas (nada pra iterar).
    """
    colunas = ["Período", "Empresa", "empresa_codigo", "tem_bp", "tem_dre", "Status"]
    if not periodos:
        return pd.DataFrame(columns=colunas)

    set_bp = {(r["empresa_codigo"], r["periodo"]) for r in lancs_bp}
    set_dre = {(r["empresa_codigo"], r["periodo"]) for r in lancs_dre}

    linhas = []
    for periodo in periodos:
        for cod, nome, _cnpj in empresas:
            tem_bp = (cod, periodo) in set_bp
            tem_dre = (cod, periodo) in set_dre
            if tem_bp and tem_dre:
                status = "✅ Completo"
            elif tem_bp:
                status = "⚠️ Só BP"
            elif tem_dre:
                status = "⚠️ Só DRE"
            else:
                status = "❌ Faltando"
            linhas.append({
                "Período": periodo, "Empresa": nome, "empresa_codigo": cod,
                "tem_bp": tem_bp, "tem_dre": tem_dre, "Status": status,
            })
    return pd.DataFrame(linhas, columns=colunas)


def montar_serie_kpis_grupo(
    lancamentos_multi: list[dict], cods_selecionados: list[str], contas_kpi: list[str], periodos: list,
) -> pd.DataFrame:
    """
    Serie temporal das contas-chave (CONTAS_KPI_BP ou CONTAS_KPI_DRE) somadas
    entre as empresas selecionadas, 1 linha por periodo -- usada no "Resumo
    do grupo" da Visao Grupo "Completo" (KPIs + grafico de evolucao).

    lancamentos_multi: formato de db.listar_lancamentos_grupo_periodos ([{
    "empresa_codigo","periodo","grupo","conta","valor"}, ...], varios
    periodos misturados). `periodos` e' a lista COMPLETA de periodos a
    cobrir (nao inferida do dado) -- garante 1 linha por periodo pedido
    mesmo se nenhuma das contas_kpi apareceu nele (fica 0.0, nao some a
    linha).

    Indice do DataFrame retornado e' pd.DatetimeIndex de verdade (mesma
    razao do fix do grafico do Dashboard de Projecao, 22/09/2026: um
    indice de STRING faz o st.line_chart/Vega-Lite reordenar por ordem
    alfabetica em vez de cronologica -- nunca repetir esse bug aqui).
    """
    colunas = list(contas_kpi)
    if not periodos:
        vazio = pd.DataFrame(columns=colunas)
        vazio.index = pd.DatetimeIndex([], name="periodo")
        return vazio

    if lancamentos_multi:
        df = pd.DataFrame(lancamentos_multi)
        df["valor"] = df["valor"].astype(float)  # Decimal do psycopg2 -- mesma regra de sempre
        df = df[df["empresa_codigo"].isin(cods_selecionados) & df["conta"].isin(contas_kpi)]
        agrupado = df.groupby(["periodo", "conta"])["valor"].sum().unstack("conta") if not df.empty else pd.DataFrame()
    else:
        agrupado = pd.DataFrame()

    agrupado = agrupado.reindex(index=sorted(periodos), columns=colunas, fill_value=0.0)
    agrupado.index = pd.to_datetime(agrupado.index)
    agrupado.index.name = "periodo"
    return agrupado
