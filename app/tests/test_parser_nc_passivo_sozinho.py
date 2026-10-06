# -*- coding: utf-8 -*-
"""
v0.47.3 -- regressao do "Passivo Nao Circulante zerado no relatorio" (Energia 12/2025).

Quando o PASSIVO tem mais linhas que o ATIVO, o pdfplumber cola o resto do
passivo sozinho na coluna 0. A linha "Nao Circulante <total>" do PASSIVO caia
no ATIVO (nao e' nome de conta), o total nunca era gravado e o relatorio
mostrava R$ 0,00. Alem disso o subbloco do passivo nao virava NAO CIRCULANTE,
perdendo as contas que se repetem nos dois blocos.

Layout abaixo reproduz o do PDF real (validado contra Energia 12/2025:
Nao Circulante passivo = 12.717.981,09).
Rodar: pytest app/tests/test_parser_nc_passivo_sozinho.py
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import parser_egc as P


class _Page:
    def __init__(self, rows):
        self._rows = rows

    def extract_tables(self):
        return [self._rows]

    def extract_text(self, **_):
        return ""


class _Pdf:
    def __init__(self, rows):
        self.pages = [_Page(rows)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _linha(c0):
    return [c0, "", "", "", "", "", "", ""]


def _rodar(monkeypatch, rows):
    fake = types.SimpleNamespace(open=lambda _p: _Pdf(rows))
    monkeypatch.setattr(P, "pdfplumber", fake)
    return P.parse_duplo(Path("x.pdf"), "BP", "teste")


def _mapa(res):
    return {(r[0], r[1]): r[2] for r in res}


def test_nao_circulante_sozinho_do_passivo_grava_total_e_contas_nc(monkeypatch):
    rows = [
        _linha("Circulante 16.000,00 Circulante 17.000,00"),
        _linha("Disponível 4.000,00 Instituições Financeiras 3.000,00"),
        _linha("Não Circulante 22.000,00 Contas a Pagar 1.800,00"),
        _linha("Não Circulante 12.000,00"),          # passivo sobrando (sem ativo)
        _linha("Instituições Financeiras 4.000,00"),
    ]
    m = _mapa(_rodar(monkeypatch, rows))
    assert m[("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO")] == "R$ 12.000,00"
    assert m[("PASSIVO NAO CIRCULANTE", "INSTITUICOES FINANCEIRAS")] == "R$ 4.000,00"
    # o Circulante nao pode ser sobrescrito
    assert m[("PASSIVO CIRCULANTE", "INSTITUICOES FINANCEIRAS")] == "R$ 3.000,00"
    assert m[("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO")] == "R$ 17.000,00"
    # o Nao Circulante do ATIVO continua no ATIVO
    assert m[("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO")] == "R$ 22.000,00"


def test_nao_circulante_unico_continua_sendo_do_ativo(monkeypatch):
    # sem "Nao Circulante" anterior no ATIVO a linha sozinha segue o comportamento antigo
    rows = [
        _linha("Circulante 16.000,00 Circulante 17.000,00"),
        _linha("Não Circulante 22.000,00"),
    ]
    m = _mapa(_rodar(monkeypatch, rows))
    assert m[("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO")] == "R$ 22.000,00"
    assert ("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO") not in m
