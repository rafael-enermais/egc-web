# -*- coding: utf-8 -*-
"""v0.52.0 -- leitura contabil do dashboard (creditos_analise): sugestoes, composicao, comparativos e ponte. Funcoes puras, sem banco."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import creditos_analise as an  # noqa: E402
import creditos_db as cdb  # noqa: E402
import creditos_graficos as gr  # noqa: E402


def _fonte(chave="HIST_APONTADO", cred=(100.0, 400.0), cat=None, **resumo):
    r = {"credito_pis": cred[0], "credito_cofins": cred[1], "debito_nc_pis": 300.0, "debito_nc_cofins": 1400.0, "debito_cum_pis": 10.0,
         "debito_cum_cofins": 40.0, "credito_usado_pis": 100.0, "credito_usado_cofins": 400.0, "credito_anterior_pis": 0.0, "credito_anterior_cofins": 0.0,
         "saldo_pis": 0.0, "saldo_cofins": 0.0, "retencao_nc_pis": 1.0, "retencao_nc_cofins": 4.0, "a_recolher_pis": 209.0, "a_recolher_cofins": 1036.0,
         "receita_nc": 50000.0, "receita_cum": 1000.0}
    r.update(resumo)
    return {"chave": chave, "tipo": "HISTORICO", "sped_tipo": "Retificadora", "rotulo": chave, "resumo": r,
            "por_categoria": cat if cat is not None else {"Aluguel": {"pis": cred[0] * 0.45, "cofins": cred[1] * 0.45}, "Combustível": {"pis": cred[0] * 0.55, "cofins": cred[1] * 0.55}},
            "por_natureza": {}, "dif_fornecedores": []}


def _serie(creds, tributo="total", **kw):
    linhas = [cdb._linha_serie(f"{m:02d}/2026", _fonte(cred=c, **kw), None, "padrao", 1, {}) for m, c in enumerate(creds, 1)]
    return gr.preparar_serie(pd.DataFrame(linhas), tributo)


def test_composicao_percentuais_somam_um_e_ordenam():
    c = an.composicao({"A": {"pis": 10, "cofins": 40}, "B": {"pis": 30, "cofins": 120}, "C": {"pis": 0, "cofins": 0}})
    assert list(c["categoria"]) == ["B", "A", "C"] and abs(c["pct"].sum() - 1) < 1e-9 and round(c.iloc[0]["pct"], 2) == 0.75
    assert an.composicao({"A": {"pis": 10, "cofins": 40}}, "pis").iloc[0]["valor"] == 10
    assert an.composicao({}).empty


def test_variacao_categorias_ordena_pela_maior_mudanca():
    v = an.variacao_categorias({"A": {"pis": 50, "cofins": 0}, "N": {"pis": 5, "cofins": 0}}, {"A": {"pis": 10, "cofins": 0}, "B": {"pis": 30, "cofins": 0}})
    assert list(v["categoria"]) == ["A", "B", "N"] and v.iloc[0]["dif"] == 40 and v.iloc[1]["dif"] == -30
    assert v.iloc[2]["pct"] is None or pd.isna(v.iloc[2]["pct"])          # nova categoria: sem % (anterior zero)


def test_comparar_fontes_apuracao_e_categorias():
    a = _fonte(cred=(100, 400))
    b = _fonte("ENV:x", cred=(110, 380))
    t = an.comparar_fontes(a, b)
    cred = t[(t["grupo"] == "Apuração") & (t["item"] == "Crédito apurado no mês")].iloc[0]
    assert cred["a"] == 500 and cred["b"] == 490 and cred["dif"] == -10 and round(cred["pct"], 4) == -0.02
    assert set(t[t["grupo"] == "Categoria"]["item"]) == {"Aluguel", "Combustível"}
    assert an.comparar_fontes(a, b, "pis").iloc[0]["a"] == 100


def test_comparar_sped_detalhamento():
    d = an.comparar_sped_detalhamento({"A": {"pis": 10, "cofins": 40}, "Deprec": {"pis": 5, "cofins": 20}}, {"A": {"pis": 10, "cofins": 40}})
    assert d.set_index("categoria").loc["Deprec", "dif"] == -25 and d.set_index("categoria").loc["A", "dif"] == 0


def test_ponte_so_lista_meses_que_mudaram():
    base, cen = _serie([(100, 400)] * 3), _serie([(100, 400), (120, 400), (100, 400)])
    p = an.ponte(base, cen)
    assert p["inicio"] == 1500 and p["fim"] == 1520 and p["itens"] == [("fev/26", 20.0)]
    assert an.ponte(base, base)["itens"] == []
    assert an.ponte(pd.DataFrame(), base)["itens"] == []


def test_sem_historico_nao_sugere_variacao_e_so_lembra_conferido():
    d = _serie([(100, 400), (100, 400)])
    titulos = [x["titulo"] for x in an.sugestoes_mes(d, 1)]
    assert titulos == ["Mês ainda não marcado como conferido"]


def test_sugere_conferir_credito_fora_da_media_e_cita_categoria():
    d = _serie([(100, 400)] * 4 + [(200, 800)])
    s = an.sugestoes_mes(d, 4)
    fora = [x for x in s if x["titulo"].startswith("Crédito fora do padrão")][0]
    assert fora["nivel"] == "atencao" and "100%" in fora["texto"] and "acima" in fora["texto"] and "Vale conferir" in fora["texto"]
    assert "Combustível" in fora["texto"]
    assert not [x for x in an.sugestoes_mes(d, 3) if x["titulo"].startswith("Crédito fora do padrão")]


def test_sugere_saldo_do_mes_anterior_sem_uso():
    d = _serie([(100, 400), (100, 400)])
    d.loc[0, "saldo_total"] = 300.0
    d.loc[1, "anterior_pis"] = 0.0
    s = [x for x in an.sugestoes_mes(d, 1) if x["titulo"].startswith("Saldo de crédito")]
    assert s and "R$ 300,00" in s[0]["texto"]
    d.loc[1, "anterior_pis"] = 50.0                                        # usou saldo anterior -> nao sugere
    assert not [x for x in an.sugestoes_mes(d, 1) if x["titulo"].startswith("Saldo de crédito")]


def test_sugere_usado_acima_do_debito():
    d = _serie([(100, 400)])
    d.loc[0, "usado_total"] = d.loc[0, "deb_nc_total"] + 500
    assert [x for x in an.sugestoes_mes(d, 0) if x["titulo"].startswith("Crédito utilizado acima")]


def test_sugere_diferenca_do_original_e_concentracao():
    lin = cdb._linha_serie("01/2026", _fonte(cred=(100, 400), cat={"A": {"pis": 90, "cofins": 360}, "B": {"pis": 10, "cofins": 40}}),
                           _fonte("HIST_ORIGINAL", cred=(80, 320)), "padrao", 2, {})
    d = gr.preparar_serie(pd.DataFrame([lin]))
    t = [x["titulo"] for x in an.sugestoes_mes(d, 0)]
    assert "Versão em uso difere do SPED original" in t and "Crédito concentrado em uma categoria" in t and "Este mês tem mais de uma versão" in t


def test_simulacao_avisa_que_nao_e_oficial_e_nao_pede_para_marcar_versao():
    lin = cdb._linha_serie("01/2026", _fonte(), None, "simulada", 3, {"conferido": True})
    t = an.sugestoes_mes(gr.preparar_serie(pd.DataFrame([lin])), 0)
    assert [x["titulo"] for x in t] == ["Você está vendo uma simulação"]


def test_nenhuma_sugestao_e_bloqueio():
    # contrato: so' texto + nivel; nunca ha' campo que impeça uma acao
    d = _serie([(100, 400)] * 4 + [(500, 2000)])
    for x in an.sugestoes_mes(d, 4):
        assert set(x) == {"nivel", "titulo", "texto"} and x["nivel"] in ("atencao", "info")


def test_resumo_variacao():
    d = _serie([(100, 400), (150, 600)])
    r = an.resumo_variacao(d, 1)
    assert r["mes"] == "fev/26" and r["mes_anterior"] == "jan/26" and r["cred_total"]["dif"] == 250 and round(r["cred_total"]["pct"], 2) == 0.5
    assert an.resumo_variacao(d, 0)["cred_total"]["dif"] is None


def test_graficos_novos_nao_quebram():
    base, cen = _serie([(100, 400)] * 3), _serie([(100, 400), (120, 400), (100, 400)])
    assert len(gr.fig_ponte(an.ponte(base, cen), True).data) == 1
    assert "nenhum mês" in gr.fig_ponte(an.ponte(base, base), False).layout.annotations[0].text
    assert len(gr.fig_composicao(an.composicao({"A": {"pis": 1, "cofins": 2}}), False).data) == 1
    t = an.comparar_fontes(_fonte(), _fonte("ENV:x", cred=(90, 360)))
    assert len(gr.fig_comparacao_categorias(t[t["grupo"] == "Categoria"], True, "A", "B").data) == 2
