# -*- coding: utf-8 -*-
"""v0.44.0 -- Desfazer/Recuperar com VERSOES (lote) contra Postgres real.

Cenario do Rafael: ja ha 1 PDF de BP/DRE ativo; sobe outro do mesmo periodo e
CNPJ (o antigo vira INATIVO); desfaz o ultimo import -> o penultimo volta ATIVO.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import db  # noqa: E402

P = date(2026, 6, 30)


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    with c.cursor() as cur:
        cur.execute("DELETE FROM egc.lancamentos WHERE empresa_codigo='ENERGIA' AND periodo=%s", (P,))
    yield c
    c.close()


def _importar(c, arquivo, receita, tipo="DRE", gran="trimestral"):
    """Mesmo caminho do Importar PDF: inativa a versao anterior e grava a nova."""
    db.inativar_periodo_existente(c, "ENERGIA", P, tipo, granularidade=gran)
    if tipo == "DRE":
        rows = [["RECEITA BRUTA", f"{receita},00", "RECEITAS", "x"], ["IMPOSTOS", "10,00", "DEDUCOES", "x"]]
    else:
        rows = [["ATIVO CIRCULANTE", "CAIXA", f"{receita},00", "x"]]
    db.inserir_lancamentos(c, "ENERGIA", tipo, P, rows, arquivo, "u", None, gran)


def _estado(c, tipo="DRE"):
    with c.cursor() as cur:
        cur.execute(
            "SELECT arquivo_pdf, status, count(*) FROM egc.lancamentos "
            "WHERE empresa_codigo='ENERGIA' AND periodo=%s AND tipo=%s GROUP BY 1,2 ORDER BY 1,2", (P, tipo))
        return cur.fetchall()


def test_desfazer_reativa_o_penultimo_import(conn):
    _importar(conn, "A.pdf", 100)
    _importar(conn, "B.pdf", 200)
    assert _estado(conn) == [("A.pdf", "INATIVO", 2), ("B.pdf", "ATIVO", 2)]
    res = db.desfazer_importacao(conn, "ENERGIA", P)
    assert res["arquivados"] == 2 and res["reativados"] == 2 and res["reativados_arquivos"] == ["A.pdf"]
    assert _estado(conn) == [("A.pdf", "ATIVO", 2), ("B.pdf", "INATIVO", 2)]


def test_desfazer_sem_versao_anterior_so_arquiva(conn):
    _importar(conn, "A.pdf", 100)
    res = db.desfazer_importacao(conn, "ENERGIA", P)
    assert res == {"arquivados": 2, "reativados": 0, "reativados_arquivos": []}
    assert _estado(conn) == [("A.pdf", "INATIVO", 2)]


def test_tres_versoes_desfazer_em_cadeia(conn):
    for arq, v in (("A.pdf", 100), ("B.pdf", 200), ("C.pdf", 300)):
        _importar(conn, arq, v)
    db.desfazer_importacao(conn, "ENERGIA", P)          # tira C, volta B
    assert dict(((a, s), n) for a, s, n in _estado(conn))[("B.pdf", "ATIVO")] == 2
    db.desfazer_importacao(conn, "ENERGIA", P)          # tira B, volta A (nao oscila de volta pra C)
    assert [a for a, s, n in _estado(conn) if s == "ATIVO"] == ["A.pdf"]
    res = db.desfazer_importacao(conn, "ENERGIA", P)    # tira A: nao ha versao mais antiga
    assert res["reativados"] == 0 and not [1 for _a, s, _n in _estado(conn) if s == "ATIVO"]


def test_recuperar_com_duas_versoes_arquivadas_traz_so_a_mais_recente(conn):
    """Antes da v0.44.0 isto estourava UniqueViolation e travava o periodo."""
    _importar(conn, "A.pdf", 100)
    _importar(conn, "B.pdf", 200)
    db.arquivar_periodo(conn, "ENERGIA", P)
    assert all(s == "INATIVO" for _a, s, _n in _estado(conn))
    n = db.recuperar_periodo(conn, "ENERGIA", P)
    assert n == 2
    assert _estado(conn) == [("A.pdf", "INATIVO", 2), ("B.pdf", "ATIVO", 2)]


def test_recuperar_nao_mexe_em_documento_ja_ativo(conn):
    _importar(conn, "A.pdf", 100)
    _importar(conn, "B.pdf", 200)       # A inativo, B ativo
    assert db.recuperar_periodo(conn, "ENERGIA", P) == 0
    assert _estado(conn) == [("A.pdf", "INATIVO", 2), ("B.pdf", "ATIVO", 2)]


def test_bp_e_dre_sao_independentes_e_granularidades_nao_se_misturam(conn):
    _importar(conn, "A.pdf", 100, "DRE", "trimestral")
    _importar(conn, "S.pdf", 500, "DRE", "semestral")
    _importar(conn, "A.pdf", 7, "BP", "")
    _importar(conn, "B.pdf", 200, "DRE", "trimestral")      # substitui so' o trimestral
    db.desfazer_importacao(conn, "ENERGIA", P, granularidade="trimestral")
    with conn.cursor() as cur:
        cur.execute("SELECT tipo, COALESCE(granularidade,''), arquivo_pdf, status FROM egc.lancamentos "
                    "WHERE empresa_codigo='ENERGIA' AND periodo=%s GROUP BY 1,2,3,4 ORDER BY 1,2,3", (P,))
        linhas = cur.fetchall()
    assert ("DRE", "semestral", "S.pdf", "ATIVO") in linhas
    assert ("BP", "", "A.pdf", "ATIVO") in linhas
    assert ("DRE", "trimestral", "A.pdf", "ATIVO") in linhas
    assert ("DRE", "trimestral", "B.pdf", "INATIVO") in linhas


def test_correcao_manual_conta_nova_acompanha_a_versao(conn):
    _importar(conn, "A.pdf", 100)
    db.salvar_correcao_manual(conn, None, 55.0, P, "u", empresa_codigo="ENERGIA", tipo="DRE",
                              conta="OUTRAS RECEITAS", granularidade="trimestral")
    _importar(conn, "B.pdf", 200)       # A (com o ajuste) vai pra INATIVO
    db.desfazer_importacao(conn, "ENERGIA", P)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM egc.lancamentos WHERE empresa_codigo='ENERGIA' AND periodo=%s "
                    "AND conta='OUTRAS RECEITAS'", (P,))
        assert cur.fetchall() == [("ATIVO",)], "o ajuste manual volta junto com a versao A"


def test_arquivos_ativos_por_periodo(conn):
    _importar(conn, "A.pdf", 100)
    _importar(conn, "B.pdf", 200)
    assert db.arquivos_ativos_por_periodo(conn, "ENERGIA")[P] == {"B.pdf"}
