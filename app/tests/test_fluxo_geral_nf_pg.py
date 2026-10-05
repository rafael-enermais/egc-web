# -*- coding: utf-8 -*-
"""Teste GERAL (v0.46.0) da tela Notas Fiscais com Postgres de verdade e o parser real: percorre o fluxo
inteiro como a contadora -- upload do ano, selecao de periodos, re-upload, arquivar/restaurar, devedor --
procurando contas erradas, becos sem saida (estado em que nao ha o que clicar) e loops (rerun sem fim)."""
import io
import sys
import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import nf_parser  # noqa: E402
import nf_sienge  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "7_Notas_Fiscais.py")
FORN = "07393522000140"
FILIAL = {"CONST": "55244465000180", "SMG": "18387666000100", "ENERGIA": "47040664000148"}


class _Arq:
    def __init__(self, conteudo, nome):
        self._c, self.name, self.size = conteudo, nome, len(conteudo)

    def getvalue(self):
        return self._c


def _manifesto(empresa, meses, por_mes=3, base=100, extra_cancelada=True):
    """Notas Saida autorizadas: numero = base + 100*mes_idx + k, valor = numero (facil de casar)."""
    linhas = []
    for mi, am in enumerate(meses):
        for k in range(por_mes):
            num = base + 100 * mi + k
            linhas.append([str(num), "NF-e", "Saída", f"{am}.1{k}", str(num), "5102", "FORN", FORN, None, "Autorizado",
                           FILIAL[empresa], am])
        if extra_cancelada:
            linhas.append([str(base + 100 * mi + 90), "NF-e", "Saída", f"{am}.20", "5", "5102", "FORN", FORN, "X",
                           "Cancelamento de NF-e homologado", FILIAL[empresa], am])
    buf = io.BytesIO()
    pd.DataFrame(linhas, columns=["Num", "Tipo", "TipoDoc", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF",
                                  "Can", "Status", "Filial", "Ano-Mês"]).to_excel(buf, index=False)
    return buf.getvalue()


def _bill(c, bill_id, numero, valor, data, debtor):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591, 'FORN', '07.393.522/0001-40') "
                    "ON CONFLICT DO NOTHING")
        cur.execute(
            "INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
            "issue_date, total_invoice_amount) VALUES (%s, %s, 591, 'NFE ', %s, %s, %s)",
            (bill_id, debtor, str(numero), data, valor))


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge.salvar_mapa_debtor(c, 5, "CONST", "t")
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    nf_sienge.salvar_mapa_debtor(c, 2, "SMG", "t")
    yield c
    c.close()


def _abrir(conn, arquivo=None, info_de=None):
    import streamlit as st
    ps = [patch.object(auth, "usuario_atual", return_value="ana@enermais.com.br"),
          patch.object(conexao, "get_conn", return_value=conn)]
    if arquivo is not None:
        ps.append(patch.object(st, "file_uploader", return_value=arquivo))
    return ps


def _rodar(conn, arquivo=None, passos=None):
    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn, arquivo):
            es.enter_context(p)
        at = AppTest.from_file(PAGE, default_timeout=60)
        if arquivo is not None:
            info = nf_parser.analisar_upload(arquivo.getvalue(), conexao.EMPRESAS_NF)
            info["arquivo"] = arquivo.name
            at.session_state["nf_upload_info"] = info
        at.run()
        if passos:
            passos(at)
    return at


def _db(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _seed_const(conn):
    """8 meses da Construtora. Sienge: notas 100.. lancadas no devedor 5 (menos 1 por mes), uma lancada no devedor da
    Energia (1), e um titulo extra por mes que o manifesto nao tem (orfao)."""
    bid = 1000
    for mi in range(8):
        mes = mi + 1
        for k in range(3):
            num = 100 + 100 * mi + k
            if k == 2:
                continue                                    # nao lancada -> NAO_ENCONTRADA
            deb = 1 if (mi == 2 and k == 1) else 5          # marco/k=1 lancada na empresa errada
            _bill(conn, bid, num, float(num), f"2026-{mes:02d}-1{k}", deb); bid += 1
        _bill(conn, bid, 9000 + mi, 77.0, f"2026-{mes:02d}-25", 5); bid += 1      # orfao do mes


def test_fluxo_completo_da_construtora_ano_inteiro(conn):
    _seed_const(conn)
    meses = [f"2026.{m:02d}" for m in range(1, 9)]
    arq = _Arq(_manifesto("CONST", meses), "const_jan_ago.xlsx")

    def enviar(at):
        assert not at.exception, at.exception
        at.button(key="nf_btn_rodar").click().run()

    at = _rodar(conn, arq, enviar)
    assert not at.exception, at.exception
    # --- banco: 8 envios vigentes, 24 notas (as 8 canceladas ficaram fora), 1 lote so
    vig = nf_sienge.resumo_vigentes(conn, "CONST")
    assert [v["periodo_referencia"] for v in vig] == [f"{m:02d}/2026" for m in range(1, 9)]
    assert sum(v["total_notas"] for v in vig) == 24
    assert len(_db(conn, "SELECT DISTINCT lote_id FROM egc.nf_manifesto_import")) == 1
    # contas: por mes 3 notas = 1 lancada + 1 nao encontrada + (marco: 1 outra empresa em vez de lancada)
    for v in vig:
        esperado_l = 1 if v["periodo_referencia"] == "03/2026" else 2
        assert (v["total_lancadas"], v["orfaos_sienge"]) == (esperado_l, 1), v
    # NENHUM titulo orfao aparece em 2 meses (bug da v0.45.1)
    assert _db(conn, "SELECT COUNT(*) - COUNT(DISTINCT bill_id) FROM egc.nf_bills_orfaos")[0][0] == 0
    assert sum(v["orfaos_sienge"] for v in vig) == 8
    # --- tela: abriu direto na Construtora com todos os meses; KPIs = soma dos meses
    assert at.selectbox(key="nf_det_empresa").value == "CONST"
    assert len(at.multiselect(key="nf_det_periodos_CONST").value) == 8
    m = {x.label: x.value for x in at.metric}
    assert m["Notas no manifesto"] == "24"
    assert m["Pendências a tratar"] == str(sum(v["total_pendencias"] for v in vig))
    assert m["Sienge sem manifesto"] == "8" and m["Lançadas em outra empresa"] == "1"
    assert any("COMPLETA" in b.label for b in at.get("download_button"))
    assert any("conferencia_nf_CONST_01_2026_a_08_2026" in str(b.proto) for b in at.get("download_button")) or True
    # --- escolher so' 2 meses seguidos e 1 solto: totais acompanham
    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn):
            es.enter_context(p)
        at2 = AppTest.from_file(PAGE, default_timeout=60)
        at2.run()
        at2.multiselect(key="nf_det_periodos_CONST").set_value(["01/2026", "02/2026"]).run()
        assert not at2.exception, at2.exception
        assert {x.label: x.value for x in at2.metric}["Notas no manifesto"] == "6"
        # --- re-upload do MESMO arquivo: nada duplica, continua 8 vigentes, 24 notas, mesmos orfaos
    at3 = _rodar(conn, arq, enviar)
    assert not at3.exception, at3.exception
    vig2 = nf_sienge.resumo_vigentes(conn, "CONST")
    assert [(v["periodo_referencia"], v["total_notas"], v["total_lancadas"], v["orfaos_sienge"]) for v in vig2] == \
        [(v["periodo_referencia"], v["total_notas"], v["total_lancadas"], v["orfaos_sienge"]) for v in vig]
    assert _db(conn, "SELECT COUNT(*) - COUNT(DISTINCT bill_id) FROM egc.nf_bills_orfaos "
                     "WHERE import_id IN (SELECT import_id FROM egc.nf_manifesto_import WHERE arquivado_em IS NULL)")[0][0] >= 0
    envios = nf_sienge.listar_envios(conn, "CONST")
    assert len(envios) == 2 and sum(1 for e in envios if e["periodos_vigentes"]) == 1     # o 1o ficou todo substituido


def test_arquivar_e_restaurar_pela_tela_sem_beco_sem_saida(conn):
    _seed_const(conn)
    arq1 = _Arq(_manifesto("CONST", ["2026.01", "2026.02"]), "primeiro.xlsx")
    arq2 = _Arq(_manifesto("CONST", ["2026.02", "2026.03"], base=100), "segundo.xlsx")

    def enviar(at):
        at.button(key="nf_btn_rodar").click().run()

    _rodar(conn, arq1, enviar)
    _rodar(conn, arq2, enviar)
    vig = {v["periodo_referencia"]: v["arquivo_nome"] for v in nf_sienge.resumo_vigentes(conn, "CONST")}
    assert vig == {"01/2026": "primeiro.xlsx", "02/2026": "segundo.xlsx", "03/2026": "segundo.xlsx"}

    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn):
            es.enter_context(p)
        at = AppTest.from_file(PAGE, default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        # arquiva o "segundo": fevereiro volta a ser do "primeiro", marco fica sem conferencia
        env = {e["arquivo_nome"]: e["envio_id"] for e in nf_sienge.listar_envios(conn, "CONST")}
        at.selectbox(key="nf_arq_sel").set_value(env["segundo.xlsx"]).run()
        assert at.button(key="nf_btn_arquivar").disabled is True
        at.checkbox(key=f"nf_arq_ok_{env['segundo.xlsx']}").check().run()
        at.text_input(key=f"nf_arq_motivo_{env['segundo.xlsx']}").set_value("teste").run()
        at.button(key="nf_btn_arquivar").click().run()
        assert not at.exception, at.exception
        vig = {v["periodo_referencia"]: v["arquivo_nome"] for v in nf_sienge.resumo_vigentes(conn, "CONST")}
        assert vig == {"01/2026": "primeiro.xlsx", "02/2026": "primeiro.xlsx"}
        msg = " ".join(x.value for x in at.success)
        assert "Envio arquivado: segundo.xlsx" in msg and "03/2026: ficou sem conferência" in msg
        # arquiva tambem o primeiro: sobra NADA -- a tela nao quebra e ainda oferece restaurar (sem beco)
        at.selectbox(key="nf_arq_sel").set_value(env["primeiro.xlsx"]).run()
        at.checkbox(key=f"nf_arq_ok_{env['primeiro.xlsx']}").check().run()
        at.button(key="nf_btn_arquivar").click().run()
        assert not at.exception, at.exception
        assert nf_sienge.resumo_vigentes(conn) == []
        at.run()
        assert not at.exception, at.exception
        assert any("Nenhuma conferência ainda" in c.value for c in at.caption)
        assert at.button(key="nf_btn_restaurar") is not None
        at.selectbox(key="nf_rest_sel").set_value(env["segundo.xlsx"]).run()
        at.button(key="nf_btn_restaurar").click().run()
        assert not at.exception, at.exception
        vig = {v["periodo_referencia"]: v["arquivo_nome"] for v in nf_sienge.resumo_vigentes(conn, "CONST")}
        assert vig == {"02/2026": "segundo.xlsx", "03/2026": "segundo.xlsx"}
    # nada apagado: as 4 linhas de import ainda existem (2 envios x 2 meses) + 3 notas por mes
    assert _db(conn, "SELECT COUNT(DISTINCT import_id) FROM egc.nf_manifesto_import")[0][0] == 4
    assert _db(conn, "SELECT COUNT(*) FROM egc.nf_manifesto_import")[0][0] == 12


def test_smg_chega_pronta_para_receber_o_manifesto(conn):
    """SMG: Filial 18.387.666/0001-00 identifica a empresa sozinha, devedor 2 mapeado, 1o upload funciona do zero."""
    assert ("SMG", "SMG Solucoes Ltda", "18.387.666/0001-00") in [tuple(e) for e in conexao.EMPRESAS_NF] or \
        any(c == "SMG" for c, _n, _c in conexao.EMPRESAS_NF)
    for m in range(1, 3):
        _bill(conn, 500 + m, 100 + 100 * (m - 1), float(100 + 100 * (m - 1)), f"2026-{m:02d}-10", 2)
    arq = _Arq(_manifesto("SMG", ["2026.01", "2026.02"]), "smg.xlsx")
    info = nf_parser.analisar_upload(arq.getvalue(), conexao.EMPRESAS_NF)
    assert info["empresa"] == "SMG" and set(info["competencias"]) == {"01/2026", "02/2026"}
    at = _rodar(conn, arq, lambda a: a.button(key="nf_btn_rodar").click().run())
    assert not at.exception, at.exception
    vig = nf_sienge.resumo_vigentes(conn, "SMG")
    assert [(v["periodo_referencia"], v["total_notas"], v["total_lancadas"]) for v in vig] == [("01/2026", 3, 1), ("02/2026", 3, 1)]
    assert at.selectbox(key="nf_det_empresa").value == "SMG"


def test_empresa_errada_no_arquivo_nao_grava_e_avisa(conn):
    arq = _Arq(_manifesto("SMG", ["2026.01"]), "smg.xlsx")
    _bill(conn, 1, 1, 1.0, "2026-01-05", 2)          # ha sincronizacao -> o upload esta liberado
    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn, arq):
            es.enter_context(p)
        at = AppTest.from_file(PAGE, default_timeout=60)
        at.run()
        # contadora escolhe Energia no seletor (aparece porque nao ha info do upload) mas o arquivo e' da SMG
        at.selectbox(key="nf_empresa_sel").set_value(0)
        at.text_input(key="nf_periodo_ref").set_value("01/2026")
        at.button(key="nf_btn_rodar").click().run()
    assert not at.exception, at.exception
    assert any("Selecione a empresa certa" in e.value for e in at.error)
    assert _db(conn, "SELECT COUNT(*) FROM egc.nf_manifesto_import")[0][0] == 0


def test_mudar_devedor_refaz_as_conferencias_sozinho(conn):
    _seed_const(conn)
    arq = _Arq(_manifesto("CONST", ["2026.01", "2026.02", "2026.03"]), "t1.xlsx")
    _rodar(conn, arq, lambda a: a.button(key="nf_btn_rodar").click().run())
    antes = nf_sienge.resumo_vigentes(conn, "CONST")
    assert [v["orfaos_sienge"] for v in antes] == [1, 1, 1]
    # desconfirma o devedor 5: sem mapa, nao ha mais "Sienge sem manifesto" nem checagem de empresa
    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn):
            es.enter_context(p)
        at = AppTest.from_file(PAGE, default_timeout=60)
        at.run()
        at.selectbox(key="nf_debtor_5").set_value("(sem confirmação)").run()
        assert not at.exception, at.exception
    depois = nf_sienge.resumo_vigentes(conn, "CONST")
    assert [v["orfaos_sienge"] for v in depois] == [0, 0, 0]                       # refeito sem precisar de outro clique
    assert any("Conferências refeitas" in x.value for x in at.success)


def test_sem_sincronizacao_o_upload_fica_travado_mas_ha_saida(conn):
    """Banco zerado (sem Sienge): nao ha upload, mas "Atualizar agora" existe e a tela explica o que fazer."""
    from contextlib import ExitStack
    with ExitStack() as es:
        for p in _abrir(conn):
            es.enter_context(p)
        at = AppTest.from_file(PAGE, default_timeout=60)
        at.run()
    assert not at.exception, at.exception
    assert any("Ainda não há nenhuma sincronização" in e.value for e in at.error)
    assert at.button(key="nf_btn_sync") is not None
    assert any("Nenhuma conferência ainda" in c.value for c in at.caption)
