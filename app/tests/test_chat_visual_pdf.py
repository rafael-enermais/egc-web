# -*- coding: utf-8 -*-
"""v0.44.1 -- PDF do Erik.AI no padrao EnerMais + tabela de exibicao limpa."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber  # noqa: E402

import chat_visual as V  # noqa: E402


def _prep_evolucao(n=1):
    r = {"evolucao": [{"periodo": f"2025-0{i}", "EBITDA": 1000.0 * (3 - i), "Margem EBITDA": 0.05 * i} for i in range(1, n + 1)],
         "base_do_periodo": "Trimestral", "empresas_incluidas": ["ENERGIA", "SMG"]}
    return V.preparar("consultar_evolucao_indicadores", r)


def test_pdf_tem_marca_enermais_e_paginacao():
    prep = _prep_evolucao(3)
    pdf = V.pdf_bytes(prep, "yudi@enermais.com.br", "consultar_evolucao_indicadores", prep["graficos"]["Valores em R$"], "Barras", None)
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert len(doc.pages) == 1
        pg = doc.pages[0]
        txt = pg.extract_text()
        assert "ERIK.AI" in txt and "Página 1 de 1" in txt and "yudi@enermais.com.br" in txt
        assert "Evolução dos indicadores" in txt
        assert pg.images, "logo do Grupo na pagina"
        fontes = {c["fontname"] for c in pg.chars}
        assert any("Poppins" in f for f in fontes), f"fonte da marca ausente: {fontes}"


def test_pdf_tabela_longa_pagina_e_repete_cabecalho():
    r = {"pendencias": [{"registro_id": i, "numero_nota": str(i), "fornecedor_nome": f"Forn {i}", "valor": 100.0 + i,
                         "status": "NAO_ENCONTRADA", "pendencia_status": None} for i in range(120)]}
    prep = V.preparar("consultar_notas_pendentes", r)
    pdf = V.pdf_bytes(prep, "u", "consultar_notas_pendentes", None)
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert len(doc.pages) >= 3
        for pg in doc.pages:
            t = pg.extract_text()
            assert "Fornecedor" in t and f"de {len(doc.pages)}" in t


def test_tabela_exibicao_sem_none_e_com_nomes_legiveis():
    r = {"pendencias": [{"registro_id": 1, "numero_nota": "101525", "fornecedor_nome": "ACME", "valor": 1234.5,
                         "status": "NAO_ENCONTRADA", "pendencia_status": None}]}
    prep = V.preparar("consultar_notas_pendentes", r)
    df = V.tabela_exibicao(prep)
    assert "Fornecedor" in df.columns and "Nota" in df.columns and "fornecedor_nome" not in df.columns
    assert not df.astype(str).apply(lambda c: c.str.contains("None|nan", regex=True)).any().any()
    assert df.iloc[0]["Pendência"] == "—" and df.iloc[0]["Valor"].startswith("R$")
