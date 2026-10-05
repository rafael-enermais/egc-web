# -*- coding: utf-8 -*-
"""v0.46.3 -- pista de combustivel na Observacao + Anotacoes da contadora (egc.nf_anotacao) +
exemplos por devedor, contra Postgres de verdade."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import nf_sienge  # noqa: E402
import nf_export  # noqa: E402

CNPJ = "07393522000140"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge._TAB_ANOTACAO["ok"] = False
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    yield c
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge._TAB_ANOTACAO["ok"] = False
    c.close()


def _bill(c, bill_id, numero, valor, data, debtor=1):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591, 'COSTA BENTO', '07.393.522/0001-40') "
                    "ON CONFLICT DO NOTHING")
        cur.execute(
            "INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
            "issue_date, total_invoice_amount) VALUES (%s, %s, 591, 'NFE ', %s, %s, %s)",
            (bill_id, debtor, numero, data, valor))


def _import(c, empresa, periodo, notas, criado_em=None, arquivo="a.xlsx"):
    """notas = [(numero, valor, data, cfop)]"""
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        for numero, valor, data, cfop in notas:
            cur.execute(
                "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por, criado_em) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'07.393.522/0001-40',%s,%s,'t', COALESCE(%s::timestamptz, now()))",
                (imp, empresa, periodo, numero, numero.lstrip("0"), data, valor, cfop, CNPJ, arquivo, criado_em))
    return imp


def test_observacao_combustivel_so_quando_todos_os_cfop_sao_de_combustivel():
    f = nf_sienge.observacao_combustivel
    assert f("5929") and f("5667") and f("5656") and f("5667, 5929")
    assert f("5102") is None and f("5405, 5667") is None and f(None) is None and f("") is None


def test_tabela_marca_combustivel_so_em_nao_encontrada_e_sem_mexer_no_status(conn):
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20", "5929"),     # lancada: sem pista
                                         ("20", 50.0, "2026-07-21", "5929"),       # nao encontrada, combustivel
                                         ("30", 70.0, "2026-07-22", "5102")])      # nao encontrada, outro CFOP
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    t = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    por = {r["numero_nota"]: r for _, r in t.iterrows()}
    assert por["10"]["status"] == "LANCADA" and not por["10"]["observacao"]
    assert por["20"]["status"] == "NAO_ENCONTRADA" and por["20"]["observacao"] == nf_sienge.OBS_COMBUSTIVEL
    assert por["30"]["status"] == "NAO_ENCONTRADA" and not por["30"]["observacao"]
    assert (t["status"] != "LANCADA").sum() == 2          # as duas continuam nas pendencias
    xl = nf_export.preparar_tabela(t)
    assert "Anotação" in xl.columns and "chave_ref" not in xl.columns


def test_anotacao_acompanha_a_nota_apos_novo_upload_atualizar_e_remocao(conn):
    _import(conn, "ENERGIA", "07/2026", [("20", 50.0, "2026-07-21", "5102")], criado_em="2026-10-01 09:00+00")
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    t = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    assert t["anotacao"].isna().all()
    ref = t.iloc[0]["chave_ref"]
    nf_sienge.salvar_anotacao(conn, "ENERGIA", ref, "  fornecedor vai reemitir  ", "ana")
    # novo upload do mesmo mes (outro import_id) + "Atualizar agora": a anotacao continua na mesma nota
    _import(conn, "ENERGIA", "07/2026", [("000020", 50.0, "2026-07-21", "5102")], criado_em="2026-10-02 09:00+00")
    nf_sienge.reconferir_todas(conn)
    t2 = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    assert list(t2["anotacao"]) == ["fornecedor vai reemitir"]
    # editar e remover
    nf_sienge.salvar_anotacao(conn, "ENERGIA", ref, "aguardando", "bia")
    assert nf_sienge.carregar_anotacoes(conn, ["ENERGIA"]) == {ref: "aguardando"}
    nf_sienge.salvar_anotacao(conn, "ENERGIA", ref, "", "bia")
    assert nf_sienge.carregar_anotacoes(conn, ["ENERGIA"]) == {}
    # mesma nota em OUTRA empresa nao herda
    nf_sienge.salvar_anotacao(conn, "ENERGIA", ref, "so energia", "ana")
    assert nf_sienge.carregar_anotacoes(conn, ["CONST"]) == {}


def test_anotacao_em_titulo_sienge_sem_nota(conn):
    _bill(conn, 77, "777", 10.0, "2026-07-15")
    _import(conn, "ENERGIA", "07/2026", [("20", 50.0, "2026-07-21", "5102")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    t = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    orf = t[t["origem"] == "SIENGE_ORFAO"]
    assert len(orf) == 1 and orf.iloc[0]["chave_ref"] == "ENERGIA|bill|77"
    nf_sienge.salvar_anotacao(conn, "ENERGIA", "ENERGIA|bill|77", "titulo de teste", "ana")
    t = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    assert t[t["origem"] == "SIENGE_ORFAO"].iloc[0]["anotacao"] == "titulo de teste"


def test_sem_o_bloco_24_tudo_segue_funcionando_e_salvar_avisa(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.nf_anotacao")
    nf_sienge._TAB_ANOTACAO["ok"] = False
    _import(conn, "ENERGIA", "07/2026", [("20", 50.0, "2026-07-21", "5929")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    t = nf_sienge.tabela_conferencia(conn, nf_sienge.listar_vigentes(conn, "ENERGIA"))
    assert len(t) == 1 and t["anotacao"].isna().all() and t.iloc[0]["observacao"] == nf_sienge.OBS_COMBUSTIVEL
    with pytest.raises(RuntimeError, match="BLOCO 24"):
        nf_sienge.salvar_anotacao(conn, "ENERGIA", "x", "texto", "ana")


def test_devedores_trazem_exemplos_de_titulos_pra_conferir_no_sienge(conn):
    for i in range(5):
        _bill(conn, 100 + i, str(900 + i), 10.0 + i, f"2026-07-{10 + i}", debtor=2)
    d = nf_sienge.listar_debtors_sienge(conn)
    ex = d[d["debtor_id"] == 2].iloc[0]["exemplos"]
    assert len(ex) == 3 and "título 104" in ex[0] and "COSTA BENTO" in ex[0]
