# -*- coding: utf-8 -*-
"""
Testes unitarios de app/visao_grupo.py (extraido de 4_Visao_Grupo.py em
22/09/2026, reaproveitado tambem pela ferramenta do chat). Roda igual aos
outros arquivos de teste do projeto (sem pytest, __main__ no final).
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import visao_grupo  # noqa: E402

CODS_TODAS = ["ENERGIA", "SMG", "ENG", "RENOV", "CONST", "SOL"]

MOCK_LANCS = [
    {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("500000.00")},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("121755.76")},
    {"empresa_codigo": "CONST", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("80.00")},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": Decimal("749.70")},
    {"empresa_codigo": "ENG", "grupo": "ATIVO NAO CIRCULANTE", "conta": "IMOBILIZADO", "valor": Decimal("10000.00")},
]


def test_montar_pivot_grupo_vazio_devolve_dataframe_vazio_com_colunas_certas():
    pivot = visao_grupo.montar_pivot_grupo([], CODS_TODAS)
    assert list(pivot.columns) == ["grupo", "conta", "VALOR CONSOLIDADO"] + CODS_TODAS
    assert len(pivot) == 0
    print("OK: montar_pivot_grupo — lista vazia devolve DataFrame vazio com colunas certas")


def test_montar_pivot_grupo_soma_decimal_sem_typeerror_e_fillna_zero():
    # regressao do bug real: Decimal (do banco) + float (fill_value do reindex)
    pivot = visao_grupo.montar_pivot_grupo(MOCK_LANCS, CODS_TODAS)
    linha_clientes = pivot[pivot["conta"] == "CLIENTES"].iloc[0]
    assert linha_clientes["ENERGIA"] == 500000.0
    assert linha_clientes["SMG"] == 121755.76
    assert linha_clientes["CONST"] == 80.0
    assert linha_clientes["ENG"] == 0.0  # sem lancamento nessa conta -> 0, nao NaN
    assert linha_clientes["VALOR CONSOLIDADO"] == 500000.0 + 121755.76 + 80.0
    linha_imobilizado = pivot[pivot["conta"] == "IMOBILIZADO"].iloc[0]
    assert linha_imobilizado["ENG"] == 10000.0
    assert linha_imobilizado["VALOR CONSOLIDADO"] == 10000.0
    print("OK: montar_pivot_grupo — soma Decimal sem TypeError, reindex preenche 0.0")


def test_montar_pivot_grupo_reindex_so_empresas_selecionadas():
    # so' ENERGIA e SMG selecionadas -- ENG/RENOV/CONST/SOL nao devem aparecer
    # como coluna, mesmo tendo lancamento (IMOBILIZADO/ENG) no mock
    pivot = visao_grupo.montar_pivot_grupo(MOCK_LANCS, ["ENERGIA", "SMG"])
    assert list(pivot.columns) == ["grupo", "conta", "ENERGIA", "SMG", "VALOR CONSOLIDADO"]
    assert "ENG" not in pivot.columns
    print("OK: montar_pivot_grupo — reindex respeita so' as empresas selecionadas")


def test_visao_macro_energia_separada_de_consolidadoras_com_percentual():
    pivot = visao_grupo.montar_pivot_grupo(MOCK_LANCS, CODS_TODAS)
    saida = visao_grupo.visao_macro(pivot, CODS_TODAS)
    linha = saida[saida["conta"] == "CLIENTES"].iloc[0]
    assert linha["ENERMAIS ENERGIA"] == 500000.0
    assert linha["EMPRESAS CONSOLIDADORAS"] == 121755.76 + 80.0  # SMG + CONST (as unicas com CLIENTES)
    total = 500000.0 + 121755.76 + 80.0
    assert abs(linha["% ENERGIA"] - 500000.0 / total) < 1e-9
    assert abs(linha["% CONSOLIDADORAS"] - (121755.76 + 80.0) / total) < 1e-9
    assert abs(linha["% ENERGIA"] + linha["% CONSOLIDADORAS"] - 1.0) < 1e-9
    print("OK: visao_macro — Energia x Consolidadoras com % somando 100%")


def test_visao_macro_sem_energia_selecionada_zera_bloco_energia():
    pivot = visao_grupo.montar_pivot_grupo(MOCK_LANCS, ["SMG", "CONST"])
    saida = visao_grupo.visao_macro(pivot, ["SMG", "CONST"])
    linha = saida[saida["conta"] == "CLIENTES"].iloc[0]
    assert linha["ENERMAIS ENERGIA"] == 0.0
    assert linha["% ENERGIA"] == 0.0
    assert linha["EMPRESAS CONSOLIDADORAS"] == 121755.76 + 80.0
    print("OK: visao_macro — sem Energia selecionada, bloco Energia fica 0 sem quebrar")


def test_visao_macro_conta_com_consolidado_zero_nao_gera_divisao_por_zero():
    # conta so' existe pra ENERGIA=0 e SMG=0 -> VALOR CONSOLIDADO=0 -> % deve
    # virar 0.0, nao NaN/inf (0/0 e' NaN em pandas, x/0 e' inf)
    lancs = [
        {"empresa_codigo": "ENERGIA", "grupo": "G", "conta": "ZERADA", "valor": Decimal("0.00")},
        {"empresa_codigo": "SMG", "grupo": "G", "conta": "ZERADA", "valor": Decimal("0.00")},
    ]
    pivot = visao_grupo.montar_pivot_grupo(lancs, ["ENERGIA", "SMG"])
    saida = visao_grupo.visao_macro(pivot, ["ENERGIA", "SMG"])
    linha = saida.iloc[0]
    assert linha["% ENERGIA"] == 0.0
    assert linha["% CONSOLIDADORAS"] == 0.0
    print("OK: visao_macro — consolidado 0 vira % 0.0 (nao NaN/inf)")


def test_visao_especifica_abre_empresas_sem_agrupar_e_renomeia():
    pivot = visao_grupo.montar_pivot_grupo(MOCK_LANCS, ["ENERGIA", "SMG", "CONST"])
    nome_por_cod = {"ENERGIA": "Enermais Energia Ltda", "SMG": "SMG Solucoes Ltda", "CONST": "Enermais Construtora Ltda"}
    saida = visao_grupo.visao_especifica(pivot, ["ENERGIA", "SMG", "CONST"], nome_por_cod)
    assert list(saida.columns) == ["grupo", "conta", "VALOR CONSOLIDADO", "Enermais Energia Ltda", "SMG Solucoes Ltda", "Enermais Construtora Ltda"]
    linha = saida[saida["conta"] == "CLIENTES"].iloc[0]
    assert linha["Enermais Energia Ltda"] == 500000.0
    assert linha["SMG Solucoes Ltda"] == 121755.76
    assert linha["Enermais Construtora Ltda"] == 80.0
    print("OK: visao_especifica — empresas abertas 1 a 1, colunas renomeadas certas")


# ─────────────── montar_serie_kpis_grupo (Visão Grupo "Completo", item 6/9) ───

MOCK_LANCS_MULTI_PERIODO = [
    {"empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 5, 31), "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 5, 31), "grupo": "PASSIVO", "conta": "TOTAL DO PASSIVO", "valor": Decimal("1000000.00")},
    {"empresa_codigo": "SMG", "periodo": datetime.date(2026, 5, 31), "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("200000.00")},
    {"empresa_codigo": "SMG", "periodo": datetime.date(2026, 5, 31), "grupo": "PASSIVO", "conta": "TOTAL DO PASSIVO", "valor": Decimal("200000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 6, 30), "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("1100000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 6, 30), "grupo": "PASSIVO", "conta": "TOTAL DO PASSIVO", "valor": Decimal("1100000.00")},
    # SMG sem lançamento em 06/2026 -- deve virar 0.0 nesse período, não sumir a linha
]


def test_montar_serie_kpis_grupo_1_linha_por_periodo_pedido_mesmo_sem_lancamento():
    periodos = [datetime.date(2026, 5, 31), datetime.date(2026, 6, 30)]
    serie = visao_grupo.montar_serie_kpis_grupo(
        MOCK_LANCS_MULTI_PERIODO, ["ENERGIA", "SMG"], visao_grupo.CONTAS_KPI_BP, periodos,
    )
    assert len(serie) == 2, "esperava 1 linha por período pedido, mesmo com lançamento parcial"
    assert list(serie.columns) == visao_grupo.CONTAS_KPI_BP
    mai = serie.loc[pd.Timestamp(2026, 5, 31)]
    jun = serie.loc[pd.Timestamp(2026, 6, 30)]
    assert mai["TOTAL DO ATIVO"] == 1000000.0 + 200000.0  # ENERGIA + SMG
    assert jun["TOTAL DO ATIVO"] == 1100000.0  # só ENERGIA -- SMG não tem lançamento em 06/2026, soma 0
    print("OK: montar_serie_kpis_grupo — 1 linha por período pedido, soma Decimal certa, 0.0 quando falta lançamento")


def test_montar_serie_kpis_grupo_indice_e_datetime_ordenado():
    # mesma regra do fix do Dashboard de Projeção: índice tem que ser
    # DatetimeIndex de verdade (não string), senão st.line_chart reordena
    # alfabeticamente em vez de cronologicamente.
    periodos = [datetime.date(2026, 6, 30), datetime.date(2025, 12, 31)]  # fora de ordem de propósito
    serie = visao_grupo.montar_serie_kpis_grupo(
        MOCK_LANCS_MULTI_PERIODO, ["ENERGIA", "SMG"], visao_grupo.CONTAS_KPI_BP, periodos,
    )
    assert isinstance(serie.index, pd.DatetimeIndex), f"esperava DatetimeIndex, veio {type(serie.index)}"
    assert serie.index.is_monotonic_increasing, f"índice fora de ordem cronológica: {list(serie.index)}"
    print("OK: montar_serie_kpis_grupo — índice é DatetimeIndex ordenado cronologicamente")


def test_montar_serie_kpis_grupo_periodo_sem_nenhum_lancamento_fica_zero():
    periodos = [datetime.date(2026, 5, 31), datetime.date(2024, 1, 31)]  # 2024 não existe no mock
    serie = visao_grupo.montar_serie_kpis_grupo(
        MOCK_LANCS_MULTI_PERIODO, ["ENERGIA", "SMG"], visao_grupo.CONTAS_KPI_BP, periodos,
    )
    linha_2024 = serie.loc[pd.Timestamp(2024, 1, 31)]
    assert (linha_2024 == 0.0).all(), "período sem nenhum lançamento deveria ficar 0.0 em todas as contas, não sumir"
    print("OK: montar_serie_kpis_grupo — período sem nenhum lançamento vira linha de 0.0, não some")


def test_montar_serie_kpis_grupo_respeita_so_empresas_selecionadas():
    periodos = [datetime.date(2026, 5, 31)]
    serie = visao_grupo.montar_serie_kpis_grupo(
        MOCK_LANCS_MULTI_PERIODO, ["ENERGIA"], visao_grupo.CONTAS_KPI_BP, periodos,
    )
    assert serie.loc[pd.Timestamp(2026, 5, 31)]["TOTAL DO ATIVO"] == 1000000.0  # só ENERGIA, sem SMG
    print("OK: montar_serie_kpis_grupo — soma só as empresas selecionadas, ignora as outras")


def test_montar_serie_kpis_grupo_lista_vazia_de_periodos_devolve_vazio():
    serie = visao_grupo.montar_serie_kpis_grupo(MOCK_LANCS_MULTI_PERIODO, ["ENERGIA"], visao_grupo.CONTAS_KPI_BP, [])
    assert len(serie) == 0
    assert list(serie.columns) == visao_grupo.CONTAS_KPI_BP
    print("OK: montar_serie_kpis_grupo — lista de períodos vazia devolve DataFrame vazio com colunas certas")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
