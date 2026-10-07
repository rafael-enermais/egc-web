# -*- coding: utf-8 -*-
"""v0.48.0 -- nao recorrentes + EBITDA Ajustado contra Postgres de VERDADE (schema.sql inteiro, bloco 25
incluso): gravacao com historico, confirmacao que cai quando a lista muda, ponte do EBITDA que fecha com o
indicador do app, consolidado que exige todas as empresas confirmadas, e PDFs completos nos 3 modelos."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
import nao_recorrentes as nr  # noqa: E402

P26, G26 = pg.P_2026, "semestral"
J, R_ = "Jurídico pontual", "Rescisões e indenizações"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    pg.semear_cenario(c)
    yield c
    c.close()


def _gran_energia(conn):
    import db
    return sorted({d["granularidade"] for d in db.listar_periodos_detalhado(conn, "ENERGIA") if d["periodo"] == P26})


def test_bloco_25_cria_as_3_tabelas(conn):
    assert nr.tabelas_existem(conn)


def test_item_historico_confirmacao_e_queda_da_confirmacao(conn):
    gran = "semestral"
    assert nr.status_confirmacao(conn, "ENERGIA", P26, gran)["status"] == "nao_confirmado"
    i1 = nr.adicionar_item(conn, "ENERGIA", P26, gran, J, 26_000.0, 1, descricao="honorarios", usuario="a@x")
    i2 = nr.adicionar_item(conn, "ENERGIA", P26, gran, R_, 84_236.69, 1, usuario="a@x", sugerido_regra=True)
    r = nr.confirmar_lista(conn, "ENERGIA", P26, gran, usuario="b@x")
    assert r == {"itens": 2, "total": 110_236.69}
    st = nr.status_confirmacao(conn, "ENERGIA", P26, gran)
    assert st["status"] == "confirmado" and st["confirmado_por"] == "b@x" and st["total"] == 110_236.69
    # alterar derruba a confirmacao
    nr.editar_item(conn, i1, J, 27_000.0, 1, descricao="honorarios", usuario="a@x")
    assert nr.status_confirmacao(conn, "ENERGIA", P26, gran)["status"] == "alterado"
    assert nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, gran)["status"] == "pendente"
    nr.confirmar_lista(conn, "ENERGIA", P26, gran, usuario="b@x")
    assert nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, gran)["total"] == 111_236.69
    # remover NAO apaga, e restaurar volta
    nr.remover_item(conn, i2, usuario="a@x")
    assert [x["id"] for x in nr.listar_itens(conn, "ENERGIA", P26, gran)] == [i1]
    assert [x["id"] for x in nr.listar_itens(conn, "ENERGIA", P26, gran, incluir_removidos=True)] == [i1, i2]
    assert nr.status_confirmacao(conn, "ENERGIA", P26, gran)["status"] == "alterado"
    nr.restaurar_item(conn, i2, usuario="a@x")
    assert nr.status_confirmacao(conn, "ENERGIA", P26, gran)["status"] == "confirmado"      # mesma lista de antes
    acoes = [h["acao"] for h in nr.listar_historico(conn, "ENERGIA", P26, gran)]
    assert acoes.count("criou") == 2 and acoes.count("editou") == 1 and acoes.count("removeu") == 1
    assert acoes.count("restaurou") == 1 and acoes.count("confirmou") == 2
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM egc.nao_recorrente")
        assert cur.fetchone()[0] == 2                  # nada foi apagado


def test_lista_vazia_confirmada_vale_e_e_diferente_de_nao_revisado(conn):
    assert nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, "semestral")["status"] == "pendente"
    nr.confirmar_lista(conn, "ENERGIA", P26, "semestral", usuario="b@x")
    r = nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, "semestral")
    assert r["status"] == "confirmado" and r["itens"] == [] and r["total"] == 0.0


def test_granularidades_nao_se_misturam(conn):
    nr.adicionar_item(conn, "ENERGIA", P26, "semestral", J, 10.0, 1, usuario="a@x")
    nr.confirmar_lista(conn, "ENERGIA", P26, "semestral", usuario="a@x")
    assert nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, "semestral")["status"] == "confirmado"
    assert nr.carregar_para_relatorio(conn, ["ENERGIA"], P26, "trimestral")["status"] == "pendente"
    assert nr.listar_itens(conn, "ENERGIA", P26, "trimestral") == []


def test_ponte_do_ebitda_fecha_com_o_indicador_do_app(conn):
    for gran in _gran_energia(conn):
        d, _ = drc.montar_dados_relatorio(conn, "ENERGIA", P26, periodo_label="t", granularidade=gran)
        pt = d["ponte_ebitda"]
        assert pt["ebitda"] == d["ebitda"]
        assert round(pt["lucro_operacional"] + pt["resultado_financeiro"] + pt["deprec_amortiz"] - pt["ebitda"], 2) == 0
        assert round(pt["resultado_liquido"] + pt["csll_irpj"] - pt["outros_resultados"] - pt["lucro_operacional"], 2) == 0
        assert d["nao_recorrentes"]["status"] == "pendente" and d["incluir_ebitda_ajustado"] is True


def test_consolidado_so_ajusta_com_todas_as_empresas_confirmadas(conn):
    gran = "semestral"
    nr.adicionar_item(conn, "ENERGIA", P26, gran, J, 100.0, 1, usuario="a@x")
    nr.confirmar_lista(conn, "ENERGIA", P26, gran, usuario="a@x")
    d, _ = drc.montar_dados_relatorio(conn, ["ENERGIA", "CONST"], P26, periodo_label="t", granularidade="trimestral" if "trimestral" in _gran_energia(conn) else gran)
    assert d["nao_recorrentes"]["status"] == "pendente"          # CONST nao confirmou -> nada de ajuste parcial
    assert d["nao_recorrentes"]["total"] == 0.0


def _confirmar_todas(conn, itens_por_empresa, gran_por_emp):
    for emp, itens in itens_por_empresa.items():
        for cat, v in itens:
            nr.adicionar_item(conn, emp, P26, gran_por_emp[emp], cat, v, 1, usuario="a@x")
        nr.confirmar_lista(conn, emp, P26, gran_por_emp[emp], usuario="a@x")


def test_pdfs_completos_nos_tres_modelos(conn):
    gran = "semestral"
    nr.adicionar_item(conn, "ENERGIA", P26, gran, J, 26_000.0, 1, usuario="a@x")
    nr.confirmar_lista(conn, "ENERGIA", P26, gran, usuario="a@x")
    import pdfplumber
    with tempfile.TemporaryDirectory() as tmp:
        for variante in ("fornecedor", "gerencial"):
            d, inc = drc.montar_dados_relatorio(conn, "ENERGIA", P26, periodo_label="1S/2026", granularidade=gran, variante=variante)
            assert d["nao_recorrentes"]["status"] == "confirmado"
            cam = os.path.join(tmp, f"{variante}.pdf")
            g.gerar_pdf_completo(d, cam, incluir_pagina_resultado=inc)
            with pdfplumber.open(cam) as pdf:
                textos = [p.extract_text() or "" for p in pdf.pages]
            assert len(textos) == g.numero_de_paginas(d, inc)
            pg_aj = next(t for t in textos if "Leitura do EBITDA Ajustado" in t)
            assert "Jurídico pontual" in pg_aj and "R$ 26.000" in pg_aj
            i_aj = textos.index(pg_aj)
            assert "EBITDA do Período" in textos[i_aj - 1] or "Do resultado líquido ao EBITDA" in textos[i_aj - 1]
        # sem confirmar: so' ate o contabil
        d, inc = drc.montar_dados_relatorio(conn, "CONST", P26, periodo_label="1S/2026", granularidade="trimestral")
        cam = os.path.join(tmp, "const.pdf")
        g.gerar_pdf_completo(d, cam, incluir_pagina_resultado=inc)
        with pdfplumber.open(cam) as pdf:
            assert any("Leitura da Reconciliação" in (p.extract_text() or "") for p in pdf.pages)


def test_evolucao_com_duas_colunas_traz_a_pagina_nova(conn):
    pg.semear_trimestre_anterior(conn)
    from datetime import date
    P1 = date(2026, 3, 31)
    nr.adicionar_item(conn, "CONST", P1, "trimestral", J, 500.0, 1, usuario="a@x")
    nr.confirmar_lista(conn, "CONST", P1, "trimestral", usuario="a@x")
    nr.confirmar_lista(conn, "CONST", P26, "trimestral", usuario="a@x")        # lista vazia confirmada
    d = drc.montar_dados_relatorio_comparativo(conn, ["CONST"], [P1, P26], ["1T/2026", "2T/2026"], "1T A 2T/2026",
                                               granularidades=["trimestral", "trimestral"])
    ea = d["ebitda_ajustado"]
    assert [x["status"] for x in ea["nr"]] == ["confirmado", "confirmado"] and ea["nr"][0]["total"] == 500.0
    d1, _ = drc.montar_dados_relatorio(conn, "CONST", P1, periodo_label="x", granularidade="trimestral")
    assert ea["ponte"][0]["ebitda"] == d1["ebitda"]                              # mesma ponte do relatorio de 1 periodo
    with tempfile.TemporaryDirectory() as tmp:
        cam = os.path.join(tmp, "evol.pdf")
        gc.gerar_pdf_comparativo(d, cam)
        import pdfplumber
        with pdfplumber.open(cam) as pdf:
            assert len(pdf.pages) == gc.numero_de_paginas_comparativo(d)
            assert any("(=) EBITDA ajustado" in (p.extract_text() or "") for p in pdf.pages)


def test_evolucao_de_grupo_exige_confirmacao_de_todas(conn):
    pg.semear_trimestre_anterior(conn)
    from datetime import date
    P1 = date(2026, 3, 31)
    nr.confirmar_lista(conn, "CONST", P1, "trimestral", usuario="a@x")
    d = drc.montar_dados_relatorio_comparativo(conn, ["CONST", "ENG"], [P1, P26], ["1T", "2T"], "x", granularidades=["trimestral", "trimestral"])
    assert [x["status"] for x in d["ebitda_ajustado"]["nr"]] == ["pendente", "pendente"]      # ENG nunca confirmou
