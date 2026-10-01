# -*- coding: utf-8 -*-
import sys
import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import visao_grupo  # noqa: E402
import visao_grupo_graficos as g  # noqa: E402

P1, P2 = datetime.date(2026, 3, 31), datetime.date(2026, 6, 30)


def _lancs(valores_por_cod):
    """valores_por_cod: {cod: {(tipo, conta): valor}} -> listas bp/dre (periodo P2, trimestral)."""
    bp, dre = [], []
    for cod, contas in valores_por_cod.items():
        for (tipo, conta), v in contas.items():
            r = dict(empresa_codigo=cod, periodo=P2, grupo="G", conta=conta, valor=v, granularidade="trimestral")
            (bp if tipo == "BP" else dre).append(r)
    return bp, dre


BASE = {
    ("DRE", "RECEITA OPERACIONAL LIQUIDA"): 1000.0, ("DRE", "LUCRO BRUTO"): 800.0, ("DRE", "LUCRO LIQUIDO DO EXERCICIO"): -50.0,
    ("DRE", "LUCRO OPERACIONAL LIQUIDO"): -10.0,
    ("BP", "TOTAL DO ATIVO"): 5000.0, ("BP", "TOTAL DO PASSIVO"): 5000.0,
    ("BP", "TOTAL CIRCULANTE ATIVO"): 800.0, ("BP", "TOTAL CIRCULANTE PASSIVO"): 1000.0,
    ("BP", "TOTAL NAO CIRCULANTE PASSIVO"): 1000.0, ("BP", "TOTAL PATRIMONIO LIQUIDO"): 3000.0,
}


def test_resumo_por_empresa_nan_quando_sem_documento():
    bp, dre = _lancs({"ENERGIA": BASE, "SMG": {**BASE, ("DRE", "RECEITA OPERACIONAL LIQUIDA"): 500.0}})
    df = visao_grupo.montar_resumo_por_empresa(bp, dre, ["ENERGIA", "SMG", "CONST"], P2, "trimestral")
    assert df.loc["ENERGIA", "Receita Líquida"] == 1000.0 and df.loc["SMG", "Receita Líquida"] == 500.0
    assert df.loc["CONST"].isna().all(), "empresa sem documento nao pode virar 0"
    # outra granularidade nao casa
    df2 = visao_grupo.montar_resumo_por_empresa(bp, dre, ["ENERGIA"], P2, "semestral")
    assert df2.loc["ENERGIA"].isna().all()


def test_destaques_factuais():
    bp, dre = _lancs({"ENERGIA": BASE, "SMG": BASE})
    cods = ["ENERGIA", "SMG"]
    ind = visao_grupo.montar_indicadores_grupo(bp, dre, cods, [P2], "trimestral")
    sdre = visao_grupo.montar_serie_kpis_grupo(dre, cods, visao_grupo.CONTAS_KPI_DRE, [P2], granularidade="trimestral")
    sbp = visao_grupo.montar_serie_kpis_grupo(bp, cods, visao_grupo.CONTAS_KPI_BP, [P2], granularidade="trimestral")
    res = visao_grupo.montar_resumo_por_empresa(bp, dre, cods, P2, "trimestral")
    d = visao_grupo.gerar_destaques(ind, sdre, sbp, res, P2, None, {"ENERGIA": "Enermais Energia", "SMG": "SMG"})
    textos = " ".join(t for _n, t in d)
    assert "Receita líquida de R$ 2 mil" not in textos  # sem periodo anterior nao ha' frase de variacao
    assert "Liquidez corrente" in textos  # 1600/2000 = 0,8x < 1
    assert "EBITDA negativo no consolidado" in textos and "Empresas com EBITDA negativo no período" in textos
    assert any(n == "atencao" for n, _t in d)


def test_figuras_constroem_em_claro_e_escuro_sem_dados_parciais():
    idx = pd.DatetimeIndex([pd.Timestamp(P1), pd.Timestamp(P2)], name="periodo")
    sdre = pd.DataFrame({"RECEITA OPERACIONAL LIQUIDA": [100.0, 150.0], "LUCRO BRUTO": [80.0, 90.0],
                         "LUCRO LIQUIDO DO EXERCICIO": [-5.0, 7.0]}, index=idx)
    ind = pd.DataFrame({"EBITDA": [float("nan"), 12.0], "Margem Bruta": [0.8, 0.6], "Margem EBITDA": [float("nan"), 0.08],
                        "Margem Líquida": [-0.05, 0.04], "Capital de Giro": [-10.0, 20.0],
                        "Liquidez Corrente": [0.9, 1.2], "Endividamento Geral": [0.5, 0.6]}, index=idx)
    for dark in (False, True):
        assert len(g.fig_resultado_por_periodo(sdre, ind, "trimestral", dark).data) == 3
        assert len(g.fig_margens(ind, "trimestral", dark).data) == 2
        assert g.fig_capital_giro(ind, "trimestral", dark).data[0].x == ("1T/2026", "2T/2026")
        assert g.fig_linha_unica(ind["Liquidez Corrente"], "Liquidez corrente", "trimestral", dark, "x", 1.0)
        assert g.fig_linha_unica(ind["Endividamento Geral"], "Endividamento", "trimestral", dark, "pct")
    res = pd.DataFrame({"EBITDA": [10.0, -5.0, float("nan")]}, index=["A", "B", "C"])
    f = g.fig_contribuicao(res, "EBITDA", {"A": "Alfa", "B": "Beta"}, True)
    assert list(f.data[0].y) == ["Beta", "Alfa"]  # ordenado, NaN fora
    assert f.data[0].marker.color[0] == g.cores(True)["laranja"], "negativo em laranja"


def test_ticks_moeda_pt_br_e_inclui_zero():
    tv, tt = g._ticks_moeda([-1_200_000.0, 42_800_000.0])
    assert 0 in tv and "0" in tt
    assert any("mi" in t for t in tt) and not any("M" in t for t in tt)
    assert g._ticks_moeda([]) == ([], [])


def test_linha_do_tempo_com_bases_misturadas_usa_rotulos_proprios():
    """01/10/2026: anual 2025 + trimestral 2T/2026 no mesmo grafico, cada ponto com o
    rotulo do seu documento (indice posicional, mesmo mes pode ter 2 bases)."""
    dre = pd.DataFrame({"RECEITA OPERACIONAL LIQUIDA": [1000.0, 300.0], "LUCRO LIQUIDO DO EXERCICIO": [100.0, -20.0]})
    ind = pd.DataFrame({"EBITDA": [150.0, -10.0], "Margem EBITDA": [0.15, -0.03], "Margem Líquida": [0.1, -0.06],
                        "Capital de Giro": [50.0, -5.0], "Liquidez Corrente": [1.2, 0.9], "Endividamento Geral": [0.5, 0.6]})
    rot = ["2025", "2T/2026"]
    for dark in (True, False):
        f1 = g.fig_resultado_por_periodo(dre, ind, "", dark, rotulos=rot)
        assert list(f1.data[0].x) == rot
        assert list(g.fig_margens(ind, "", dark, rotulos=rot).data[0].x) == rot
        assert list(g.fig_capital_giro(ind, "", dark, rotulos=rot).data[0].x) == rot
        assert list(g.fig_linha_unica(ind["Liquidez Corrente"], "Liq", "", dark, "x", 1.0, rotulos=rot).data[0].x) == rot
