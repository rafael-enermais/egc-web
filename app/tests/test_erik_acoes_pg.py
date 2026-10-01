# -*- coding: utf-8 -*-
"""
Erik.AI contra Postgres de verdade (schema.sql real, bloco 20 incluso):
  - memoria por usuario (janela, isolamento entre usuarios, apagar);
  - propor -> aprovar/rejeitar -> trilha em egc.chat_acao, para os 4 tipos;
  - a proposta NUNCA grava; erros de validacao (nota/conta/periodo inexistente, ambiguidade);
  - busca de nota por numero/fornecedor e registro_id nas pendencias;
  - reversao: arquivar -> recuperar; correcao preserva pdf_original.
"""
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import acoes_chat  # noqa: E402
import chat_memoria  # noqa: E402
import consultas_chat  # noqa: E402
import db  # noqa: E402
import nf_sienge  # noqa: E402

TODAS = pg.TODAS


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _lanc(c, emp, tipo, periodo, conta, valor, grupo="ATIVO CIRCULANTE", gran="trimestral"):
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO egc.lancamentos (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, granularidade) "
            "VALUES (%s,%s,%s,%s,%s,%s,'PDF',%s) RETURNING id", (emp, tipo, periodo, grupo, conta, valor, gran))
        return cur.fetchone()[0]


def _nota(c, empresa="ENERGIA", numero="500", valor=100.0, status="NAO_ENCONTRADA", fornecedor="COSTA BENTO"):
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
            "data_emissao, valor, cfop, fornecedor_nome, fornecedor_cnpj, cnpj_normalizado) "
            "VALUES (%s,%s,'07/2026',%s,%s,'2026-07-10',%s,'5102',%s,'07.393.522/0001-40','07393522000140') RETURNING id",
            (imp, empresa, numero, numero.lstrip("0"), valor, fornecedor))
        mid = cur.fetchone()[0]
        cur.execute("INSERT INTO egc.nf_conciliacao (import_id, manifesto_id, status) VALUES (%s,%s,%s)", (imp, mid, status))
    return mid


# ───────────────────────── memória ─────────────────────────

def test_memoria_janela_isolamento_e_apagar(conn):
    for i in range(30):
        assert chat_memoria.salvar_mensagem(conn, "a@x.com", "user", f"pergunta {i}")
        assert chat_memoria.salvar_mensagem(conn, "a@x.com", "assistant", f"resposta {i}", ["consultar_periodos"])
    chat_memoria.salvar_mensagem(conn, "b@x.com", "user", "outra pessoa")
    h = chat_memoria.carregar_historico(conn, "a@x.com")
    assert len(h) <= chat_memoria.JANELA_HISTORICO_CHAT and h[0]["role"] == "user" and h[-1]["content"] == "resposta 29"
    assert chat_memoria.carregar_historico(conn, "b@x.com") == []  # so' pergunta sem resposta -> nao reabre sozinha
    assert all("outra pessoa" not in m["content"] for m in h)
    assert chat_memoria.disponivel(conn) is True
    assert chat_memoria.apagar_historico(conn, "a@x.com") == 60
    assert chat_memoria.carregar_historico(conn, "a@x.com") == []
    assert len(chat_memoria.carregar_historico(conn, "b@x.com")) == 0
    with conn.cursor() as cur:  # o log da outra pessoa continua la
        cur.execute("SELECT count(*) FROM egc.chat_mensagem WHERE usuario='b@x.com'")
        assert cur.fetchone()[0] == 1


def test_memoria_sem_tabela_nao_quebra(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.chat_mensagem")
    assert chat_memoria.disponivel(conn) is False
    assert chat_memoria.carregar_historico(conn, "a@x.com") == []
    assert chat_memoria.salvar_mensagem(conn, "a@x.com", "user", "oi") is False
    assert chat_memoria.apagar_historico(conn, "a@x.com") == 0


# ───────────────── pendências: propor / aprovar / rejeitar ─────────────────

def test_proposta_nao_grava_e_aprovacao_atualiza_com_log(conn):
    mid = _nota(conn)
    pend = nf_sienge.listar_pendencias_abertas(conn, "ENERGIA")
    assert int(pend.iloc[0]["registro_id"]) == mid and pend.iloc[0]["origem"] == "MANIFESTO"

    r = acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA",
                                   {"origem": "MANIFESTO", "registro_id": mid, "novo_status": "ENVIADO_SUPRIMENTOS"}, TODAS)
    prop = r["proposta"]
    assert dict(prop["resumo"])["Nota"] == "500"
    with conn.cursor() as cur:  # proposta NAO grava
        cur.execute("SELECT pendencia_status FROM egc.nf_conciliacao WHERE manifesto_id=%s", (mid,))
        assert cur.fetchone()[0] in (None, "PENDENTE")
        cur.execute("SELECT count(*) FROM egc.chat_acao")
        assert cur.fetchone()[0] == 0

    ok, msg = acoes_chat.executar_proposta(conn, prop, "u@x.com", TODAS, edicoes={"novo_status": "RESOLVIDO"})  # pessoa ajustou
    assert ok, msg
    with conn.cursor() as cur:
        cur.execute("SELECT pendencia_status FROM egc.nf_conciliacao WHERE manifesto_id=%s", (mid,))
        assert cur.fetchone()[0] == "RESOLVIDO"
        cur.execute("SELECT usuario, tipo, status, parametros->>'novo_status' FROM egc.chat_acao")
        assert cur.fetchall() == [("u@x.com", "ATUALIZAR_STATUS_PENDENCIA", "APROVADA", "RESOLVIDO")]
    assert nf_sienge.listar_pendencias_abertas(conn, "ENERGIA").empty  # saiu das pendencias abertas


def test_rejeicao_registra_e_nao_altera(conn):
    mid = _nota(conn)
    prop = acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA",
                                      {"origem": "MANIFESTO", "registro_id": mid, "novo_status": "DESCARTADO"}, TODAS)["proposta"]
    acoes_chat.registrar_rejeicao(conn, prop, "u@x.com")
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM egc.chat_acao")
        assert cur.fetchall() == [("REJEITADA",)]
        cur.execute("SELECT pendencia_status FROM egc.nf_conciliacao WHERE manifesto_id=%s", (mid,))
        assert cur.fetchone()[0] in (None, "PENDENTE")


def test_validacoes_recusam_registro_inventado_e_parametros_ruins(conn):
    assert "erro" in acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA", {"origem": "MANIFESTO", "registro_id": 99999, "novo_status": "RESOLVIDO"}, TODAS)
    assert "erro" in acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA", {"origem": "XX", "registro_id": 1, "novo_status": "RESOLVIDO"}, TODAS)
    mid = _nota(conn)
    assert "erro" in acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA", {"origem": "MANIFESTO", "registro_id": mid, "novo_status": "APAGAR"}, TODAS)
    assert "erro" in acoes_chat.montar_proposta(conn, "APAGAR_TUDO", {}, TODAS)            # tipo fora da lista
    assert "erro" in acoes_chat.montar_proposta(conn, "ARQUIVAR_PERIODO", {"empresa": "XPTO", "periodo": "2026-06"}, TODAS)
    assert "erro" in acoes_chat.montar_proposta(conn, "ARQUIVAR_PERIODO", {"empresa": "ENERGIA", "periodo": "2026-06"}, TODAS)  # nao existe
    # empresa fora da lista permitida (ex.: usuario so' enxerga algumas)
    assert "erro" in acoes_chat.montar_proposta(conn, "ATUALIZAR_STATUS_PENDENCIA", {"origem": "MANIFESTO", "registro_id": mid, "novo_status": "RESOLVIDO"}, ["SMG"])


# ───────────────── arquivar / recuperar / corrigir ─────────────────

def test_arquivar_e_recuperar_periodo_com_trilha(conn):
    _lanc(conn, "SMG", "BP", date(2026, 6, 30), "CLIENTES", 1000)
    _lanc(conn, "SMG", "DRE", date(2026, 6, 30), "RECEITA OPERACIONAL LIQUIDA", 500, grupo="RESULTADO")
    p = acoes_chat.montar_proposta(conn, "ARQUIVAR_PERIODO", {"empresa": "SMG", "periodo": "2026-06"}, TODAS)["proposta"]
    ok, msg = acoes_chat.executar_proposta(conn, p, "u@x.com", TODAS)
    assert ok and "2 linha" in msg, msg
    assert db.listar_periodos_detalhado(conn, "SMG", status="ATIVO") == []
    p2 = acoes_chat.montar_proposta(conn, "RECUPERAR_PERIODO", {"empresa": "SMG", "periodo": "2026-06"}, TODAS)["proposta"]
    assert acoes_chat.executar_proposta(conn, p2, "u@x.com", TODAS)[0]
    assert len(db.listar_periodos_detalhado(conn, "SMG", status="ATIVO")) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT tipo, status FROM egc.chat_acao ORDER BY id")
        assert cur.fetchall() == [("ARQUIVAR_PERIODO", "APROVADA"), ("RECUPERAR_PERIODO", "APROVADA")]


def test_arquivar_com_duas_bases_exige_granularidade(conn):
    _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "CLIENTES", 1, gran="trimestral")
    _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "CLIENTES", 2, gran="semestral")
    r = acoes_chat.montar_proposta(conn, "ARQUIVAR_PERIODO", {"empresa": "ENERGIA", "periodo": "2026-06"}, TODAS)
    assert "erro" in r and "granularidade" in r["erro"]
    p = acoes_chat.montar_proposta(conn, "ARQUIVAR_PERIODO", {"empresa": "ENERGIA", "periodo": "2026-06", "granularidade": "trimestral"}, TODAS)["proposta"]
    assert acoes_chat.executar_proposta(conn, p, "u", TODAS)[0]
    ativos = db.listar_periodos_detalhado(conn, "ENERGIA", status="ATIVO")
    assert [d["granularidade"] for d in ativos] == ["semestral"]  # so' a base pedida foi arquivada


def test_corrigir_lancamento_preserva_pdf_original_e_aceita_valor_br(conn):
    lid = _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "CLIENTES", 1000)
    _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "ADIANTAMENTOS DE CLIENTES", 5, grupo="PASSIVO CIRCULANTE")
    r = acoes_chat.montar_proposta(conn, "CORRIGIR_LANCAMENTO", {
        "empresa": "ENERGIA", "tipo_demonstracao": "BP", "periodo": "2026-06", "conta": "clientes", "novo_valor": "R$ 1.234,50"}, TODAS)
    p = r["proposta"]
    d = dict(p["resumo"])
    assert d["Valor atual"] == 1000.0 and d["Novo valor"] == 1234.5 and p["parametros"]["lancamento_id"] == lid  # match EXATO: nao pegou ADIANTAMENTOS
    ok, msg = acoes_chat.executar_proposta(conn, p, "u@x.com", TODAS, edicoes={"novo_valor": 1300.0})
    assert ok, msg
    with conn.cursor() as cur:
        cur.execute("SELECT valor, pdf_original, origem FROM egc.lancamentos WHERE id=%s", (lid,))
        valor, original, origem = cur.fetchone()
    assert float(valor) == 1300.0 and float(original) == 1000.0 and origem.startswith("MANUAL")


def test_corrigir_conta_inexistente_ou_duplicada_nao_chuta(conn):
    _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "OUTROS", 1, grupo="ATIVO CIRCULANTE")
    _lanc(conn, "ENERGIA", "BP", date(2026, 6, 30), "OUTROS", 2, grupo="PASSIVO CIRCULANTE")
    base = {"empresa": "ENERGIA", "tipo_demonstracao": "BP", "periodo": "2026-06", "novo_valor": 10}
    assert "mais de um grupo" in acoes_chat.montar_proposta(conn, "CORRIGIR_LANCAMENTO", {**base, "conta": "OUTROS"}, TODAS)["erro"]
    r = acoes_chat.montar_proposta(conn, "CORRIGIR_LANCAMENTO", {**base, "conta": "OUTR"}, TODAS)
    assert "não encontrada" in r["erro"] and "OUTROS" in r["erro"]  # sugere as parecidas, mas NAO escolhe
    assert "erro" in acoes_chat.montar_proposta(conn, "CORRIGIR_LANCAMENTO", {**base, "conta": "OUTROS", "novo_valor": "abc"}, TODAS)


def test_aprovacao_revalida_estado_atual(conn):
    """Entre propor e aprovar o periodo foi arquivado por outra pessoa: nao executa."""
    _lanc(conn, "SMG", "BP", date(2026, 6, 30), "CLIENTES", 1000)
    p = acoes_chat.montar_proposta(conn, "CORRIGIR_LANCAMENTO", {
        "empresa": "SMG", "tipo_demonstracao": "BP", "periodo": "2026-06", "conta": "CLIENTES", "novo_valor": 5}, TODAS)["proposta"]
    db.arquivar_periodo(conn, "SMG", date(2026, 6, 30))
    ok, msg = acoes_chat.executar_proposta(conn, p, "u@x.com", TODAS)
    assert not ok and "Não executei" in msg
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM egc.chat_acao")
        assert cur.fetchall() == [("ERRO",)]


# ───────────────── consultas novas ─────────────────

def test_consultar_nota_fiscal_por_numero_e_fornecedor(conn):
    mid = _nota(conn, numero="000500", fornecedor="COSTA BENTO LTDA")
    r = consultas_chat.consultar_nota_fiscal(conn, numero="500")
    assert r["quantidade"] == 1 and r["notas"][0]["registro_id"] == mid and r["notas"][0]["status"] == "NAO_ENCONTRADA"
    assert consultas_chat.consultar_nota_fiscal(conn, fornecedor="costa bento")["quantidade"] == 1
    assert consultas_chat.consultar_nota_fiscal(conn, cnpj="07.393.522/0001-40")["quantidade"] == 1
    assert consultas_chat.consultar_nota_fiscal(conn, numero="999")["quantidade"] == 0
    assert "erro" in consultas_chat.consultar_nota_fiscal(conn)
    assert consultas_chat.consultar_nota_fiscal(conn, fornecedor="%")["quantidade"] == 0  # curinga puro nao vira "tudo"


def test_consultas_historico_correcoes_relatorios_e_evolucao(conn):
    db.registrar_importacao(conn, "SMG", date(2026, 6, 30), ["a.pdf"], "OK", "BP", "importado", usuario="u@x.com")
    h = consultas_chat.consultar_historico_importacoes(conn, "SMG")
    assert h["quantidade"] == 1 and h["importacoes"][0]["periodo"] == "2026-06-30" and h["importacoes"][0]["usuario"] == "u@x.com"
    lid = _lanc(conn, "SMG", "BP", date(2026, 6, 30), "CLIENTES", 100)
    db.salvar_correcao_manual(conn, lid, 150.0, date(2026, 6, 30), usuario="c@x.com")
    c = consultas_chat.consultar_correcoes_manuais(conn, TODAS)
    assert c["quantidade"] == 1 and c["correcoes"][0]["valor_atual"] == 150.0 and c["correcoes"][0]["valor_original_pdf"] == 100.0
    assert consultas_chat.consultar_relatorios_gerados(conn)["quantidade"] == 0
    db.registrar_relatorio_gerado(conn, ["SMG"], [date(2026, 6, 30)], "rel.pdf", usuario="u@x.com")
    assert consultas_chat.consultar_relatorios_gerados(conn)["quantidade"] == 1


def test_evolucao_indicadores_semeada(conn):
    pg.semear_cenario(conn)
    r = consultas_chat.consultar_evolucao_indicadores(conn, ["ENERGIA"])
    assert "erro" not in r and r["quantidade_periodos"] >= 1
    assert r["evolucao"] == sorted(r["evolucao"], key=lambda l: l["periodo"])
    assert all("EBITDA" in l for l in r["evolucao"])
    so = consultas_chat.consultar_evolucao_indicadores(conn, ["ENERGIA"], None, ["EBITDA"])
    assert set(so["evolucao"][0]) == {"periodo", "EBITDA"}
    assert "erro" in consultas_chat.consultar_evolucao_indicadores(conn, ["ENERGIA"], "mensal")
