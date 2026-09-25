# -*- coding: utf-8 -*-
"""Testes de app/nf_parser.py -- decodificacao da chave de acesso e
leitura do manifesto .xlsx. Chave de teste e' a MESMA usada na validacao
real contra a API do Sienge em 25/09/2026 (EGC 00-handoff.md secao 62):
bate com CNPJ 07393522000140, modelo 55, serie 001, numero 10448."""
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nf_parser  # noqa: E402

CHAVE_REAL = "41260607393522000140550010000104481101174213"


def test_decodificar_chave_acesso_bate_com_dado_real_do_sienge():
    d = nf_parser.decodificar_chave_acesso(CHAVE_REAL)
    assert d["cnpj_emissor"] == "07393522000140"
    assert d["modelo"] == "55"
    assert d["serie"] == "001"
    assert d["numero"] == "10448"


def test_decodificar_chave_acesso_invalida_devolve_none():
    assert nf_parser.decodificar_chave_acesso("123") is None
    assert nf_parser.decodificar_chave_acesso("") is None
    assert nf_parser.decodificar_chave_acesso(None) is None


def test_normalizar_cnpj_remove_formatacao():
    assert nf_parser.normalizar_cnpj("07.393.522/0001-40") == "07393522000140"
    assert nf_parser.normalizar_cnpj("07393522000140") == "07393522000140"
    assert nf_parser.normalizar_cnpj(None) == ""


def test_normalizar_numero_nota_remove_zeros_a_esquerda():
    assert nf_parser.normalizar_numero_nota("00010448") == "10448"
    assert nf_parser.normalizar_numero_nota(10448) == "10448"
    assert nf_parser.normalizar_numero_nota("0") == "0"
    assert nf_parser.normalizar_numero_nota("") == "0"


def _montar_xlsx_teste() -> io.BytesIO:
    df = pd.DataFrame([
        {"Num": 10448, "Tipo": "NFe", "DtEmi": "2026.06.30", "Valor": 801.08, "CFOP": "5102",
         "Emissor Nome": "COSTA BENTO E BENTO LTDA", "Emissor CNPJ/CPF": "07393522000140",
         "UF": "PR", "Chave": CHAVE_REAL},
        {"Num": 594535, "Tipo": "NFe", "DtEmi": "2026.07.31", "Valor": 1679.51, "CFOP": "6102",
         "Emissor Nome": "Havan S.A.", "Emissor CNPJ/CPF": "79379491007510",
         "UF": "RO", "Chave": "11260779379491007510550010005945351505156678"},
    ])
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_ler_manifesto_xlsx_decodifica_e_normaliza():
    df = nf_parser.ler_manifesto_xlsx(_montar_xlsx_teste())
    assert len(df) == 2
    linha = df.iloc[0]
    assert linha["_numero_normalizado"] == "10448"
    assert linha["_cnpj_normalizado"] == "07393522000140"
    assert linha["_valor_float"] == pytest.approx(801.08)
    assert linha["_chave_modelo"] == "55"
    assert linha["_chave_numero"] == "10448"


def test_ler_manifesto_sem_coluna_essencial_levanta_erro():
    df = pd.DataFrame([{"Tipo": "NFe", "Valor": 100.0}])  # falta Num e Emissor CNPJ/CPF
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    with pytest.raises(nf_parser.ManifestoInvalido):
        nf_parser.ler_manifesto_xlsx(buf)
