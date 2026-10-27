# -*- coding: utf-8 -*-
"""v0.53.0 -- relatorio PDF de creditos e debitos de PIS/COFINS: dados (puros) e PDF (reportlab) de verdade, lido de volta com pypdf."""
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import creditos_db as cdb  # noqa: E402
import creditos_graficos as gr  # noqa: E402
import dados_relatorio_creditos as dr  # noqa: E402
import gerador_relatorio_creditos as gen  # noqa: E402

pypdf = pytest.importorskip("pypdf")


def _fonte(chave, k, tipo="HISTORICO"):
    pis, cof = k * 0.18, k * 0.82
    r = {"credito_pis": pis, "credito_cofins": cof, "debito_nc_pis": pis * 3, "debito_nc_cofins": cof * 3, "debito_cum_pis": 100.0, "debito_cum_cofins": 400.0,
         "credito_usado_pis": pis * 0.9, "credito_usado_cofins": cof * 0.9, "credito_anterior_pis": 0.0, "credito_anterior_cofins": 0.0, "saldo_pis": pis * 0.1,
         "saldo_cofins": cof * 0.1, "retencao_nc_pis": 10.0, "retencao_nc_cofins": 40.0, "a_recolher_pis": pis * 2, "a_recolher_cofins": cof * 2,
         "receita_nc": k * 40, "receita_cum": 5000.0}
    return {"chave": chave, "tipo": tipo, "sped_tipo": "Retificadora", "rotulo": f"{chave} · arquivo.txt", "resumo": r,
            "por_categoria": {"Aluguel": {"pis": pis * 0.4, "cofins": cof * 0.4}, "Combustível": {"pis": pis * 0.6, "cofins": cof * 0.6}}, "por_natureza": {}, "dif_fornecedores": []}


def _serie(n=4, simular_ultimo=False, com_original=True):
    linhas = []
    for m in range(1, n + 1):
        modo = "simulada" if simular_ultimo and m == n else "padrao"
        orig = _fonte("HIST_ORIGINAL", 900.0 * m * 0.95) if com_original and m <= 2 else None
        linhas.append(cdb._linha_serie(f"{m:02d}/2026", _fonte("HIST_APONTADO", 900.0 * m), orig, modo, 2, {"conferido": m < n}))
    return gr.preparar_serie(pd.DataFrame(linhas), "total")


def _dados(serie=None, comp="04/2026", **kw):
    return dr.montar_dados(serie if serie is not None else _serie(), comp, empresa_codigo="ENERGIA", empresa_nome="Enermais Energia", cnpj="47.040.664/0001-48",
                           usuario="Rafael", **kw)


def _texto(pdf: bytes) -> list:
    return [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]


def test_dados_resumo_leitura_e_variacao():
    d = _dados()
    assert d["rotulo"] == "abr/26" and d["rotulo_anterior"] == "mar/26" and d["oficial"] and d["meses_nao_oficiais"] == []
    ap = {x["item"]: x for x in d["apuracao"]}
    assert round(ap["Crédito apurado no mês"]["total"], 2) == 3600.0 and round(ap["Crédito apurado no mês"]["anterior"], 2) == 2700.0
    assert round(ap["Crédito apurado no mês"]["pct"], 4) == 0.3333
    assert round(ap["Crédito apurado no mês"]["pis"], 2) == 648.0
    assert d["leitura"][0].startswith("Em abr/26, o crédito de PIS/COFINS apurado foi de R$ 3.600,00") and "+33,3% em relação a mar/26" in d["leitura"][0]
    assert not any(w in " ".join(d["leitura"]).lower() for w in ("ótimo", "excelente", "preocupante", "bom ", "ruim"))      # frase-modelo neutra
    assert [m["rotulo"] for m in d["meses"]] == ["jan/26", "fev/26", "mar/26", "abr/26"]
    assert d["composicao"][0]["label"] == "Combustível" and round(sum(x["pct"] for x in d["composicao"]), 6) == 100.0


def test_primeiro_mes_nao_tem_comparacao_e_nao_quebra():
    d = _dados(comp="01/2026")
    assert d["rotulo_anterior"] is None and "Não há mês anterior" in d["leitura"][0] and d["variacao_categorias"] == []
    assert d["tem_original"] and d["dif_original"] is not None and "SPED original" in d["leitura"][-1]
    assert gen.gerar_pdf_creditos(d)[:4] == b"%PDF"


def test_janela_limita_os_meses_do_historico():
    d = _dados(_serie(8), "08/2026", janela=3)
    assert [m["rotulo"] for m in d["meses"]] == ["jun/26", "jul/26", "ago/26"]


def test_mes_inexistente_levanta_erro_claro():
    with pytest.raises(ValueError, match="09/2026"):
        _dados(comp="09/2026")
    with pytest.raises(ValueError):
        dr.montar_dados(pd.DataFrame(), "01/2026", empresa_codigo="ENERGIA", empresa_nome="X")


def test_pdf_tem_5_paginas_com_capa_e_o_conteudo_certo():
    pdf = gen.gerar_pdf_creditos(_dados(controle_mes={"conferido": True, "conferido_por": "Ana", "nota": "Conferido com o Detalhamento."}, nome_contador="Ana Souza"))
    paginas = _texto(pdf)
    assert len(paginas) == 5 and pdf[:4] == b"%PDF"
    assert "Créditos e Débitos" in paginas[0] and "PIS/COFINS" in paginas[0] and "PERÍODO · 04/2026" in paginas[0] and "CNPJ 47.040.664/0001-48" in paginas[0]
    assert "Créditos e Débitos de PIS/COFINS · abr/26" in paginas[1] and "R$ 3.600,00" in paginas[1] and "Página 2 de 5" in paginas[1]
    assert "Nota da contadora: Conferido com o Detalhamento." in paginas[1].replace("\n", " ") and "conferida por Ana" in paginas[1]
    assert "Crédito por categoria em abr/26" in paginas[2] and "Combustível" in paginas[2] and "R$ 2.160,00" in paginas[2]
    assert "Apuração do mês" in paginas[3] and "Total a recolher" in paginas[3] and "Página 4 de 5" in paginas[3]
    assert "Histórico mensal" in paginas[4] and "Ana Souza" in paginas[4] and "jan/26" in paginas[4]
    assert all("SIMULAÇÃO" not in p for p in paginas) and all("Versão oficial" in p for p in paginas[1:])


def test_simulacao_avisa_em_todas_as_paginas_e_muda_o_nome_do_arquivo():
    d = _dados(_serie(simular_ultimo=True))
    assert not d["oficial"] and d["meses_nao_oficiais"] == ["abr/26"] and d["modo"] == "simulada"
    assert dr.nome_arquivo(d) == "creditos_pis_cofins_ENERGIA_04-2026_SIMULACAO.pdf"
    assert dr.nome_arquivo(_dados()) == "creditos_pis_cofins_ENERGIA_04-2026.pdf"
    paginas = _texto(gen.gerar_pdf_creditos(d))
    assert "SIMULAÇÃO" in paginas[0]                                                            # selo na capa
    assert all("NÃO É A VERSÃO OFICIAL" in p for p in paginas[1:]) and all("Simulação (não oficial)" in p for p in paginas[1:])
    assert "SIMULAÇÃO" in paginas[4]                                                           # tabela do historico marca o mes simulado


def test_sugestoes_so_entram_no_pdf_se_pedidas():
    s = _serie()
    s.loc[2, "saldo_total"] = 500.0
    s.loc[3, "anterior_pis"] = 0.0
    sem, com = _dados(s), _dados(s, incluir_sugestoes=True)
    assert sem["sugestoes"] == [] and any(x["titulo"].startswith("Saldo de crédito") for x in com["sugestoes"])
    assert not any(x["titulo"] in dr._SUGESTOES_FORA for x in com["sugestoes"])                 # lembretes da tela nao vao para o PDF
    assert "Pontos para acompanhar" not in _texto(gen.gerar_pdf_creditos(sem))[4]
    assert "Pontos para acompanhar" in _texto(gen.gerar_pdf_creditos(com))[4]


def test_valores_grandes_e_negativos_nao_quebram_o_pdf():
    s = _serie()
    s.loc[3, "cred_total"] = 987654321.55
    s.loc[3, "saldo_total"] = -1234567.89
    s.loc[3, "dif_total"] = -50.0
    pdf = gen.gerar_pdf_creditos(_dados(s))
    p = _texto(pdf)
    assert len(p) == 5 and "-R$" in p[4]


def _texto_pdf(d):
    r = pypdf.PdfReader(io.BytesIO(gen.gerar_pdf_creditos(d)))
    return "\n".join(p.extract_text() for p in r.pages)


def test_pdf_nao_traz_aviso_de_detalhamento_em_nenhum_estado():
    """Rafael 27/10/2026: aviso de Detalhamento fica SO no app; o PDF segue o padrao, sem avisos."""
    for det in ({"estado": "ausente"},
                {"estado": "fecha", "n_linhas": 627, "n_pendentes": 0, "arquivo": "x.xlsx"},
                {"estado": "pendencias", "n_linhas": 627, "n_pendentes": 195, "tipo_sped": "Retificadora"}):
        txt = " ".join(_texto_pdf(_dados(detalhamento=det)).split())
        assert "Detalhamento da contadora" not in txt and "sem Detalhamento" not in txt
    assert "Detalhamento da contadora" not in _texto_pdf(_dados())


def test_opcoes_ui_tira_duplicata_e_encurta_rotulo():
    from datetime import datetime
    arq_o = "PISCOFINS_20260701_20260731_47040664000148_Original_20260825102110_2D02.txt"
    arq_r = "PISCOFINS_20260701_20260731_47040664000148_Retificadora_20260929152505_94F0.txt"
    mk = lambda chave, tipo, arq, ap: {"chave": chave, "tipo": "HISTORICO" if chave.startswith("HIST") else "ENVIO", "sped_tipo": tipo, "arquivo": arq,
                                        "enviado_em": datetime(2026, 10, 16, 18, 0), "apontada": ap}
    fs = [mk("HIST_ORIGINAL", "Original", arq_o, False), mk("HIST_APONTADO", "Retificadora", arq_r, True), mk("ENV:a", "Original", arq_o, False), mk("ENV:b", "Retificadora", arq_r, True)]
    ops = cdb.opcoes_ui(fs)
    assert [r for _f, r in ops] == ["Original · 25/08/2026", "Retificadora · 29/09/2026 · apontada"]
    assert [f["chave"] for f, _r in ops] == ["HIST_ORIGINAL", "HIST_APONTADO"]
    ops2 = cdb.opcoes_ui(fs, manter={"ENV:b"})                                                    # a oficial marcada nao pode sumir da lista
    assert "ENV:b" in [f["chave"] for f, _r in ops2] and len(ops2) == 2


def test_v0540_mes_conferido_sem_detalhamento_pdf_segue_padrao():
    """Rafael 27/10/2026: mes sem Detalhamento PODE ser conferido; o aviso e' so' no app, o PDF fica padrao."""
    ctrl = {"conferido": True, "conferido_por": "Ana"}
    for det in ({"estado": "ausente"}, {"estado": "fecha", "n_linhas": 3}):
        com = _texto(gen.gerar_pdf_creditos(_dados(controle_mes=ctrl, detalhamento=det)))[1].replace("\n", " ")
        assert "conferida por Ana." in com and "sem Detalhamento" not in com and "Detalhamento da contadora" not in com
    nao = _texto(gen.gerar_pdf_creditos(_dados(detalhamento={"estado": "ausente"})))[1].replace("\n", " ")
    assert "ainda não foi marcado como conferido" in nao and "Detalhamento da contadora" not in nao
