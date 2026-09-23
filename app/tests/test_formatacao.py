# -*- coding: utf-8 -*-
"""
Testes unitarios de app/formatacao.py -- wrappers BR (moeda/percentual/
numero) em cima de validacoes.formatar_br, criados 23/09/2026 depois do
Rafael achar numero em estilo americano ("R$ 582681.25") em varias telas
do relatorio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import formatacao  # noqa: E402


def test_moeda_br_formata_com_separador_de_milhar_e_virgula_decimal():
    assert formatacao.moeda_br(582681.25) == "R$ 582.681,25"
    assert formatacao.moeda_br(1234567.8) == "R$ 1.234.567,80"
    assert formatacao.moeda_br(0.5) == "R$ 0,50"
    print("OK: moeda_br — separador de milhar e decimal no padrão BR")


def test_moeda_br_negativo_e_none_e_forcar_sinal():
    assert formatacao.moeda_br(-948301.48) == "-R$ 948.301,48"
    assert formatacao.moeda_br(None) == "—"
    assert formatacao.moeda_br(float("nan")) == "—"
    assert formatacao.moeda_br(500.0, forcar_sinal=True) == "+R$ 500,00"
    assert formatacao.moeda_br(-500.0, forcar_sinal=True) == "-R$ 500,00"
    print("OK: moeda_br — negativo, None/NaN vira '—', forcar_sinal poe '+' explícito")


def test_pct_br_virgula_decimal_e_sinal():
    assert formatacao.pct_br(0.949) == "94,9%"
    assert formatacao.pct_br(-0.101) == "-10,1%"
    assert formatacao.pct_br(0.048, forcar_sinal=True) == "+4,8%"
    assert formatacao.pct_br(None) == "—"
    print("OK: pct_br — vírgula decimal, sinal e vazio")


def test_numero_br_com_sufixo_x():
    assert formatacao.numero_br(0.95, sufixo="x") == "0,95x"
    assert formatacao.numero_br(-0.01, sufixo="x") == "-0,01x"
    assert formatacao.numero_br(2.0, sufixo="x") == "2,00x"
    assert formatacao.numero_br(None, sufixo="x") == "—"
    print("OK: numero_br — sufixo 'x' (Liquidez Corrente), negativo e vazio")


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
