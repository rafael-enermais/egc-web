# -*- coding: utf-8 -*-
"""
Notas Fiscais x empresa, contra Postgres de verdade (schema.sql real):
  - nota lancada no Sienge no devedor de OUTRA empresa -> LANCADA_OUTRA_EMPRESA
  - "Sienge sem nota" traz CFOP/valor da mesma nota em outro manifesto
  - mapa debtor->empresa: so' o confirmado vale (v0.45.1); o aprendido e' so' sugestao
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel"),
]

import nf_sienge  # noqa: E402

CNPJ_FORN = "07393522000140"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _bill(c, bill_id, debtor, numero, valor, tipo="NFE "):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591, 'COSTA BENTO', '07.393.522/0001-40') ON CONFLICT DO NOTHING")
        cur.execute(
            "INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, issue_date, total_invoice_amount) "
            "VALUES (%s, %s, 591, %s, %s, '2026-07-10', %s)", (bill_id, debtor, tipo, numero, valor))


def _manifesto(c, empresa, periodo, numero, valor, cfop="5102"):
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
            "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado) VALUES (%s,%s,%s,%s,%s,'2026-07-10',%s,%s,%s,%s)",
            (imp, empresa, periodo, numero, numero.lstrip("0"), valor, cfop, "07.393.522/0001-40", CNPJ_FORN))
    return imp


def test_nota_lancada_no_devedor_de_outra_empresa_vira_pendencia(conn):
    # historico: devedor 10 = ENERGIA, devedor 20 = SMG (confirmado manualmente)
    nf_sienge.salvar_mapa_debtor(conn, 10, "ENERGIA", "t")
    nf_sienge.salvar_mapa_debtor(conn, 20, "SMG", "t")
    _bill(conn, 1, 20, "500", 100.0)           # nota 500 lancada no devedor da SMG
    _bill(conn, 2, 10, "600", 200.0)           # nota 600 lancada no devedor certo
    imp = _manifesto(conn, "ENERGIA", "07/2026", "500", 100.0)
    _manifesto_extra = _manifesto(conn, "ENERGIA", "07/2026", "600", 200.0)
    # as duas notas na MESMA rodada
    with conn.cursor() as cur:
        cur.execute("UPDATE egc.nf_manifesto_import SET import_id = %s WHERE import_id = %s", (imp, _manifesto_extra))
    resumo = nf_sienge.conciliar_import(conn, imp)
    assert resumo["lancadas"] == 1 and resumo["pendencias"] == 1 and resumo["outra_empresa"] == 1
    df = nf_sienge.listar_conciliacao(conn, imp).set_index("numero_nota")
    assert df.loc["500", "status"] == "LANCADA_OUTRA_EMPRESA"
    assert "SMG" in df.loc["500", "observacao"]
    assert df.loc["600", "status"] == "LANCADA"


def test_sem_mapa_nao_gera_alarme_falso(conn):
    _bill(conn, 1, 20, "500", 100.0)
    imp = _manifesto(conn, "ENERGIA", "07/2026", "500", 100.0)
    assert nf_sienge.conciliar_import(conn, imp)["outra_empresa"] == 0


def test_orfao_do_sienge_traz_cfop_do_manifesto_de_outra_empresa(conn):
    nf_sienge.salvar_mapa_debtor(conn, 10, "ENERGIA", "t")
    _bill(conn, 1, 10, "700", 300.0)                     # titulo no devedor da ENERGIA
    imp = _manifesto(conn, "ENERGIA", "07/2026", "999", 50.0)   # manifesto da Energia sem a nota 700
    _manifesto(conn, "SMG", "07/2026", "700", 300.0, cfop="1556")  # mas a nota 700 e' do manifesto da SMG
    nf_sienge.conciliar_import(conn, imp)
    with conn.cursor() as cur:   # o orfao precisa existir (janela/mapa ja' cobertos em outros testes)
        cur.execute(
            "INSERT INTO egc.nf_bills_orfaos (import_id, empresa_codigo, bill_id, document_number, issue_date, total_invoice_amount, creditor_nome, creditor_cnpj, pendencia_status) "
            "VALUES (%s,'ENERGIA',1,'700','2026-07-10',300.0,'COSTA BENTO','07.393.522/0001-40','PENDENTE') ON CONFLICT DO NOTHING", (imp,))
    o = nf_sienge.listar_orfaos_sienge(conn, imp)
    assert len(o) == 1
    assert o.iloc[0]["cfop"] == "1556" and float(o.iloc[0]["valor"]) == 300.0
    assert "SMG" in o.iloc[0]["observacao"] and "empresa errada" in o.iloc[0]["observacao"]
    ab = nf_sienge.listar_orfaos_abertos(conn, "ENERGIA", 10)
    assert len(ab) == 1 and ab.iloc[0]["cfop"] == "1556"


def test_mapa_so_confirmado_vale_o_aprendido_e_so_sugestao(conn):
    nf_sienge.salvar_mapa_debtor(conn, 10, "SOL", "t")
    assert nf_sienge._mapear_debtor_para_empresa(conn)[10] == "SOL"
    nf_sienge.salvar_mapa_debtor(conn, 10, None, "t")
    assert 10 not in nf_sienge._mapear_debtor_para_empresa(conn)
