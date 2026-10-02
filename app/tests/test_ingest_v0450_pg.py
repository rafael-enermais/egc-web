# -*- coding: utf-8 -*-
"""v0.45.0 -- o sync diario (ingest_sienge_egc.py) refaz as conferencias vigentes
depois de atualizar o Sienge: nota pendente que a compra lancou vira LANCADA sozinha."""
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import nf_sienge  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]


def _carregar_ingest():
    spec = importlib.util.spec_from_file_location("ingest_sienge_egc", RAIZ / "ingest_sienge_egc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _preparar(c):
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        cur.execute(
            "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
            "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por) "
            "VALUES (%s,'ENERGIA','07/2026','11','11','2026-07-21',50,'5102','07.393.522/0001-40','07393522000140','a.xlsx','t')", (imp,))
    nf_sienge.reconferir_empresa(c, "ENERGIA")
    return imp


def _rodar_ingest(monkeypatch, conn, quebrar_reconferencia=False):
    ing = _carregar_ingest()
    monkeypatch.setenv("SIENGE_BASE_URL", "x"); monkeypatch.setenv("SIENGE_USER", "u")
    monkeypatch.setenv("SIENGE_PASSWORD", "p"); monkeypatch.setenv("DATABASE_URL", "x")

    def _sync(c, *a, **k):
        with c.cursor() as cur:    # "o Sienge ganhou o titulo da nota 11"
            cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591,'CB','07.393.522/0001-40') ON CONFLICT DO NOTHING")
            cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
                        "issue_date, total_invoice_amount) VALUES (1,1,591,'NFE','11','2026-07-21',50)")
        return {"2026-07": 1}
    monkeypatch.setattr(ing.nf_sienge, "sincronizar_bills_por_mes", _sync)
    monkeypatch.setattr(ing.nf_sienge, "sincronizar_creditores", lambda *a, **k: 1)
    if quebrar_reconferencia:
        monkeypatch.setattr(ing.nf_sienge, "reconferir_todas", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    # conexao fechada pelo script: devolve um proxy que ignora close()
    class _Proxy:
        def __init__(self, c): object.__setattr__(self, "_c", c)
        def __getattr__(self, n): return getattr(self._c, n)
        def __setattr__(self, n, v): setattr(self._c, n, v)      # autocommit etc. vao pra conexao real
        def close(self): pass
    monkeypatch.setattr(ing.psycopg2, "connect", lambda url: _Proxy(conn))
    ing.main()


def test_sync_diario_refaz_conferencias_e_a_pendencia_vira_lancada(monkeypatch):
    conn = pg.conectar_limpo()
    try:
        imp = _preparar(conn)
        assert nf_sienge.resumo_vigentes(conn)[0]["total_pendencias"] == 1
        _rodar_ingest(monkeypatch, conn)
        v = nf_sienge.resumo_vigentes(conn)[0]
        assert (v["total_lancadas"], v["total_pendencias"]) == (1, 0)
        with conn.cursor() as cur:
            cur.execute("SELECT mensagem FROM egc.eventos_sistema WHERE origem='notas_fiscais' ORDER BY id DESC LIMIT 5")
            msgs = [r[0] for r in cur.fetchall()]
        assert any("Conferencias refeitas" in m and "1 -> 0" in m for m in msgs), msgs
    finally:
        conn.close()


def test_falha_ao_refazer_nao_desfaz_o_sync_mas_derruba_o_job(monkeypatch):
    conn = pg.conectar_limpo()
    try:
        _preparar(conn)
        with pytest.raises(RuntimeError):
            _rodar_ingest(monkeypatch, conn, quebrar_reconferencia=True)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM egc.nf_bills_sync")
            assert cur.fetchone()[0] == 1                         # o sync ficou gravado
            cur.execute("SELECT nivel FROM egc.eventos_sistema WHERE origem='notas_fiscais' ORDER BY id DESC LIMIT 1")
            assert cur.fetchone()[0] == "ERRO"
    finally:
        conn.close()
