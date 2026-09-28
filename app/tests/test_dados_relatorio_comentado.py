# -*- coding: utf-8 -*-
"""
Testes de app/dados_relatorio_comentado.py (Fase 2, 28/09/2026).

Nao bate em banco real -- mocka db.* e indicadores.calcular_indicadores
diretamente (essas 2 camadas ja tem teste proprio, aqui so' testamos a
MONTAGEM/conversao de sinal do dict pro gerador). Os valores usados sao
os mesmos da fixture BASE de test_gerador_completo.py, "de tras pra
frente" (a partir do valor ja convertido, reconstroi o valor bruto como
ele chega do banco) -- serve tambem de teste de regressao da conversao
de sinal (ver docstring de sinal em dados_relatorio_comentado.py).
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402

PERIODO = datetime.date(2026, 6, 30)
PERIODO_ANTERIOR = datetime.date(2025, 12, 31)

EMPRESAS = [{"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"}]


def _lanc(grupo, conta, valor):
    return {"grupo": grupo, "conta": conta, "valor": valor}


BP_PERIODO = [
    _lanc("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 18_239_216.72),
    _lanc("ATIVO CIRCULANTE", "DISPONIVEL", 100.0),
    _lanc("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", 25_119_864.22),
    _lanc("ATIVO NAO CIRCULANTE", "IMOBILIZADO", 19_815_256.50),
    _lanc("TOTAL", "TOTAL DO ATIVO", 43_359_080.94),
    _lanc("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 18_338_124.77),
    _lanc("PASSIVO CIRCULANTE", "FORNECEDORES", 200.0),
    _lanc("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 18_249_041.92),
    _lanc("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", 6_771_914.25),
    _lanc("TOTAL", "TOTAL DO PASSIVO", 43_359_080.94),
]

DRE_PERIODO = [
    _lanc("RECEITAS", "RECEITA OPERACIONAL BRUTA", 36_674_150.0),
    _lanc("DEDUCOES", "DEDUCOES DA RECEITA BRUTA", -3_321_000.0),
    _lanc("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 33_353_150.0),
    _lanc("CUSTOS", "CUSTO DOS PRODUTOS/SERVICOS", -3_899_607.99),
    _lanc("RESULTADO", "LUCRO BRUTO", 29_453_542.01),
    _lanc("DESPESAS", "DESPESAS OPERACIONAIS", -30_559_591.64),
    _lanc("DESPESAS", "ADMINISTRATIVAS", -29_339_591.64),
    _lanc("DESPESAS", "DESPESAS FINANCEIRAS", -1_139_497.69),
    _lanc("DESPESAS", "DESPESAS TRIBUTARIAS", -80_502.31),
    _lanc("DESPESAS", "PROVISAO CSLL", -62_900.81),
    _lanc("DESPESAS", "PROVISAO IRPJ", -168_724.46),
    _lanc("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", -1_337_674.90),
    _lanc("DESPESAS", "DEPRECIACOES", -691_527.25),
]

ITENS_ADMIN = [("Serviços Profissionais", -11_401_300.0), ("Salários e Ordenados", -4_060_000.0)]

INDIC_ROW = {
    "Liquidez Corrente": 0.99, "Alavancagem": 5.40, "Endividamento Geral": 0.844,
    "Margem Bruta": 0.883, "Margem Líquida": -0.04, "EBITDA": 724_975.31, "Margem EBITDA": 0.02174,
}


def _indic_df(periodos_valores: dict) -> pd.DataFrame:
    """periodos_valores: {periodo: {coluna: valor}}."""
    idx = pd.to_datetime(sorted(periodos_valores.keys()))
    df = pd.DataFrame(
        [periodos_valores[d] for d in sorted(periodos_valores.keys())],
        index=idx,
    )
    df.index.name = "periodo"
    return df


def _patches(periodos_ativos=None, dre_hist=None, indic_df=None):
    periodos_ativos = periodos_ativos if periodos_ativos is not None else [PERIODO]
    dre_hist = dre_hist if dre_hist is not None else DRE_PERIODO
    indic_df = indic_df if indic_df is not None else _indic_df({PERIODO: INDIC_ROW})
    return [
        patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS),
        patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO":
              BP_PERIODO if tipo == "BP" else DRE_PERIODO),
        patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
              [dict(periodo=PERIODO, **{k: v2 for k, v2 in x.items() if k != "grupo"}, grupo=x["grupo"]) for x in BP_PERIODO] if tipo == "BP"
              else [dict(x, periodo=PERIODO) for x in dre_hist]),
        patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN),
        patch("dados_relatorio_comentado.db.listar_periodos", return_value=periodos_ativos),
        patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=indic_df),
    ]


def _montar(**kwargs):
    patches = _patches(**{k: kwargs.pop(k) for k in ("periodos_ativos", "dre_hist", "indic_df") if k in kwargs})
    ctxs = [p.start() for p in patches]
    try:
        return drc.montar_dados_relatorio(conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026", **kwargs)
    finally:
        for p in patches:
            p.stop()


def test_conversao_de_sinal_bate_com_fixture_do_gerador():
    dados, incluir_resultado = _montar()
    assert incluir_resultado is True
    assert dados["receita_bruta"] == 36_674_150.0
    assert dados["deducoes_receita"] == 3_321_000.0
    assert round(dados["deducoes_pct_bruta"], 4) == round(3_321_000.0 / 36_674_150.0, 4)
    assert dados["receita_liquida"] == 33_353_150.0
    assert dados["custo_servicos"] == 3_899_607.99
    assert dados["lucro_bruto"] == 29_453_542.01
    assert dados["despesas_operacionais"] == 30_559_591.64
    assert dados["despesas_administrativas"] == 29_339_591.64
    assert round(dados["despesas_financeiras"], 2) == 1_139_497.69
    assert dados["despesas_tributarias"] == 80_502.31
    # os 3 componentes de despesa tem que fechar com despesas_operacionais
    assert round(dados["despesas_administrativas"] + dados["despesas_financeiras"] + dados["despesas_tributarias"], 2) == dados["despesas_operacionais"]
    assert round(dados["csll_irpj"], 2) == 231_625.27
    assert dados["resultado_liquido"] == -1_337_674.90
    assert round(dados["resultado_financeiro"], 2) == 1_139_497.69
    assert round(dados["deprec_amortiz"], 2) == 691_527.25
    assert dados["ebitda"] == 724_975.31
    assert dados["margem_ebitda"] == 0.02174
    assert dados["total_ativo"] == 43_359_080.94
    assert dados["imobilizado"] == 19_815_256.50
    assert dados["liquidez_corrente"] == 0.99
    assert dados["alavancagem"] == 5.40
    print("OK: conversao de sinal do banco pro gerador bate com a fixture BASE (valor a valor)")


def test_despesas_admin_itens_ordenado_e_percentual_correto():
    dados, _ = _montar()
    itens = dados["despesas_admin_itens"]
    assert itens[0][0] == "Serviços Profissionais"
    assert itens[0][1] == 11_401_300.0
    assert round(itens[0][2], 1) == 38.9
    assert round(itens[1][2], 1) == 13.8
    print("OK: despesas_admin_itens vem em magnitude positiva, ordenado desc, percentual sobre despesas_administrativas")


def test_sem_csll_irpj_pula_pagina_resultado_e_nao_inventa_campo():
    dre_sem_csll = [r for r in DRE_PERIODO if r["conta"] not in ("PROVISAO CSLL", "PROVISAO IRPJ")]
    dados, incluir_resultado = _montar(dre_hist=dre_sem_csll)
    # a fixture patch de listar_lancamentos ainda devolve o DRE_PERIODO fixo
    # (com csll) -- o que importa aqui e' o comportamento quando os 2
    # PROVISAO_* nao existem NO PERIODO buscado por listar_lancamentos.
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO":
               BP_PERIODO if tipo == "BP" else dre_sem_csll), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in BP_PERIODO] if tipo == "BP" else [dict(x, periodo=PERIODO) for x in dre_sem_csll]), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=_indic_df({PERIODO: INDIC_ROW})):
        dados2, incluir_resultado2 = drc.montar_dados_relatorio(
            conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
        )
    assert incluir_resultado2 is False
    assert "csll_irpj" not in dados2
    print("OK: sem PROVISAO CSLL/IRPJ no periodo, incluir_pagina_resultado=False e csll_irpj nao entra no dict (nao inventa 0)")


def test_anexo_ativo_tem_grupo_conta_subtotal_total_na_ordem():
    dados, _ = _montar()
    tipos = [linha[0] for linha in dados["anexo_ativo"]]
    assert tipos == ["grupo", "conta", "subtotal", "conta", "subtotal", "total"]
    assert dados["anexo_ativo"][-1] == ("total", "TOTAL DO ATIVO", 43_359_080.94)
    print("OK: anexo_ativo monta a arvore grupo/conta/subtotal/total na ordem esperada pelo gerador")


def test_sem_periodo_anterior_cai_no_fallback_neutro():
    dados, _ = _montar(periodos_ativos=[PERIODO])
    assert "sem período anterior" in dados["complemento_receita"]
    assert "sem período anterior" in dados["complemento_ebitda"]
    print("OK: 1a importacao (sem periodo anterior) usa fallback neutro, nao inventa comparacao")


def test_com_periodo_anterior_gera_texto_comparativo():
    dre_hist_2p = [dict(x, periodo=PERIODO) for x in DRE_PERIODO] + [
        dict(_lanc("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 30_000_000.0), periodo=PERIODO_ANTERIOR),
        dict(_lanc("DESPESAS", "DESPESAS OPERACIONAIS", -25_000_000.0), periodo=PERIODO_ANTERIOR),
    ]
    indic_df_2p = _indic_df({PERIODO: INDIC_ROW, PERIODO_ANTERIOR: {**INDIC_ROW, "EBITDA": 500_000.0}})
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO":
               BP_PERIODO if tipo == "BP" else DRE_PERIODO), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in BP_PERIODO] if tipo == "BP" else dre_hist_2p), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO_ANTERIOR, PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=indic_df_2p):
        dados, _ = drc.montar_dados_relatorio(
            conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
        )
    assert "período anterior" not in dados["complemento_receita"] or "sem período anterior" not in dados["complemento_receita"]
    assert "%" in dados["complemento_receita"]
    assert "%" in dados["complemento_ebitda"]
    print("OK: com periodo anterior disponivel, gera texto comparativo percentual (nao o fallback)")


def test_pipeline_completo_nao_quebra_gerando_pdf_de_verdade():
    import tempfile
    import os
    dados, incluir_resultado = _montar()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "real.pdf")
        g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=incluir_resultado)
        assert os.path.getsize(caminho) > 5000
    print("OK: dados montados a partir do 'banco' (mockado) geram PDF completo sem excecao")


if __name__ == "__main__":
    test_conversao_de_sinal_bate_com_fixture_do_gerador()
    test_despesas_admin_itens_ordenado_e_percentual_correto()
    test_sem_csll_irpj_pula_pagina_resultado_e_nao_inventa_campo()
    test_anexo_ativo_tem_grupo_conta_subtotal_total_na_ordem()
    test_sem_periodo_anterior_cai_no_fallback_neutro()
    test_com_periodo_anterior_gera_texto_comparativo()
    test_pipeline_completo_nao_quebra_gerando_pdf_de_verdade()
