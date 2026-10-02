# -*- coding: utf-8 -*-
"""v0.44.3 -- conferencia de Notas Fiscais contra Postgres de verdade:
escopo dos "Sienge sem manifesto", avisos de cobertura, resumo por status,
data do manifesto gravada certa e badge "Ativo" por geracao."""
import datetime as dt
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import db  # noqa: E402
import importacoes_ui  # noqa: E402
import nf_parser  # noqa: E402
import nf_sienge  # noqa: E402

CNPJ_FORN = "07393522000140"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _bill(c, bill_id, debtor, numero, valor, data="2026-07-10"):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591, 'COSTA BENTO', '07.393.522/0001-40') ON CONFLICT DO NOTHING")
        cur.execute(
            "INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, issue_date, total_invoice_amount) "
            "VALUES (%s, %s, 591, 'NFE ', %s, %s, %s)", (bill_id, debtor, numero, data, valor))


def _manifesto(c, empresa, periodo, notas, imp=None, criado_em=None):
    """notas = [(numero, valor)]; devolve import_id."""
    imp = imp or str(uuid.uuid4())
    with c.cursor() as cur:
        for numero, valor in notas:
            cur.execute(
                "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, criado_em) "
                "VALUES (%s,%s,%s,%s,%s,'2026-07-10',%s,'5102','07.393.522/0001-40',%s, COALESCE(%s, now()))",
                (imp, empresa, periodo, numero, numero.lstrip("0"), valor, CNPJ_FORN, criado_em))
    return imp


def _mapa(c):
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    nf_sienge.salvar_mapa_debtor(c, 5, "CONST", "t")


def test_conciliar_nao_gera_numero_divergente_falso_e_resume_por_status(conn):
    _mapa(conn)
    _bill(conn, 1, 1, "10", 100.0)
    imp = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0), ("11", 100.0), ("12", 55.0)])
    r = nf_sienge.conciliar_import(conn, imp)
    assert (r["total"], r["lancadas"], r["pendencias"]) == (3, 1, 2)
    assert r["numero_divergente"] == 0 and r["nao_encontradas"] == 2
    df = nf_sienge.listar_conciliacao(conn, imp).set_index("numero_nota")
    assert df.loc["11", "status"] == "NAO_ENCONTRADA"


def test_orfao_nao_e_escondido_por_rodada_antiga_do_mesmo_periodo(conn):
    _mapa(conn)
    _bill(conn, 1, 1, "10", 100.0)
    _bill(conn, 2, 1, "20", 200.0)   # titulo sem nota nenhuma nesta rodada
    antiga = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0), ("20", 200.0)], criado_em="2026-09-01")
    nf_sienge.conciliar_import(conn, antiga)           # a antiga associou o titulo 2
    nova = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0)])
    r = nf_sienge.conciliar_import(conn, nova)
    assert r["orfaos_sienge"] == 1                      # titulo 2 e' orfao NESTA rodada
    assert nf_sienge.listar_orfaos_sienge(conn, nova).iloc[0]["sienge_bill_id"] == 2


def test_titulo_conferido_em_outro_periodo_da_mesma_empresa_nao_e_orfao(conn):
    _mapa(conn)
    _bill(conn, 1, 1, "10", 100.0, "2026-07-10")
    _bill(conn, 2, 1, "20", 200.0, "2026-08-02")      # dentro da janela (+-15d) do manifesto de julho
    ago = _manifesto(conn, "ENERGIA", "08/2026", [("20", 200.0)])
    nf_sienge.conciliar_import(conn, ago)
    jul = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0)])
    assert nf_sienge.conciliar_import(conn, jul)["orfaos_sienge"] == 0


def test_titulo_com_nota_no_manifesto_de_outra_empresa_vira_orfao_com_aviso(conn):
    _mapa(conn)
    _bill(conn, 1, 1, "10", 100.0)
    _bill(conn, 3, 1, "30", 300.0)                      # lancado no devedor ENERGIA...
    const = _manifesto(conn, "CONST", "07/2026", [("30", 300.0)])   # ...mas a nota e' da CONST
    nf_sienge.conciliar_import(conn, const)
    en = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0)])
    assert nf_sienge.conciliar_import(conn, en)["orfaos_sienge"] == 1
    o = nf_sienge.listar_orfaos_sienge(conn, en).iloc[0]
    assert "CONST" in o["observacao"] and "empresa errada" in o["observacao"]


def test_avisos_sem_devedor_da_empresa_e_espelho_que_nao_cobre(conn):
    _bill(conn, 1, 1, "99", 100.0, "2026-07-20")      # titulo que nao casa com nota nenhuma -> nada e' "aprendido"
    imp = _manifesto(conn, "ENERGIA", "07/2026", [("10", 100.0)])   # emissao 2026-07-10, espelho so' a partir de 07-20
    nf_sienge.conciliar_import(conn, imp)
    avisos = nf_sienge.avisos_conciliacao(conn, imp, "ENERGIA")
    assert any("Nenhum devedor" in a and "ENERGIA" in a for a in avisos)
    assert any("a partir de 20/07/2026" in a for a in avisos)
    _mapa(conn)
    avisos = nf_sienge.avisos_conciliacao(conn, imp, "ENERGIA")
    assert not any("Nenhum devedor" in a for a in avisos)


def test_gravar_manifesto_grava_a_data_da_receita_sem_trocar_dia_e_mes(conn):
    import io
    buf = io.BytesIO()
    pd.DataFrame([["77", "NF-e", "2026.07.06", "10", "5102", "A", CNPJ_FORN]],
                 columns=["Num", "Tipo", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF"]).to_excel(buf, index=False)
    buf.seek(0)
    df = nf_parser.ler_manifesto_xlsx(buf)
    imp = nf_sienge.gravar_manifesto(conn, "ENERGIA", "07/2026", df, "a.xlsx", "t")
    with conn.cursor() as cur:
        cur.execute("SELECT data_emissao FROM egc.nf_manifesto_import WHERE import_id = %s", (imp,))
        assert cur.fetchone()[0] == dt.date(2026, 7, 6)


def test_painel_de_devedores_mostra_historico_ambiguo(conn):
    _bill(conn, 1, 1, "10", 100.0)
    _bill(conn, 2, 1, "11", 100.0)
    for numero, emp, bill in (("10", "ENERGIA", 1), ("11", "CONST", 2)):
        imp = _manifesto(conn, emp, "07/2026", [(numero, 100.0)])
        with conn.cursor() as cur:   # historico de matches LANCADA do mesmo devedor em 2 empresas
            cur.execute("INSERT INTO egc.nf_conciliacao (import_id, manifesto_id, status, sienge_bill_id) "
                        "SELECT import_id, id, 'LANCADA', %s FROM egc.nf_manifesto_import WHERE import_id = %s", (bill, imp))
    d = nf_sienge.listar_debtors_sienge(conn).set_index("debtor_id")
    assert d.loc[1, "empresa_aprendida"] is None
    assert "ENERGIA (1)" in d.loc[1, "historico_ambiguo"] and "CONST (1)" in d.loc[1, "historico_ambiguo"]


def test_badge_ativo_depois_de_reimportar_o_mesmo_arquivo_e_desfazer(conn):
    P = dt.date(2026, 6, 30)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM egc.lancamentos WHERE empresa_codigo='ENERGIA' AND periodo=%s", (P,))
    rows = [["RECEITA BRUTA", "100,00", "RECEITAS", "x"]]
    db.inserir_lancamentos(conn, "ENERGIA", "DRE", P, rows, "mesmo.pdf", "u", None, "trimestral")
    with conn.cursor() as cur:  # geracao A foi criada ha' 1 hora
        cur.execute("UPDATE egc.lancamentos SET criado_em = now() - interval '1 hour' WHERE empresa_codigo='ENERGIA' AND periodo=%s", (P,))
    db.inativar_periodo_existente(conn, "ENERGIA", P, "DRE", granularidade="trimestral")
    db.inserir_lancamentos(conn, "ENERGIA", "DRE", P, rows, "mesmo.pdf", "u", None, "trimestral")   # geracao B, mesmo nome
    evento_b = dt.datetime.now(dt.timezone.utc)
    ativas = db.geracoes_ativas_por_periodo(conn, "ENERGIA")[P]
    assert importacoes_ui.evento_esta_ativo(evento_b, ativas) is True
    db.desfazer_importacao(conn, "ENERGIA", P)          # B arquivada, A volta a ficar ATIVA
    ativas = db.geracoes_ativas_por_periodo(conn, "ENERGIA")[P]
    assert importacoes_ui.evento_esta_ativo(evento_b, ativas) is False   # o nome e' igual, a geracao nao


def _ignoradas_df(linhas):
    return pd.DataFrame([{
        "numero_nota": n, "data_emissao": dt.date(2026, 7, 14), "valor": v, "cfop": "2201", "fornecedor_nome": "F. BORGES",
        "fornecedor_cnpj": "07.393.522/0001-40", "cnpj_normalizado": CNPJ_FORN, "tipo_doc": "Entrada",
        "natureza": "Dev vda prod do estab", "motivo": nf_parser.MOTIVO_ENTRADA, "competencia": "07/2026"} for n, v in linhas])


def test_ignoradas_sao_gravadas_listadas_e_geram_observacao_na_pendencia(conn):
    _mapa(conn)
    imp = _manifesto(conn, "ENERGIA", "07/2026", [("5700", 4100.0), ("11", 55.0)])
    assert nf_sienge.gravar_ignoradas(conn, imp, "ENERGIA", "07/2026", _ignoradas_df([("5735", 4100.0)]), "t") == 1
    lst = nf_sienge.listar_ignoradas(conn, imp)
    assert list(lst["numero_nota"]) == ["5735"] and lst.iloc[0]["motivo"] == nf_parser.MOTIVO_ENTRADA
    nf_sienge.conciliar_import(conn, imp)
    df = nf_sienge.listar_conciliacao(conn, imp).set_index("numero_nota")
    assert df.loc["5700", "status"] == "NAO_ENCONTRADA"             # NAO e' excluida...
    assert "devolução/entrada nº 5735 (mesmo valor)" in df.loc["5700", "observacao"]   # ...so' avisa
    assert pd.isna(df.loc["11", "observacao"])


def test_sem_a_tabela_de_ignoradas_a_conferencia_continua(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.nf_manifesto_ignoradas")
    imp = _manifesto(conn, "ENERGIA", "07/2026", [("1", 1.0)])
    assert nf_sienge.gravar_ignoradas(conn, imp, "ENERGIA", "07/2026", _ignoradas_df([("5735", 4100.0)]), "t") == 0
    assert nf_sienge.listar_ignoradas(conn, imp).empty
    assert nf_sienge.conciliar_import(conn, imp)["total"] == 1


def test_sette_existe_como_empresa_e_o_devedor_21_vem_mapeado(conn):
    imp = _manifesto(conn, "SETTE", "07/2026", [("1", 1.0)])        # FK em egc.empresas
    assert nf_sienge.conciliar_import(conn, imp)["total"] == 1
    assert nf_sienge.carregar_mapa_debtor_manual(conn).get(21) == "SETTE"
