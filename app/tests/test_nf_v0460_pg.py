# -*- coding: utf-8 -*-
"""v0.46.0 -- arquivar/restaurar envio (desfazer upload sem apagar), envios por arquivo/lote,
conferencia de varios periodos numa tabela so' -- contra Postgres de verdade."""
import sys
import uuid
import datetime as dt
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
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    yield c
    nf_sienge._COL_ARQUIVADO["ok"] = False
    c.close()


def _bill(c, bill_id, numero, valor, data, debtor=1):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591, 'COSTA BENTO', '07.393.522/0001-40') "
                    "ON CONFLICT DO NOTHING")
        cur.execute(
            "INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
            "issue_date, total_invoice_amount) VALUES (%s, %s, 591, 'NFE ', %s, %s, %s)",
            (bill_id, debtor, numero, data, valor))


def _import(c, empresa, periodo, notas, criado_em=None, arquivo="a.xlsx", lote=None):
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        for numero, valor, data in notas:
            cur.execute(
                "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por, criado_em, lote_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'5102','07.393.522/0001-40',%s,%s,'t', COALESCE(%s::timestamptz, now()), %s)",
                (imp, empresa, periodo, numero, numero.lstrip("0"), data, valor, CNPJ, arquivo, criado_em, lote))
    return imp


def _vig(c, empresa="ENERGIA"):
    return {v["periodo_referencia"]: v for v in nf_sienge.listar_vigentes(c, empresa)}


def test_arquivar_o_envio_novo_devolve_o_vigente_ao_anterior_sem_apagar_nada(conn):
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    antigo = _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20")], criado_em="2026-10-01 09:00+00", arquivo="certo.xlsx")
    novo = _import(conn, "ENERGIA", "07/2026", [("99", 5.0, "2026-07-21")], criado_em="2026-10-02 09:00+00", arquivo="errado.xlsx")
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert _vig(conn)["07/2026"]["import_id"] == novo

    prev = nf_sienge.previa_arquivamento(conn, [novo])
    assert prev == [{"empresa_codigo": "ENERGIA", "periodo_referencia": "07/2026", "import_id": novo, "passa_a_valer": "certo.xlsx"}]
    res = nf_sienge.arquivar_envio(conn, [novo], "ana", "arquivo errado")
    assert res["afetados"] == 1
    assert res["mudancas"] == [{"empresa_codigo": "ENERGIA", "periodo_referencia": "07/2026",
                                "antes": "errado.xlsx", "depois": "certo.xlsx"}]
    assert _vig(conn)["07/2026"]["import_id"] == antigo
    # o antigo foi reconferido (volta a mostrar a nota 10 como LANCADA)
    assert dict(zip(nf_sienge.listar_conciliacao(conn, antigo)["numero_nota"],
                    nf_sienge.listar_conciliacao(conn, antigo)["status"])) == {"10": "LANCADA"}
    # NADA foi apagado: linhas do arquivado, conciliacao e historico continuam la'
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), MAX(arquivado_por), MAX(arquivado_motivo) FROM egc.nf_manifesto_import WHERE import_id = %s", (novo,))
        assert cur.fetchone() == (1, "ana", "arquivo errado")
        cur.execute("SELECT COUNT(*) FROM egc.nf_conciliacao WHERE import_id = %s", (novo,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM egc.nf_import_historico WHERE import_id = %s", (novo,))
        assert cur.fetchone()[0] == 1
    hist = {h["import_id"]: h for h in nf_sienge.listar_historico_importacoes(conn, "ENERGIA")}
    assert hist[novo]["arquivado"] is True and hist[novo]["vigente"] is False
    assert hist[antigo]["arquivado"] is False and hist[antigo]["vigente"] is True
    # consultas que alimentam tela e chat nao enxergam o arquivado
    assert nf_sienge.buscar_notas(conn, numero="99") == []          # nota so' do envio arquivado nao aparece no chat
    assert len(nf_sienge.buscar_notas(conn, numero="10")) == 1
    assert [r["import_id"] for r in nf_sienge.resumo_vigentes(conn, "ENERGIA")] == [antigo]
    assert set(nf_sienge.listar_pendencias_abertas(conn, "ENERGIA")["numero_nota"]) == set()


def test_arquivar_unico_envio_deixa_o_mes_sem_conferencia_e_restaurar_traz_de_volta(conn):
    imp = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21")], arquivo="x.xlsx")
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    assert nf_sienge.previa_arquivamento(conn, [imp])[0]["passa_a_valer"] is None
    res = nf_sienge.arquivar_envio(conn, [imp], "ana")
    assert _vig(conn) == {}
    assert res["mudancas"][0]["depois"] is None
    assert len(nf_sienge.listar_pendencias_abertas(conn, "ENERGIA")) == 0
    nf_sienge.restaurar_envio(conn, [imp], "ana")
    assert _vig(conn)["07/2026"]["import_id"] == imp
    assert len(nf_sienge.listar_pendencias_abertas(conn, "ENERGIA")) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT arquivado_em, arquivado_por, arquivado_motivo FROM egc.nf_manifesto_import WHERE import_id = %s", (imp,))
        assert cur.fetchone() == (None, None, None)


def test_arquivar_preserva_acompanhamento_manual_da_pendencia(conn):
    imp = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    reg = int(nf_sienge.listar_conciliacao(conn, imp)["registro_id"].iloc[0])
    nf_sienge.atualizar_status_pendencia(conn, reg, "ENVIADO_SUPRIMENTOS", "ana")
    nf_sienge.arquivar_envio(conn, [imp], "ana")
    nf_sienge.restaurar_envio(conn, [imp], "ana")
    assert nf_sienge.listar_conciliacao(conn, imp)["pendencia_status"].iloc[0] == "ENVIADO_SUPRIMENTOS"


def test_envio_agrupa_os_meses_do_mesmo_arquivo_por_lote_e_arquiva_todos_juntos(conn):
    lote = str(uuid.uuid4())
    a = _import(conn, "ENERGIA", "06/2026", [("1", 1.0, "2026-06-10")], lote=lote, arquivo="jan_ago.xlsx")
    b = _import(conn, "ENERGIA", "07/2026", [("2", 2.0, "2026-07-10"), ("3", 3.0, "2026-07-11")], lote=lote, arquivo="jan_ago.xlsx")
    c = _import(conn, "ENERGIA", "08/2026", [("4", 4.0, "2026-08-10")], arquivo="outro.xlsx")        # outro envio
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    envios = nf_sienge.listar_envios(conn, "ENERGIA")
    assert len(envios) == 2
    grande = next(e for e in envios if e["arquivo_nome"] == "jan_ago.xlsx")
    assert grande["periodos"] == ["06/2026", "07/2026"] and grande["total_notas"] == 3
    assert set(grande["import_ids"]) == {a, b} and grande["arquivado"] is False
    assert grande["periodos_vigentes"] == ["06/2026", "07/2026"]
    nf_sienge.arquivar_envio(conn, grande["import_ids"], "ana", "teste")
    assert set(_vig(conn)) == {"08/2026"}
    envios = nf_sienge.listar_envios(conn, "ENERGIA")
    arq = next(e for e in envios if e["arquivo_nome"] == "jan_ago.xlsx")
    assert arq["arquivado"] is True and arq["arquivado_por"] == "ana" and arq["arquivado_motivo"] == "teste"
    assert not next(e for e in envios if e["arquivo_nome"] == "outro.xlsx")["arquivado"]
    assert nf_sienge.listar_vigentes(conn, "ENERGIA")[0]["import_id"] == c


def test_envio_antigo_sem_lote_agrupa_por_arquivo_usuario_e_horario():
    base = dt.datetime(2026, 10, 1, 9, 0, 0)
    imports = [
        dict(import_id="a", empresa_codigo="ENERGIA", periodo_referencia="08/2026", arquivo_nome="x.xlsx", criado_por="t",
             criado_em=base + dt.timedelta(seconds=4), notas=1, lote_id=None, arquivado_em=None),
        dict(import_id="b", empresa_codigo="ENERGIA", periodo_referencia="07/2026", arquivo_nome="x.xlsx", criado_por="t",
             criado_em=base, notas=2, lote_id=None, arquivado_em=None),
        dict(import_id="c", empresa_codigo="ENERGIA", periodo_referencia="07/2026", arquivo_nome="x.xlsx", criado_por="t",
             criado_em=base + dt.timedelta(days=3), notas=5, lote_id=None, arquivado_em=None),   # mesmo nome, outro dia
        dict(import_id="d", empresa_codigo="SMG", periodo_referencia="07/2026", arquivo_nome="x.xlsx", criado_por="t",
             criado_em=base + dt.timedelta(seconds=2), notas=7, lote_id=None, arquivado_em=None),  # outra empresa
    ]
    envios = nf_sienge._agrupar_envios(imports)
    por_ids = {tuple(sorted(e["import_ids"])) for e in envios}
    assert por_ids == {("a", "b"), ("c",), ("d",)}
    e_ab = next(e for e in envios if set(e["import_ids"]) == {"a", "b"})
    assert e_ab["periodos"] == ["07/2026", "08/2026"] and e_ab["total_notas"] == 3


def test_tabela_conferencia_junta_periodos_com_coluna_periodo_e_orfaos(conn):
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    _bill(conn, 2, "20", 200.0, "2026-08-02")
    _bill(conn, 3, "30", 300.0, "2026-08-09")            # titulo de agosto sem nota no manifesto -> orfao de agosto
    _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20"), ("11", 50.0, "2026-07-21")])
    _import(conn, "ENERGIA", "08/2026", [("20", 200.0, "2026-08-01")])
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    vig = nf_sienge.resumo_vigentes(conn, "ENERGIA")
    t = nf_sienge.tabela_conferencia(conn, vig)
    assert list(t.columns[:2]) == ["periodo", "numero_nota"]
    manifesto = t[t["origem"] == "MANIFESTO"]
    assert sorted(zip(manifesto["periodo"], manifesto["numero_nota"], manifesto["status"])) == [
        ("07/2026", "10", "LANCADA"), ("07/2026", "11", "NAO_ENCONTRADA"), ("08/2026", "20", "LANCADA")]
    orf = t[t["origem"] == "SIENGE_ORFAO"]
    assert list(orf["periodo"]) == ["08/2026"] and len(orf) == 1
    r = nf_export.resumo_conferencia(t)
    assert (r["total"], r["lancadas"], r["pendencias"], r["sienge_sem_manifesto"]) == (3, 2, 2, 1)
    rpp = nf_export.resumo_por_periodo(t)
    assert list(rpp["Período"]) == ["07/2026", "08/2026"] and list(rpp["Total de pendências"]) == [1, 1]
    # o total do conjunto = soma dos totais mensais (numeros da tela 3 batem com o detalhe)
    assert sum(v["total_pendencias"] for v in vig) == r["pendencias"]


def test_sem_a_coluna_do_bloco_23_tudo_funciona_como_antes_e_arquivar_avisa(conn):
    imp = _import(conn, "ENERGIA", "07/2026", [("11", 50.0, "2026-07-21")])
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE egc.nf_manifesto_import DROP COLUMN lote_id, DROP COLUMN arquivado_em, "
                    "DROP COLUMN arquivado_por, DROP COLUMN arquivado_motivo")
    nf_sienge._COL_ARQUIVADO["ok"] = False
    assert nf_sienge.tem_arquivamento(conn) is False
    res = nf_sienge.reconferir_empresa(conn, "ENERGIA")                 # conferencia segue normal
    assert res["07/2026"]["total"] == 1
    assert len(nf_sienge.listar_pendencias_abertas(conn, "ENERGIA")) == 1
    assert len(nf_sienge.listar_envios(conn, "ENERGIA")) == 1
    assert nf_sienge.listar_historico_importacoes(conn, "ENERGIA")[0]["arquivado"] is False
    assert len(nf_sienge.listar_orfaos_abertos(conn, "ENERGIA")) == 0
    with pytest.raises(nf_sienge.ArquivamentoIndisponivel):
        nf_sienge.arquivar_envio(conn, [imp], "ana")
    # gravar_manifesto aceita lote_id mesmo sem a coluna (ignora)
    import pandas as pd
    df = pd.DataFrame([{"Num": "5", "_numero_normalizado": "5", "Tipo": "NF-e", "_valor_float": 1.0, "CFOP": "5102",
                        "Emissor Nome": "F", "Emissor CNPJ/CPF": "1", "_cnpj_normalizado": "1", "UF": "SP", "Chave": None,
                        "_chave_modelo": None, "_chave_serie": None, "_chave_numero": None,
                        "_data_emissao": dt.date(2026, 7, 5), "DtEmi": "2026.07.05"}])
    novo = nf_sienge.gravar_manifesto(conn, "ENERGIA", "07/2026", df, "n.xlsx", "t", lote_id=str(uuid.uuid4()))
    assert novo


def test_gravar_manifesto_com_lote_grava_o_lote(conn):
    import pandas as pd
    lote = str(uuid.uuid4())
    df = pd.DataFrame([{"Num": "5", "_numero_normalizado": "5", "Tipo": "NF-e", "_valor_float": 1.0, "CFOP": "5102",
                        "Emissor Nome": "F", "Emissor CNPJ/CPF": "1", "_cnpj_normalizado": "1", "UF": "SP", "Chave": None,
                        "_chave_modelo": None, "_chave_serie": None, "_chave_numero": None,
                        "_data_emissao": dt.date(2026, 7, 5), "DtEmi": "2026.07.05"}])
    imp = nf_sienge.gravar_manifesto(conn, "ENERGIA", "07/2026", df, "n.xlsx", "t", lote_id=lote)
    with conn.cursor() as cur:
        cur.execute("SELECT lote_id::text FROM egc.nf_manifesto_import WHERE import_id = %s", (imp,))
        assert cur.fetchone()[0] == lote


def test_chat_kpi_bate_com_a_tela_inclui_sienge_sem_nota_e_ignora_arquivado(conn):
    import consultas_chat
    _bill(conn, 1, "10", 100.0, "2026-07-20")
    _bill(conn, 3, "30", 300.0, "2026-07-09")                       # orfao de julho
    certo = _import(conn, "ENERGIA", "07/2026", [("10", 100.0, "2026-07-20"), ("11", 50.0, "2026-07-21")], arquivo="certo.xlsx")
    errado = _import(conn, "ENERGIA", "06/2026", [("77", 7.0, "2026-06-10")], arquivo="errado.xlsx")
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    kpi = consultas_chat.consultar_notas_fiscais_kpi(conn, "ENERGIA")
    tela = {(v["periodo_referencia"]): v for v in nf_sienge.resumo_vigentes(conn, "ENERGIA")}
    assert {r["periodo_referencia"]: r["total_pendencias"] for r in kpi["rodadas"]} == \
        {p: v["total_pendencias"] for p, v in tela.items()}
    jul = next(r for r in kpi["rodadas"] if r["periodo_referencia"] == "07/2026")
    assert (jul["total_notas"], jul["total_lancadas"], jul["notas_pendentes"], jul["sienge_sem_manifesto"],
            jul["total_pendencias"]) == (2, 1, 1, 1, 2)
    nf_sienge.arquivar_envio(conn, [errado], "ana")
    kpi = consultas_chat.consultar_notas_fiscais_kpi(conn, "ENERGIA")
    assert [r["periodo_referencia"] for r in kpi["rodadas"]] == ["07/2026"]
    nf_sienge.arquivar_envio(conn, [certo], "ana")
    assert "erro" in consultas_chat.consultar_notas_fiscais_kpi(conn, "ENERGIA")
