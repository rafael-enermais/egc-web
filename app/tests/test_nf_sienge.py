# -*- coding: utf-8 -*-
"""
Testes de app/nf_sienge.py.

_classificar_nota e' funcao pura (recebe um DataFrame de bills+credores ja
carregado, sem tocar banco/rede) -- testada isolada, cobrindo os 4
desfechos possiveis do matching fechado com o Rafael em 25/09/2026 (EGC
00-handoff.md secao 62): LANCADA (por chave OU por CNPJ+numero+valor),
VALOR_DIVERGENTE, NUMERO_DIVERGENTE, NAO_ENCONTRADA, e o fallback de tipo
de documento (nota lancada sob codigo diferente de NFE/NF).

gravar_manifesto/conciliar_import sao testados com mock de conexao/cursor
(mesmo padrao DB-API de app/tests/test_db_logic.py) -- garante que o SQL
roda sem excecao e que o resumo bate, sem precisar de banco real.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nf_sienge  # noqa: E402


def _bills(linhas):
    cols = ["bill_id", "creditor_id", "document_identification_id", "document_number",
            "total_invoice_amount", "access_key_number", "creditor_cnpj",
            "cnpj_normalizado", "numero_normalizado"]
    return pd.DataFrame(linhas, columns=cols)


def test_classificar_lancada_por_chave_de_acesso():
    bills = _bills([
        [100, 591, "NFE ", "10448", 801.08, "41260607393522000140550010000104481101174213",
         "07.393.522/0001-40", "07393522000140", "10448"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "10448",
           "_valor_float": 801.08, "Chave": "41260607393522000140550010000104481101174213", "Num": "10448"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "LANCADA"
    assert r["confianca"] == "CHAVE"
    assert r["sienge_bill_id"] == 100


def test_classificar_lancada_por_numero_cnpj_valor_sem_chave():
    bills = _bills([
        [200, 723, "NF  ", "20260513", 200000.0, None, "12.345.678/0001-90",
         "12345678000190", "20260513"],
    ])
    row = {"_cnpj_normalizado": "12345678000190", "_numero_normalizado": "20260513",
           "_valor_float": 200000.0, "Chave": None, "Num": "20260513"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "LANCADA"
    assert r["confianca"] == "NUMERO_CNPJ_VALOR"
    assert r["sienge_bill_id"] == 200


def test_classificar_valor_divergente():
    bills = _bills([
        [300, 591, "NFE ", "555", 100.00, None, "07.393.522/0001-40", "07393522000140", "555"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "555",
           "_valor_float": 999.99, "Chave": None, "Num": "555"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "VALOR_DIVERGENTE"
    assert r["sienge_bill_id"] == 300


def test_classificar_numero_divergente_mesmo_cnpj_e_valor():
    bills = _bills([
        [400, 591, "NFE ", "777", 500.00, None, "07.393.522/0001-40", "07393522000140", "777"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "999",
           "_valor_float": 500.00, "Chave": None, "Num": "999"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "NUMERO_DIVERGENTE"
    assert r["sienge_bill_id"] == 400


def test_classificar_nao_encontrada():
    bills = _bills([
        [500, 591, "NFE ", "1", 10.0, None, "00.000.000/0001-00", "00000000000100", "1"],
    ])
    row = {"_cnpj_normalizado": "99999999000199", "_numero_normalizado": "42",
           "_valor_float": 42.00, "Chave": None, "Num": "42"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "NAO_ENCONTRADA"
    assert r["sienge_bill_id"] is None


def test_classificar_achada_fora_do_universo_nfe_nf_fallback():
    """Nota lancada sob tipo de documento diferente de NFE/NF (ex.: RDV) --
    2o passe restrito nao acha, mas o fallback (3o passe, sem filtro de
    tipo) acha e sinaliza a divergencia de tipo na observacao."""
    bills = _bills([
        [600, 591, "RDV ", "321", 150.00, None, "07.393.522/0001-40", "07393522000140", "321"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "321",
           "_valor_float": 150.00, "Chave": None, "Num": "321"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "LANCADA"
    assert r["confianca"] == "NUMERO_CNPJ_VALOR_TIPO_DIVERGENTE"
    assert "RDV" in r["observacao"]


def test_classificar_bills_vazio_nao_quebra():
    bills = _bills([])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "1",
           "_valor_float": 1.0, "Chave": None, "Num": "1"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "NAO_ENCONTRADA"


# ───────────────────── mock de conexao (DB-API) ─────────────────────

class FakeCursor:
    def __init__(self, fetchall_result=None, description=None):
        self.executed = []
        self._fetchall_result = fetchall_result or []
        self.description = description or []

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchall_result[0] if self._fetchall_result else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_gravar_manifesto_insere_1_linha_por_nota():
    cur = FakeCursor()
    conn = FakeConn(cur)
    df = pd.DataFrame([
        {"Num": "10448", "_numero_normalizado": "10448", "Tipo": "NFe", "DtEmi": "2026-06-30",
         "_valor_float": 801.08, "CFOP": "5102", "Emissor Nome": "COSTA BENTO", "Emissor CNPJ/CPF": "07393522000140",
         "_cnpj_normalizado": "07393522000140", "UF": "PR", "Chave": "41260607393522000140550010000104481101174213",
         "_chave_modelo": "55", "_chave_serie": "001", "_chave_numero": "10448"},
    ])
    import_id = nf_sienge.gravar_manifesto(conn, "ENERGIA", "08/2026", df, "manifesto.xlsx", "rafael")
    assert len(cur.executed) == 1
    assert "INSERT INTO egc.nf_manifesto_import" in cur.executed[0][0]
    assert import_id  # uuid gerado


def test_listar_pendencias_abertas_filtra_status_correto():
    cur = FakeCursor(fetchall_result=[], description=[("empresa_codigo",)])
    conn = FakeConn(cur)
    nf_sienge.listar_pendencias_abertas(conn, "ENERGIA", 10)
    sql, params = cur.executed[0]
    assert "status <> 'LANCADA'" in sql
    assert "PENDENTE" in sql and "ENVIADO_SUPRIMENTOS" in sql
    assert params == ("ENERGIA", "ENERGIA", 10)


def test_ultima_sincronizacao_pega_o_mais_antigo_dos_dois():
    import datetime
    ts = datetime.datetime(2026, 9, 25, 4, 0, 0)
    cur = FakeCursor(fetchall_result=[(ts,)])
    conn = FakeConn(cur)
    r = nf_sienge.ultima_sincronizacao(conn)
    assert r == ts
    assert "LEAST" in cur.executed[0][0]


def test_ultima_sincronizacao_none_quando_nunca_sincronizou():
    cur = FakeCursor(fetchall_result=[(None,)])
    conn = FakeConn(cur)
    assert nf_sienge.ultima_sincronizacao(conn) is None
