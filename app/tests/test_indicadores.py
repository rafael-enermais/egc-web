# -*- coding: utf-8 -*-
"""
Testes de app/indicadores.py -- modulo puro (sem banco/Streamlit), mesmo
padrao de test_projecao.py/test_visao_grupo.py. Dado sintetico com
Decimal de proposito (psycopg2 real nunca devolve float) pra pegar
regressao de mistura Decimal+float, mesma classe de bug ja encontrada na
Visao Grupo (v0.3.0) e no Dashboard de Projecao.

Rodar: python3 tests/test_indicadores.py
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import indicadores  # noqa: E402

P_MAI = datetime.date(2026, 5, 31)
P_JUN = datetime.date(2026, 6, 30)

LANCS_BP = [
    {"periodo": P_MAI, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("200000.00")},
    {"periodo": P_MAI, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("100000.00")},
    {"periodo": P_MAI, "grupo": "PASSIVO NAO CIRCULANTE", "conta": "TOTAL NAO CIRCULANTE PASSIVO", "valor": Decimal("50000.00")},
    {"periodo": P_MAI, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"periodo": P_MAI, "grupo": "PATRIMONIO LIQUIDO", "conta": "TOTAL PATRIMONIO LIQUIDO", "valor": Decimal("850000.00")},
    {"periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("220000.00")},
    {"periodo": P_JUN, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("110000.00")},
    {"periodo": P_JUN, "grupo": "PASSIVO NAO CIRCULANTE", "conta": "TOTAL NAO CIRCULANTE PASSIVO", "valor": Decimal("40000.00")},
    {"periodo": P_JUN, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1100000.00")},
    {"periodo": P_JUN, "grupo": "PATRIMONIO LIQUIDO", "conta": "TOTAL PATRIMONIO LIQUIDO", "valor": Decimal("950000.00")},
]
LANCS_DRE = [
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("300000.00")},
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("110000.00")},
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("50000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("320000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("120000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("60000.00")},
]


def test_calcula_todos_indicadores_com_decimal_sem_typeerror():
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    assert list(tabela.columns) == indicadores.COLUNAS_INDICADORES
    assert len(tabela) == 2
    print(f"OK: calcular_indicadores nao quebra com Decimal (psycopg2 real), {len(indicadores.COLUNAS_INDICADORES)} colunas, 2 periodos")


def test_liquidez_corrente_e_capital_de_giro_batem_a_mao():
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    jun = tabela.loc[__import__("pandas").Timestamp(P_JUN)]
    assert abs(jun["Liquidez Corrente"] - (220000.0 / 110000.0)) < 1e-6
    assert abs(jun["Capital de Giro"] - (220000.0 - 110000.0)) < 1e-6
    print("OK: Liquidez Corrente (2.0x) e Capital de Giro (R$110.000) batem calculo manual")


def test_endividamento_usa_passivo_exigivel_sem_misturar_pl():
    # endividamento geral = (circulante + nao circulante) / ativo total,
    # SEM patrimonio liquido misturado -- essa e' a regra que distingue
    # esse indicador de so' usar "TOTAL DO PASSIVO" (que no BP real inclui
    # o PL, e' por isso que Ativo=Passivo sempre fecha).
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    jun = tabela.loc[__import__("pandas").Timestamp(P_JUN)]
    esperado = (110000.0 + 40000.0) / 1100000.0
    assert abs(jun["Endividamento Geral"] - esperado) < 1e-6
    print("OK: Endividamento Geral usa só passivo exigível (circulante+não circulante), não TOTAL DO PASSIVO")


def test_alavancagem_usa_passivo_exigivel_sobre_pl():
    # Alavancagem = passivo exigivel (circulante + nao circulante) / PL --
    # "R$ X de capital de terceiros pra cada R$ 1,00 de capital proprio",
    # leitura que aparece nos modelos de relatorio comentado (pagina de
    # Balanco Patrimonial). Mesmo numerador do Endividamento Geral, PL no
    # denominador em vez de Ativo total.
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    jun = tabela.loc[__import__("pandas").Timestamp(P_JUN)]
    esperado = (110000.0 + 40000.0) / 950000.0
    assert abs(jun["Alavancagem"] - esperado) < 1e-6
    print("OK: Alavancagem usa passivo exigível sobre PL (não Ativo total)")


def test_margens_roa_roe_batem_a_mao():
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    jun = tabela.loc[__import__("pandas").Timestamp(P_JUN)]
    assert abs(jun["Margem Bruta"] - (120000.0 / 320000.0)) < 1e-6
    assert abs(jun["Margem Líquida"] - (60000.0 / 320000.0)) < 1e-6
    assert abs(jun["ROA"] - (60000.0 / 1100000.0)) < 1e-6
    assert abs(jun["ROE"] - (60000.0 / 950000.0)) < 1e-6
    print("OK: Margem Bruta/Líquida, ROA, ROE batem calculo manual")


def test_indice_e_datetimeindex_ordenado_cronologicamente():
    # mesma convencao anti-bug de ordenacao alfabetica do grafico do
    # Dashboard de Projecao/Visao Grupo -- indice tem que ser data de
    # verdade, nao string "MM/AAAA".
    import pandas as pd
    tabela = indicadores.calcular_indicadores(LANCS_BP, LANCS_DRE)
    assert isinstance(tabela.index, pd.DatetimeIndex)
    assert list(tabela.index) == sorted(tabela.index)
    print("OK: índice é pd.DatetimeIndex ordenado cronologicamente")


def test_sem_lancamento_nenhum_devolve_tabela_vazia_sem_excecao():
    tabela = indicadores.calcular_indicadores([], [])
    assert tabela.empty
    assert list(tabela.columns) == indicadores.COLUNAS_INDICADORES
    print("OK: sem BP/DRE nenhum, devolve tabela vazia sem exceção (caso real: empresa recém-cadastrada)")


def test_periodo_so_com_bp_sem_dre_nao_quebra():
    # periodo existe no BP mas ainda nao foi importado o DRE correspondente
    # -- margens/ROA/ROE ficam NaN nesse periodo, mas Liquidez/Endividamento
    # continuam calculaveis (nao dependem do DRE).
    tabela = indicadores.calcular_indicadores(LANCS_BP, [])
    assert len(tabela) == 2
    jun = tabela.loc[__import__("pandas").Timestamp(P_JUN)]
    assert abs(jun["Liquidez Corrente"] - 2.0) < 1e-6
    import pandas as pd
    assert pd.isna(jun["Margem Bruta"])
    print("OK: BP sem DRE correspondente não quebra — indicadores de BP calculam, os de DRE ficam NaN")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO (não AssertionError) em {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
