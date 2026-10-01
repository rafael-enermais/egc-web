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


def test_classificar_1_centavo_de_diferenca_bate_apesar_do_erro_de_ponto_flutuante():
    # Bug real achado 28/09/2026 conferindo pendencias_nf_ENERGIA_07_2026.xlsx
    # contra o manifesto ja conferido a mao pela contadora: nota da Gramaeira
    # Pereira (Sienge R$ 6.000,01, manifesto R$ 6.000,00) saia como
    # VALOR_DIVERGENTE mesmo a diferenca sendo exatamente 1 centavo (dentro
    # de TOLERANCIA_VALOR) -- abs(6000.01-6000.0) em float python da
    # 0.010000000000218, que e' MAIOR que 0.01. Devia bater como LANCADA.
    bills = _bills([
        [600, 591, "NFE ", "10486", 6000.01, None, "76.424.845/0001-76", "76424845000176", "10486"],
    ])
    row = {"_cnpj_normalizado": "76424845000176", "_numero_normalizado": "10486",
           "_valor_float": 6000.00, "Chave": None, "Num": "10486"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "LANCADA", (
        "diferenca de exatamente 1 centavo deve bater dentro de TOLERANCIA_VALOR, "
        "erro de ponto flutuante nao pode fazer a nota parecer divergente"
    )
    assert r["confianca"] == "NUMERO_CNPJ_VALOR"


def test_classificar_2_centavos_de_diferenca_continua_divergente():
    # Confirma que o fix (round antes de comparar) nao afrouxa a tolerancia
    # real -- 2 centavos de diferenca continua sendo VALOR_DIVERGENTE.
    bills = _bills([
        [601, 591, "NFE ", "999", 100.02, None, "07.393.522/0001-40", "07393522000140", "999"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "999",
           "_valor_float": 100.00, "Chave": None, "Num": "999"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "VALOR_DIVERGENTE"


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


def test_classificar_titulo_de_outro_tipo_nao_conta_mais_01_10():
    """01/10/2026: o fallback "sem tipo" saiu. Um titulo RDV/PPC (nao NFE/NF)
    nunca concilia uma nota -> NAO_ENCONTRADA (pendencia real: o Suprimentos
    corrige o tipo no Sienge). Antes: LANCADA com observacao de tipo."""
    bills = _bills([
        [600, 591, "RDV ", "321", 150.00, None, "07.393.522/0001-40", "07393522000140", "321"],
        [601, 591, "PPC ", "4548", 4266.00, None, "07.393.522/0001-40", "07393522000140", "4548"],
    ])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "321",
           "_valor_float": 150.00, "Chave": None, "Num": "321"}
    assert nf_sienge._classificar_nota(row, bills)["status"] == "NAO_ENCONTRADA"


def test_eh_nota_fiscal_aceita_padding_e_rejeita_outros_tipos():
    assert nf_sienge.eh_nota_fiscal("NFE ") and nf_sienge.eh_nota_fiscal("NF  ") and nf_sienge.eh_nota_fiscal("nfe")
    for t in ("PPC ", "RDV ", "NFS ", "NFC ", "FAT ", "", None):
        assert not nf_sienge.eh_nota_fiscal(t), t


def test_sincronizar_bills_grava_so_nfe_nf_e_pagina_pelo_tamanho_cru(monkeypatch):
    """Pagina com 3 titulos (PPC, NFE, NF) -> so' 2 entram; total devolvido = 2."""
    pagina = [
        {"id": 32010, "documentIdentificationId": "PPC ", "documentNumber": "PPC.4548", "totalInvoiceAmount": 4266.0},
        {"id": 32034, "documentIdentificationId": "NFE ", "documentNumber": "22299", "totalInvoiceAmount": 4266.0},
        {"id": 32040, "documentIdentificationId": "NF  ", "documentNumber": "10", "totalInvoiceAmount": 5.0},
    ]

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": pagina}

    class Sess:
        auth = None
        def get(self, *a, **k): return Resp()

    gravados = []
    monkeypatch.setattr(nf_sienge, "_sessao_sienge", lambda *a: ("http://x", Sess()))
    monkeypatch.setattr(nf_sienge, "execute_values", lambda cur, sql, linhas, template=None: gravados.extend(linhas))

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class Conn:
        def cursor(self): return Cur()

    import datetime
    n = nf_sienge.sincronizar_bills(Conn(), "http://x", "u", "p", datetime.date(2026, 7, 1), datetime.date(2026, 7, 31))
    assert n == 2
    assert [l[0] for l in gravados] == [32034, 32040]


def test_classificar_bills_vazio_nao_quebra():
    bills = _bills([])
    row = {"_cnpj_normalizado": "07393522000140", "_numero_normalizado": "1",
           "_valor_float": 1.0, "Chave": None, "Num": "1"}
    r = nf_sienge._classificar_nota(row, bills)
    assert r["status"] == "NAO_ENCONTRADA"


# ───────── direção reversa (bloco 14, FIX_20260928f) ─────────

def _bills_completo(linhas):
    """Fixture equivalente a _bills() acima, so' que com as colunas que
    _filtrar_bills_orfaos usa (debtor_id/issue_date/creditor_nome --
    acrescentadas a _carregar_bills_creditores nesta mudanca)."""
    cols = ["bill_id", "debtor_id", "creditor_id", "document_identification_id", "document_number",
            "issue_date", "total_invoice_amount", "access_key_number", "creditor_cnpj", "creditor_nome"]
    return pd.DataFrame(linhas, columns=cols)


def test_filtrar_orfaos_bill_valido_entra():
    bills = _bills_completo([
        [900, 591, 10, "NFE ", "111", pd.Timestamp("2026-07-15"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    mapa = {591: "ENERGIA"}
    r = nf_sienge._filtrar_bills_orfaos(
        bills, mapa, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert len(r) == 1
    assert int(r.iloc[0]["bill_id"]) == 900


def test_filtrar_orfaos_tipo_fora_do_universo_nfe_nf_nao_entra():
    bills = _bills_completo([
        [901, 591, 10, "RDV ", "222", pd.Timestamp("2026-07-15"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    mapa = {591: "ENERGIA"}
    r = nf_sienge._filtrar_bills_orfaos(
        bills, mapa, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty, "titulo que nao e' NFE/NF nao deve virar pendencia reversa"


def test_filtrar_orfaos_debtor_de_outra_empresa_nao_entra():
    # Protecao central do bloco 14: Sienge e' 1 conta so' pras 6 empresas,
    # um titulo cujo debtor_id ja' foi confirmado de OUTRA empresa nao
    # pode virar pendencia da empresa que esta' rodando a conferencia.
    bills = _bills_completo([
        [902, 723, 10, "NFE ", "333", pd.Timestamp("2026-07-15"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    mapa = {723: "SMG"}
    r = nf_sienge._filtrar_bills_orfaos(
        bills, mapa, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty


def test_filtrar_orfaos_debtor_nunca_mapeado_nao_entra():
    # debtor_id sem nenhum match LANCADA no historico ainda -- nao arrisca
    # falso positivo, fica de fora ate' a empresa ter pelo menos 1 match.
    bills = _bills_completo([
        [903, 999, 10, "NFE ", "444", pd.Timestamp("2026-07-15"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    r = nf_sienge._filtrar_bills_orfaos(
        bills, {}, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty


def test_filtrar_orfaos_ja_associado_a_alguma_nota_nao_entra():
    # Bill ja' tem sienge_bill_id preenchido em alguma linha de
    # nf_conciliacao (mesmo que so' como candidato VALOR/NUMERO_DIVERGENTE)
    # -- ja' esta' contabilizado do lado do match direto, nao e' "orfao".
    bills = _bills_completo([
        [904, 591, 10, "NFE ", "555", pd.Timestamp("2026-07-15"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    mapa = {591: "ENERGIA"}
    r = nf_sienge._filtrar_bills_orfaos(
        bills, mapa, "ENERGIA", {904}, pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty


def test_filtrar_orfaos_fora_da_janela_de_data_nao_entra():
    bills = _bills_completo([
        [905, 591, 10, "NFE ", "666", pd.Timestamp("2026-01-01"), 500.0, None, "01.234.567/0001-00", "Fornecedor X"],
    ])
    mapa = {591: "ENERGIA"}
    r = nf_sienge._filtrar_bills_orfaos(
        bills, mapa, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty


def test_filtrar_orfaos_bills_vazio_nao_quebra():
    r = nf_sienge._filtrar_bills_orfaos(
        _bills_completo([]), {}, "ENERGIA", set(), pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"),
    )
    assert r.empty


def test_mapear_debtor_para_empresa_inequivoco():
    cur = FakeCursor(fetchall_result=[(591, "ENERGIA", 12)])
    conn = FakeConn(cur)
    mapa = nf_sienge._mapear_debtor_para_empresa(conn)
    assert mapa == {591: "ENERGIA"}


def test_mapear_debtor_para_empresa_ambiguo_fica_de_fora():
    # Mesmo debtor_id ja' bateu LANCADA com 2 empresas diferentes em algum
    # momento do historico -- relacao ambigua, nao confia (nao deveria
    # acontecer, mas ja' teve mais de 1 bug de dado real neste projeto).
    cur = FakeCursor(fetchall_result=[(591, "ENERGIA", 5), (591, "SMG", 1)])
    conn = FakeConn(cur)
    mapa = nf_sienge._mapear_debtor_para_empresa(conn)
    assert mapa == {}


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
