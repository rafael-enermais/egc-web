# -*- coding: utf-8 -*-
"""v0.45.0 -- conferencia VIVA contra Postgres de verdade: todos os meses vigentes da
empresa conferidos juntos, resultado que acompanha o Sienge, re-upload que substitui
sem perder o status manual, "numero divergente" com limite de data."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import nf_sienge  # noqa: E402

CNPJ = "07393522000140"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    nf_sienge.salvar_mapa_debtor(c, 5, "CONST", "t")
    yield c
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
    """notas = [(numero, valor, data_emissao)] -> import_id"""
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        for numero, valor, data in notas:
            cur.execute(
                "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por, criado_em) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'5102','07.393.522/0001-40',%s,%s,'t', COALESCE(%s, now()))",
                (imp, empresa, periodo, numero, numero.lstrip("0"), data, valor, CNPJ, arquivo, criado_em))
    return imp


def _status(c, imp):
    df = nf_sienge.listar_conciliacao(c, imp)
    return dict(zip(df["numero_nota"], df["status"]))


def test_dois_meses_juntos_nao_deixam_orfao_de_borda(conn):
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    _bill(conn, 2, "20", 200.0, "2026-08-02")     # titulo de agosto, perto da fronteira de julho
    jul = _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20")])
    # como era: julho conferido ANTES de agosto existir -> titulo de agosto vira orfao de julho
    assert nf_sienge.conciliar_import(conn, jul)["orfaos_sienge"] == 1
    ago = _import(conn, "ENERGIA", "08/2026", [("20", 200.0, "2026-08-01")])
    res = nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert set(res) == {"07/2026", "08/2026"}
    assert res["07/2026"]["orfaos_sienge"] == 0 and res["08/2026"]["orfaos_sienge"] == 0
    assert nf_sienge.listar_orfaos_sienge(conn, jul).empty
    assert _status(conn, ago) == {"20": "LANCADA"}


def test_resultado_acompanha_o_sienge_e_mostra_o_antes(conn):
    jul = _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20"), ("11", 50.0, "2026-07-21")])
    r1 = nf_sienge.reconferir_empresa(conn, "ENERGIA")["07/2026"]
    assert (r1["lancadas"], r1["pendencias"]) == (0, 2) and r1["antes"] is None
    _bill(conn, 1, "10", 100.0, "2026-07-20")           # a compra lancou a nota 10 no Sienge depois
    r2 = nf_sienge.reconferir_empresa(conn, "ENERGIA")["07/2026"]
    assert (r2["lancadas"], r2["pendencias"]) == (1, 1)
    assert r2["antes"]["pendencias"] == 2               # a tela mostra "2 -> 1"
    assert _status(conn, jul) == {"10": "LANCADA", "11": "NAO_ENCONTRADA"}


def test_reconferir_e_idempotente_e_preserva_status_manual(conn):
    jul = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    reg = int(nf_sienge.listar_conciliacao(conn, jul)["registro_id"].iloc[0])
    nf_sienge.atualizar_status_pendencia(conn, reg, "ENVIADO_SUPRIMENTOS", "ana")
    a = nf_sienge.reconferir_empresa(conn, "ENERGIA")["07/2026"]
    b = nf_sienge.reconferir_empresa(conn, "ENERGIA")["07/2026"]
    assert (a["total"], a["pendencias"]) == (b["total"], b["pendencias"]) == (1, 1)
    assert nf_sienge.listar_conciliacao(conn, jul)["pendencia_status"].iloc[0] == "ENVIADO_SUPRIMENTOS"


def test_subir_o_mes_de_novo_substitui_e_leva_o_status_manual(conn):
    antigo = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21"), ("12", 70.0, "2026-07-22")], criado_em="2026-10-01")
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    df = nf_sienge.listar_conciliacao(conn, antigo).set_index("numero_nota")
    nf_sienge.atualizar_status_pendencia(conn, int(df.loc["11", "registro_id"]), "RESOLVIDO", "ana")
    novo = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21"), ("12", 70.0, "2026-07-22"), ("13", 9.0, "2026-07-23")])
    res = nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert [v["import_id"] for v in nf_sienge.listar_vigentes(conn, "ENERGIA")] == [novo]
    assert res["07/2026"]["total"] == 3
    st = nf_sienge.listar_conciliacao(conn, novo).set_index("numero_nota")["pendencia_status"]
    assert st["11"] == "RESOLVIDO" and st["12"] == "PENDENTE" and st["13"] == "PENDENTE"
    hist = {h["import_id"]: h["vigente"] for h in nf_sienge.listar_historico_importacoes(conn, "ENERGIA")}
    assert hist[str(novo)] is True and hist[str(antigo)] is False
    assert [h["import_id"] for h in nf_sienge.listar_historico_importacoes(conn, "ENERGIA", somente_vigentes=True)] == [str(novo)]
    # o chat/busca enxerga so' a vigente (nota 11 nao aparece 2x)
    assert len(nf_sienge.buscar_notas(conn, numero="11", empresa_codigo="ENERGIA")) == 1


def test_numero_divergente_respeita_limite_de_data(conn):
    _bill(conn, 1, "1000", 100.0, "2026-07-10")
    # valor igual, numero diferente, 31 dias depois: NAO e' erro de digitacao do titulo de julho
    _import(conn, "ENERGIA", "08/2026", [("1001", 100.0, "2026-08-10")])
    r = nf_sienge.reconferir_empresa(conn, "ENERGIA")["08/2026"]
    assert (r["numero_divergente"], r["nao_encontradas"]) == (0, 1)
    # mesmo caso, mas a 5 dias: continua sendo "numero divergente"
    _import(conn, "CONST", "07/2026", [("1001", 100.0, "2026-07-15")])
    r2 = nf_sienge.reconferir_empresa(conn, "CONST")["07/2026"]
    assert r2["numero_divergente"] == 1


def test_titulo_usado_por_um_mes_nao_vira_numero_divergente_do_outro(conn):
    _bill(conn, 1, "1000", 100.0, "2026-07-30")
    jul = _import(conn, "ENERGIA", "07/2026", [("1000", 100.0, "2026-07-30")])
    ago = _import(conn, "ENERGIA", "08/2026", [("1001", 100.0, "2026-08-05")])   # mesmo fornecedor e valor, 6 dias depois
    # como era (cada import sozinho): agosto enxergava o titulo de julho como livre
    assert nf_sienge.conciliar_import(conn, ago)["numero_divergente"] == 1
    res = nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert res["07/2026"]["lancadas"] == 1
    assert (res["08/2026"]["numero_divergente"], res["08/2026"]["nao_encontradas"]) == (0, 1)
    assert _status(conn, jul) == {"1000": "LANCADA"} and _status(conn, ago) == {"1001": "NAO_ENCONTRADA"}


def test_falha_no_meio_nao_perde_o_resultado_anterior(conn, monkeypatch):
    jul = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert _status(conn, jul) == {"11": "NAO_ENCONTRADA"}
    _bill(conn, 1, "11", 50.0, "2026-07-21")
    def _quebra(*a, **k):
        raise RuntimeError("falha simulada")
    monkeypatch.setattr(nf_sienge, "checar_empresa_do_lancamento", _quebra)
    with pytest.raises(RuntimeError):
        nf_sienge.reconferir_empresa(conn, "ENERGIA")
    monkeypatch.undo()
    assert _status(conn, jul) == {"11": "NAO_ENCONTRADA"}     # nada ficou pela metade


def test_resumo_vigentes_e_reconferir_todas(conn):
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    _bill(conn, 2, "20", 200.0, "2026-07-21")           # orfao (devedor 1, sem nota)
    _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20"), ("11", 5.0, "2026-07-22")], arquivo="en.xlsx")
    _import(conn, "CONST", "07/2026", [("30", 9.0, "2026-07-22")])
    todas = nf_sienge.reconferir_todas(conn)
    assert set(todas) == {"ENERGIA", "CONST"}
    linhas = {(r["empresa_codigo"], r["periodo_referencia"]): r for r in nf_sienge.resumo_vigentes(conn)}
    e = linhas[("ENERGIA", "07/2026")]
    assert (e["total_notas"], e["total_lancadas"], e["pendencias_notas"], e["orfaos_sienge"], e["total_pendencias"]) == (2, 1, 1, 1, 2)
    assert e["arquivo_nome"] == "en.xlsx" and e["taxa"] == 0.5
    assert linhas[("CONST", "07/2026")]["total_pendencias"] == 1


def test_devedor_sem_confirmacao_nao_e_adotado_pela_primeira_empresa(conn):
    # v0.45.1: Energia tem 1 nota casada num titulo do devedor 7 (sem confirmacao). Antes, o historico
    # "ensinava" 7 = ENERGIA e a Construtora saia com tudo "lancado em outra empresa".
    with conn.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (592,'X','11.111.111/0001-11') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, issue_date, total_invoice_amount) "
                    "VALUES (9, 7, 592, 'NFE ', '77', '2026-07-10', 70)")
    imp = _import(conn, "ENERGIA", "07/2026", [("77", 70.0, "2026-07-10")])
    with conn.cursor() as cur:
        cur.execute("UPDATE egc.nf_manifesto_import SET cnpj_normalizado='11111111000111' WHERE import_id=%s::uuid", (imp,))
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert nf_sienge._mapear_debtor_para_empresa(conn).get(7) is None
    assert _status(conn, imp) == {"77": "LANCADA"}       # casou, sem alarme de empresa
    d = nf_sienge.listar_debtors_sienge(conn).set_index("debtor_id")
    assert d.loc[7, "empresa_aprendida"] == "ENERGIA" and d.loc[7, "empresa_confirmada"] is None   # fica como sugestao


def test_numero_divergente_exige_numero_parecido(conn):
    _bill(conn, 1, "14281", 2753.2, "2026-07-01")
    _import(conn, "ENERGIA", "07/2026", [("14327", 2753.2, "2026-07-06")])       # recorrente: mesmo valor, outro numero
    r = nf_sienge.reconferir_empresa(conn, "ENERGIA")["07/2026"]
    assert (r["numero_divergente"], r["nao_encontradas"]) == (0, 1)
