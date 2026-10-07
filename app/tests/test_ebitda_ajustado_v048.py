# -*- coding: utf-8 -*-
"""v0.48.0 -- EBITDA Ajustado (paginas nova e evolutiva), regra de cor dos graficos e nao recorrentes
(logica pura). A parte que fala com Postgres de verdade esta em test_ebitda_ajustado_v048_pg.py."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
import nao_recorrentes as nr  # noqa: E402
import dados_relatorio_comentado as drc  # noqa: E402
from test_gerador_completo import BASE  # noqa: E402
from test_gerador_relatorio_comparativo import BASE as BASE_COMP  # noqa: E402


def _ponte(rol, rl, cs, out, fin, da):
    lop = rl + cs - out
    return dict(receita_liquida=rol, resultado_liquido=rl, csll_irpj=cs, outros_resultados=out, lucro_operacional=lop,
                resultado_financeiro=fin, ebit=lop + fin, deprec_amortiz=da, ebitda=lop + fin + da)


PT_1S26 = _ponte(33_350_846.80, -1_337_674.90, 231_625.27, 0.0, 1_139_497.69, 691_527.25)
NR_CONF = {"status": "confirmado", "total": 111_026.64, "pendentes": [],
           "itens": [("Rescisões e indenizações", 84_236.69), ("Jurídico pontual", 26_000.0), ("Multas e penalidades", 789.95)]}
NR_PEND = {"status": "pendente", "itens": [], "total": 0.0, "pendentes": ["ENERGIA"]}


def _dados(nr_=NR_CONF, **kw):
    return dict(BASE, ponte_ebitda=PT_1S26, nao_recorrentes=nr_, incluir_ebitda_ajustado=True, **kw)


def _texto_paginas(caminho):
    import pdfplumber
    with pdfplumber.open(caminho) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def _pdf(dados, tmp, nome="r.pdf", **kw):
    caminho = os.path.join(tmp, nome)
    g.gerar_pdf_completo(dados, caminho, **kw)
    return caminho


# ───────────────────────── ponte do EBITDA (matematica) ─────────────────────────
def test_ponte_fecha_com_o_ebitda_do_app():
    assert round(PT_1S26["ebitda"], 2) == 724_975.31
    assert round(PT_1S26["lucro_operacional"], 2) == -1_106_049.63
    linhas = g._linhas_ponte(PT_1S26, True, 111_026.64)
    rotulos = [r for r, _v, _d in linhas]
    assert rotulos[-1] == "(=) EBITDA ajustado" and "(+) Não recorrentes" in rotulos
    assert round(linhas[-1][1], 2) == 836_001.95


def test_ponte_lucro_presumido_sem_csll_e_com_outros_resultados():
    pt = _ponte(15_565_767.10, 3_432_098.83, 0.0, 13_863.02, 15_288.49, 264_455.94)
    rotulos = [r for r, _v, _d in g._linhas_ponte(pt, False, 0.0)]
    assert "(+) CSLL e IRPJ provisionados" not in rotulos           # nao inventa linha que nao existe
    assert "(-) Outros resultados operacionais" in rotulos
    assert "(=) Lucro operacional líquido" in rotulos
    assert "(+) Não recorrentes" not in rotulos and "(=) EBITDA ajustado" not in rotulos   # sem lista confirmada


def test_ponte_sem_csll_e_sem_outros_nao_repete_linha_de_lucro_operacional():
    pt = _ponte(1e6, 100_000.0, 0.0, 0.0, 10_000.0, 5_000.0)
    rotulos = [r for r, _v, _d in g._linhas_ponte(pt, False, 0.0)]
    assert not any("Lucro operacional" in r or "antes de CSLL" in r for r in rotulos)


def test_leitura_ajustado_mesma_frase_com_lucro_ou_prejuizo():
    import re
    sem_num = lambda t: re.sub(r"[-+]?R\$\s*[\d.,]+|[-+]?[\d.,]+%|[-+]?[\d.,]+", "", t)
    pos = dict(_dados(), ponte_ebitda=_ponte(1e7, 500_000.0, 0.0, 0.0, 10_000.0, 50_000.0))
    neg = dict(_dados(), ponte_ebitda=_ponte(1e7, -500_000.0, 0.0, 0.0, 10_000.0, 50_000.0))
    a = g._leitura_ebitda_ajustado(pos, True, 20_000.0)
    b = g._leitura_ebitda_ajustado(neg, True, 20_000.0)
    assert [sem_num(x) for x in a][0] == [sem_num(x) for x in b][0]


def test_sem_confirmacao_a_leitura_nao_apresenta_ajustado():
    txt = " ".join(g._leitura_ebitda_ajustado(_dados(NR_PEND), False, 0.0))
    assert "não foram confirmados" in txt and "EBITDA ajustado não é apresentado" in txt


# ───────────────────────── paginas no PDF ─────────────────────────
def test_pagina_entra_depois_do_ebitda_com_ponte_e_nao_entra_sem():
    d = _dados(variante="gerencial")
    lista = g.paginas_do_relatorio(d)
    assert lista.index(g.pagina_ebitda_ajustado) == lista.index(g.pagina_ebitda) + 1
    assert g.pagina_ebitda_ajustado not in g.paginas_do_relatorio(dict(BASE))               # dict antigo: sem ponte
    assert g.pagina_ebitda_ajustado not in g.paginas_do_relatorio(dict(d, incluir_ebitda_ajustado=False))


def test_fornecedor_8_paginas_e_gerencial_9_com_a_nova_pagina():
    # Fornecedor 7 (sem Despesas) + 1; Gerencial 8 + 1 -- com a pagina de Resultado incluida
    assert g.numero_de_paginas(_dados(variante="fornecedor")) == g.numero_de_paginas(dict(BASE, variante="fornecedor")) + 1
    assert g.numero_de_paginas(_dados(variante="gerencial")) == g.numero_de_paginas(dict(BASE, variante="gerencial")) + 1


def test_pdf_com_lista_confirmada_mostra_ajustado_e_numeros_inteiros():
    with tempfile.TemporaryDirectory() as tmp:
        d = _dados(variante="fornecedor")
        paginas = _texto_paginas(_pdf(d, tmp))
        assert len(paginas) == g.numero_de_paginas(d)
        txt = paginas[[i for i, t in enumerate(paginas) if "EBITDA ajustado" in t][0]]
        assert "R$ 836.002" in txt and "R$ 724.975" in txt and "R$ 111.027" in txt
        assert "Abertura do não recorrente" in txt and "Rescisões e indenizações" in txt
        assert "Página 6 de 9" in txt   # logo depois da pagina do EBITDA (Fornecedor: capa, destaques, receita, resultado, ebitda, AJUSTADO, balanco, anexo...)


def test_pdf_sem_confirmacao_mostra_so_ate_ebitda_contabil():
    with tempfile.TemporaryDirectory() as tmp:
        paginas = _texto_paginas(_pdf(_dados(NR_PEND, variante="gerencial"), tmp))
        txt = next(t for t in paginas if "Reconciliação do EBITDA" in t and "Leitura da Reconciliação" in t)
        plano = " ".join(txt.split())
        assert "EBITDA ajustado" not in plano.replace("por isso o EBITDA ajustado não é apresentado", "")
        assert "Abertura do não recorrente" not in plano
        assert "R$ 724.975" in txt


def test_pdf_lista_confirmada_vazia_mostra_ajustado_igual_ao_contabil():
    vazio = {"status": "confirmado", "itens": [], "total": 0.0, "pendentes": []}
    with tempfile.TemporaryDirectory() as tmp:
        paginas = _texto_paginas(_pdf(_dados(vazio), tmp))
        txt = next(t for t in paginas if "Leitura do EBITDA Ajustado" in t)
        assert "Nenhum item não recorrente confirmado" in txt


def test_prejuizo_e_lucro_geram_a_pagina_sem_quebrar():
    for rl in (-2_000_000.0, 2_000_000.0):
        pt = _ponte(1e7, rl, 0.0, 0.0, 100_000.0, 200_000.0)
        with tempfile.TemporaryDirectory() as tmp:
            _pdf(dict(_dados(), ponte_ebitda=pt, ebitda=pt["ebitda"]), tmp)


# ───────────────────────── regra de cor (azul degrade; laranja so' negativo) ─────────────────────────
def _canvas(tmp):
    from reportlab.pdfgen import canvas
    g._registrar_fontes()
    return canvas.Canvas(os.path.join(tmp, "c.pdf"), pagesize=(g.PAGE_W, g.PAGE_H))


def test_cor_helpers():
    assert g.cor_barra_por_sinal(10.0, "#123456") == "#123456"
    assert g.cor_barra_por_sinal(-1.0, "#123456") == g.NEG_FILL
    assert g.cor_texto_por_sinal(-1.0) == g.NEG_TEXT and g.cor_texto_por_sinal(5.0) == g.NAVY
    assert g.BLUE_DEGRADE[0] == "#B4B9D6" and g.BLUE_DEGRADE[-1] == g.NAVY and len(g.BLUE_DEGRADE) == 6


def test_ranking_de_despesas_e_azul_degrade_sem_laranja_maior_mais_escuro():
    with tempfile.TemporaryDirectory() as tmp:
        c = _canvas(tmp)
        itens = [dict(label=f"c{i}", pct=50 - 10 * i, rotulo_valor="x") for i in range(5)]
        fills = []
        original = g.rect
        def spy(c_, x0, y0, x1, y1, fill=None, **kw):
            fills.append(fill)
            return original(c_, x0, y0, x1, y1, fill=fill, **kw)
        with patch.object(g, "rect", spy):
            g.grafico_ranking_horizontal(c, 42, 550, 150, 26, itens)
    barras = [f for f in fills if f]
    assert g.ORANGE not in barras and g.NEG_FILL not in barras
    assert barras[0].upper() == g.NAVY.upper()          # maior = mais escuro


def _espiar(nome, chamar):
    """Roda `chamar()` espiando g.<nome> (grafico_waterfall / grafico_barra_empilhada) e devolve os itens passados."""
    capturado = []
    original = getattr(g, nome)
    def spy(c, *a, **k):
        capturado.append(a[-1])
        return original(c, *a, **k)
    with patch.object(g, nome, spy):
        chamar()
    return capturado[0]


def test_receita_lucro_bruto_azul_e_deducoes_custo_laranja():
    with tempfile.TemporaryDirectory() as tmp:
        itens = _espiar("grafico_waterfall", lambda: g.pagina_receita_custos(_canvas(tmp), dict(BASE), 3, 9))
    cores = {i["label"].replace("\n", " "): i["cor"] for i in itens}
    assert cores["Deduções da Receita"] == g.NEG_FILL and cores["Custo dos Serviços"] == g.NEG_FILL
    assert cores["Lucro Bruto"] == g.NAVY and cores["Receita Bruta"] in g.BLUE_DEGRADE and cores["Receita Líquida"] in g.BLUE_DEGRADE
    assert g.ORANGE not in (cores["Lucro Bruto"], cores["Receita Bruta"], cores["Receita Líquida"])


def test_ebitda_positivo_sem_laranja_e_negativo_so_nas_barras_negativas():
    with tempfile.TemporaryDirectory() as tmp:
        pos = dict(BASE, resultado_liquido=500_000.0, ebitda=900_000.0, csll_irpj=0.0)
        neg = dict(BASE, resultado_liquido=-500_000.0, ebitda=-900_000.0)
        i_pos = _espiar("grafico_waterfall", lambda: g.pagina_ebitda(_canvas(tmp), pos, 6, 9))
        i_neg = _espiar("grafico_waterfall", lambda: g.pagina_ebitda(_canvas(tmp), neg, 6, 9))
    assert all(i["cor"] != g.NEG_FILL for i in i_pos)
    assert [i["cor"] for i in i_pos if i["tipo"] == "abs"][-1] == g.NAVY                    # EBITDA positivo = azul escuro
    assert i_neg[0]["cor"] == g.NEG_FILL and i_neg[-1]["cor"] == g.NEG_FILL                 # resultado e EBITDA negativos
    assert all(i["cor"] != g.NEG_FILL for i in i_neg[1:-1])                                 # somas de volta (+) seguem azuis


def test_balanco_pl_positivo_azul_claro_e_negativo_laranja():
    with tempfile.TemporaryDirectory() as tmp:
        capt = []
        original = g.grafico_barra_empilhada
        def spy(c, x0, x1, y_top, altura, segmentos):
            capt.append(segmentos)
            return original(c, x0, x1, y_top, altura, segmentos)
        with patch.object(g, "grafico_barra_empilhada", spy):
            g.pagina_balanco(_canvas(tmp), dict(BASE), 7, 9)
            g.pagina_balanco(_canvas(tmp), dict(BASE, patrimonio_liquido=-1_000_000.0), 7, 9)
    assert [x["cor"] for x in capt[0]] == [g.BLUE_DEGRADE[5], g.BLUE_DEGRADE[3]]            # Ativo: azuis, sem laranja
    pl_pos, pl_neg = capt[1][-1], capt[3][-1]
    assert pl_pos["label"] == "Patrimônio Líquido" and pl_pos["cor"] == g.BLUE_DEGRADE[1]
    assert pl_neg["cor"] == g.NEG_FILL


def test_card_ebitda_do_destaques_branco_se_positivo_e_laranja_se_negativo():
    with tempfile.TemporaryDirectory() as tmp:
        usados = []
        original = g._kpi_grande
        def spy(*a, **k):
            usados.append((a[5], k.get("valor_negativo")))
            return original(*a, **k)
        with patch.object(g, "_kpi_grande", spy):
            g.pagina_destaques(_canvas(tmp), dict(BASE, ebitda=1.0), 2, 9)
            g.pagina_destaques(_canvas(tmp), dict(BASE, ebitda=-1.0), 2, 9)
    assert [v for _l, v in usados if _l == "EBITDA do Período"] == [False, True]


# ───────────────────────── evolutivo ─────────────────────────
def _evol(nrs, granularidades=None, labels=("2023", "2024", "2025", "1S/2026")):
    pts = [_ponte(3_327_615.35, 1_285_930.76, 0, 0, 4_297.31, 9_521.42),
           _ponte(15_565_767.10, 3_432_098.83, 0, 13_863.02, 15_288.49, 264_455.94),
           _ponte(49_429_478.90, 439_410.42, 0, 14_474.50, 254_284.74, 480_013.80), PT_1S26]
    n = len(labels)
    return dict(BASE_COMP, periodos_labels=list(labels), anexo_colunas=list(labels), periodo_range_label="x",
                kpis_fluxo=[{"label": "Receita Líquida", "tag": "fluxo", "valores": [1.0e6] * n, "acumulado": None}],
                kpis_saldo=[{"label": "Total do Ativo", "tag": "saldo", "valores": [2.0e6] * n}],
                grafico_evolucao_metricas=[{"label": "Receita Líquida", "valores": [1.0e6] * n}],
                granularidades=granularidades or ["anual"] * n,
                anexo_ativo=[("grupo", "ATIVO"), ("conta", "D", *([1] * n)), ("total", "TOTAL DO ATIVO", *([1] * n))],
                anexo_passivo=[("grupo", "PASSIVO"), ("conta", "F", *([1] * n)), ("total", "TOTAL PASSIVO + PL", *([1] * n))],
                ebitda_ajustado=dict(ponte=pts[-n:], nr=nrs[-n:]), incluir_ebitda_ajustado=True)


def _nr(itens):
    return {"status": "confirmado", "itens": itens, "total": round(sum(v for _, v in itens), 2)}


def test_comparativo_so_inclui_a_pagina_com_ponte_e_flag():
    d = _evol([_nr([])] * 4)
    assert gc.pagina_ebitda_ajustado_evolucao in gc.paginas_do_comparativo(d)
    assert gc.pagina_ebitda_ajustado_evolucao not in gc.paginas_do_comparativo(dict(d, incluir_ebitda_ajustado=False))
    assert gc.pagina_ebitda_ajustado_evolucao not in gc.paginas_do_comparativo(dict(BASE_COMP))
    lista = gc.paginas_do_comparativo(d)
    assert lista.index(gc.pagina_ebitda_ajustado_evolucao) == lista.index(gc.pagina_grafico_evolucao) + 1


def test_comparativo_pdf_tres_cenarios():
    cenarios = {
        "todos": [_nr([]), _nr([("Equivalência patrimonial", 891_341.08)]), _nr([("Equivalência patrimonial", 2_695_244.78)]), _nr([("Jurídico pontual", 26_000.0)])],
        "parcial": [NR_PEND, NR_PEND, _nr([("Rescisões e indenizações", 120_113.15)]), _nr([("Jurídico pontual", 26_000.0)])],
        "nenhum": [{"status": "indisponivel", "itens": [], "total": 0.0}] * 4,
    }
    for nome, nrs in cenarios.items():
        with tempfile.TemporaryDirectory() as tmp:
            d = _evol(nrs)
            caminho = os.path.join(tmp, f"{nome}.pdf")
            gc.gerar_pdf_comparativo(d, caminho)
            paginas = _texto_paginas(caminho)
            assert len(paginas) == gc.numero_de_paginas_comparativo(d)
            txt = paginas[3]
            assert "Reconciliação do EBITDA" in txt and "R$ 1.299.749" in txt      # EBITDA contabil 2023 (confere com o app)
            if nome == "nenhum":
                assert "EBITDA ajustado" not in " ".join(txt.split()).replace("o EBITDA ajustado não é apresentado", "")
            else:
                assert "(=) EBITDA ajustado" in txt


def test_comparativo_bases_diferentes_variacao_nao_comparavel():
    with tempfile.TemporaryDirectory() as tmp:
        d = _evol([_nr([])] * 4, granularidades=["anual", "anual", "anual", "semestral"])
        caminho = os.path.join(tmp, "x.pdf")
        gc.gerar_pdf_comparativo(d, caminho)
        txt = _texto_paginas(caminho)[3]
        assert "n/c" in txt and "bases diferentes" in txt
        d2 = _evol([_nr([])] * 4, granularidades=["anual"] * 4)
        gc.gerar_pdf_comparativo(d2, caminho)
        assert "bases diferentes" not in _texto_paginas(caminho)[3]


def test_var_txt_regras():
    assert gc._var_txt(100.0, 150.0) == "+50,0%"
    assert gc._var_txt(-5.0, 10.0) == "n/d" and gc._var_txt(5.0, -10.0) == "n/d" and gc._var_txt(0.0, 3.0) == "n/d"
    assert gc._var_txt(100.0, 150.0, comparavel=False) == "n/c"
    assert gc._var_txt(1.0, 100.0) == "> +999%"


def test_comparativo_2_periodos():
    with tempfile.TemporaryDirectory() as tmp:
        d = _evol([_nr([("Jurídico pontual", 1_000.0)])] * 2, labels=("2025", "1S/2026"), granularidades=["anual", "anual"])
        gc.gerar_pdf_comparativo(d, os.path.join(tmp, "x.pdf"))


# ───────────────────────── nao recorrentes (logica pura) ─────────────────────────
def test_assinatura_muda_com_qualquer_alteracao_e_ignora_removidos():
    base = [dict(id=1, categoria=nr.CATEGORIAS[0], valor=10.0, sinal=1, descricao="a", ativo=True)]
    a0 = nr.assinatura_lista(base)
    assert nr.assinatura_lista(base) == a0
    assert nr.assinatura_lista([dict(base[0], valor=10.01)]) != a0
    assert nr.assinatura_lista([dict(base[0], sinal=-1)]) != a0
    assert nr.assinatura_lista([dict(base[0], categoria=nr.CATEGORIAS[1])]) != a0
    assert nr.assinatura_lista(base + [dict(id=2, categoria=nr.CATEGORIAS[1], valor=1.0, sinal=1, descricao="", ativo=False)]) == a0


def test_validacao_de_item():
    with pytest.raises(ValueError):
        nr._validar("Categoria inventada", 10, 1)
    with pytest.raises(ValueError):
        nr._validar(nr.CATEGORIAS[0], 0, 1)
    with pytest.raises(ValueError):
        nr._validar(nr.CATEGORIAS[0], 10, 2)
    assert nr._validar(nr.CATEGORIAS[0], -10.456, -1) == (nr.CATEGORIAS[0], 10.46, -1)   # valor sempre em modulo


def test_sugestoes_so_olham_contas_acima_da_linha_e_nada_e_gravado():
    sug = nr.sugerir([("HONORARIOS ADVOCATICIOS", 26_000.0), ("Multas Indedutíveis", 789.95), ("SALARIOS", 1e6), ("INDENIZACOES E AVISO PREVIO", 84_236.69)])
    cats = {s["categoria"] for s in sug}
    assert cats == {"Jurídico pontual", "Multas e penalidades", "Rescisões e indenizações"}
    assert all(s["sinal"] == 1 and s["valor"] > 0 for s in sug)
    eq = nr.sugerir([], dre_equivalencia=-2_695_244.78)[0]
    assert eq["categoria"] == "Equivalência patrimonial" and eq["sinal"] == 1 and eq["valor"] == 2_695_244.78
    assert nr.sugerir([], dre_equivalencia=1_000.0)[0]["sinal"] == -1      # ganho: subtrai


def test_carregar_para_relatorio_confirmado_pendente_e_indisponivel():
    itens = {"ENERGIA": [dict(id=1, categoria="Jurídico pontual", valor=100.0, sinal=1), dict(id=2, categoria="Rescisões e indenizações", valor=30.0, sinal=-1)],
             "SMG": [dict(id=3, categoria="Jurídico pontual", valor=50.0, sinal=1)]}
    st = {"ENERGIA": "confirmado", "SMG": "confirmado"}
    with patch.object(nr, "tabelas_existem", return_value=True), \
         patch.object(nr, "status_confirmacao", side_effect=lambda c, e, p, g: {"status": st[e]}), \
         patch.object(nr, "listar_itens", side_effect=lambda c, e, p, g: itens[e]):
        r = nr.carregar_para_relatorio(object(), ["ENERGIA", "SMG"], None, "")
        assert r["status"] == "confirmado" and r["total"] == 120.0           # 100 - 30 + 50
        assert r["itens"][0] == ("Rescisões e indenizações", -30.0)          # ordem de CATEGORIAS
        st["SMG"] = "alterado"
        r = nr.carregar_para_relatorio(object(), ["ENERGIA", "SMG"], None, "")
        assert r["status"] == "pendente" and r["itens"] == [] and r["total"] == 0.0 and r["pendentes"] == ["SMG"]
    with patch.object(nr, "tabelas_existem", return_value=False):
        assert nr.carregar_para_relatorio(object(), ["ENERGIA"], None, "")["status"] == "indisponivel"
    with patch.object(nr, "tabelas_existem", side_effect=RuntimeError("banco fora")):
        assert nr.carregar_para_relatorio(object(), ["ENERGIA"], None, "")["status"] == "indisponivel"   # nunca derruba o relatorio


def test_somar_nao_recorrentes_exige_todas_as_empresas():
    ok = {"status": "confirmado", "itens": [("Jurídico pontual", 10.0)], "total": 10.0}
    assert drc._somar_nao_recorrentes([ok, ok])["total"] == 20.0
    assert drc._somar_nao_recorrentes([ok, NR_PEND])["status"] == "pendente"
    assert drc._somar_nao_recorrentes([ok, {"status": "indisponivel", "itens": [], "total": 0.0}])["status"] == "indisponivel"
