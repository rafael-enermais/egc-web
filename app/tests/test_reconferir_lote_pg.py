# -*- coding: utf-8 -*-
"""v0.47.0: a conferencia grava em LOTE. Antes eram ~3 idas ao banco por nota (SAVEPOINT + INSERT + RELEASE) e 1 por
titulo orfao -- com o Supabase a ~100 ms de distancia, "Atualizar agora" levava varios minutos. Este teste trava a
contagem de idas ao banco (nao depende da velocidade da maquina) e o resultado da conferencia."""
import sys
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import nf_sienge  # noqa: E402

N_MESES, POR_MES = 3, 60


def _cnpj(f):
    return f"{10000000000000 + f:014d}"


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    nf_sienge.salvar_mapa_debtor(c, 5, "CONST", "t")
    with c.cursor() as cur:
        for f in range(20):
            cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (%s, %s, %s)",
                        (f, f"FORN {f}", _cnpj(f)))
        bid = 1
        for m in range(1, N_MESES + 1):
            for k in range(POR_MES):
                if k % 3 == 2:
                    continue                                    # 1/3 nao lancada
                deb = 5 if k % 10 == 0 else 1                   # algumas lancadas na empresa errada
                cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, "
                            "document_number, issue_date, total_invoice_amount) VALUES (%s,%s,%s,'NFE ',%s,%s,%s)",
                            (bid, deb, k % 20, str(m * 1000 + k), dt.date(2026, m, 1 + k % 27), 100.0 + k)); bid += 1
            for j in range(5):                                  # titulos que o manifesto nao tem
                cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, "
                            "document_number, issue_date, total_invoice_amount) VALUES (%s,1,%s,'NFE ',%s,%s,7)",
                            (bid, j, str(900000 + bid), dt.date(2026, m, 5))); bid += 1
    for m in range(1, N_MESES + 1):
        df = pd.DataFrame([dict(Num=str(m * 1000 + k), _numero_normalizado=str(m * 1000 + k), Tipo="NF-e",
                                _data_emissao=dt.date(2026, m, 1 + k % 27), _valor_float=100.0 + k, CFOP="5102",
                                **{"Emissor Nome": f"FORN {k % 20}", "Emissor CNPJ/CPF": _cnpj(k % 20)},
                                _cnpj_normalizado=_cnpj(k % 20), UF="SP", Chave=None) for k in range(POR_MES)])
        nf_sienge.gravar_manifesto(c, "ENERGIA", f"{m:02d}/2026", df, "x.xlsx", "t")
    yield c
    c.close()


class _Cur:
    def __init__(self, c, cont): self.c, self.cont = c, cont
    def execute(self, *a, **k): self.cont[0] += 1; return self.c.execute(*a, **k)
    def __getattr__(self, n): return getattr(self.c, n)
    def __enter__(self): self.c.__enter__(); return self
    def __exit__(self, *a): return self.c.__exit__(*a)


class _Conn:
    def __init__(self, c): object.__setattr__(self, "c", c); object.__setattr__(self, "cont", [0])
    def cursor(self, *a, **k): return _Cur(self.c.cursor(*a, **k), self.cont)
    def __getattr__(self, n): return getattr(self.c, n)
    def __setattr__(self, n, v): setattr(self.c, n, v)


def test_reconferencia_grava_em_lote_e_o_resultado_bate(conn):
    p = _Conn(conn)
    res = nf_sienge.reconferir_empresa(p, "ENERGIA")
    total_notas = N_MESES * POR_MES
    # antes: >= 3 idas por nota + 1 por orfao (> 540 aqui); agora: dezenas, e NAO cresce com o numero de notas
    assert p.cont[0] < 120, p.cont[0]
    assert sum(r["total"] for r in res.values()) == total_notas
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM egc.nf_conciliacao"); assert cur.fetchone()[0] == total_notas
        cur.execute("SELECT status, COUNT(*) FROM egc.nf_conciliacao GROUP BY 1")
        por = dict(cur.fetchall())
        cur.execute("SELECT COUNT(*) FROM egc.nf_bills_orfaos"); orf = cur.fetchone()[0]
    assert set(por) >= {"LANCADA", "NAO_ENCONTRADA", "LANCADA_OUTRA_EMPRESA"}
    assert sum(r["lancadas"] for r in res.values()) == por["LANCADA"]
    assert sum(r["orfaos_sienge"] for r in res.values()) == orf and orf > 0
    # idempotente: rodar de novo nao duplica nada
    nf_sienge.reconferir_empresa(conn, "ENERGIA")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM egc.nf_conciliacao"); assert cur.fetchone()[0] == total_notas
        cur.execute("SELECT COUNT(*) FROM egc.nf_bills_orfaos"); assert cur.fetchone()[0] == orf


def test_banco_sem_bloco_19_continua_gravando_nota_a_nota(conn):
    """CHECK de status antigo (sem LANCADA_OUTRA_EMPRESA): a conferencia nao quebra -- grava como LANCADA com o alerta."""
    with conn.cursor() as cur:
        cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'egc.nf_conciliacao'::regclass AND contype = 'c' "
                    "AND pg_get_constraintdef(oid) ILIKE '%LANCADA_OUTRA_EMPRESA%'")
        nomes = [r[0] for r in cur.fetchall()]
        for n in nomes:
            cur.execute(f'ALTER TABLE egc.nf_conciliacao DROP CONSTRAINT "{n}"')
        cur.execute("ALTER TABLE egc.nf_conciliacao ADD CONSTRAINT ck_antigo CHECK "
                    "(status IN ('LANCADA','NAO_ENCONTRADA','VALOR_DIVERGENTE','NUMERO_DIVERGENTE'))")
    res = nf_sienge.reconferir_empresa(conn, "ENERGIA")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM egc.nf_conciliacao"); assert cur.fetchone()[0] == N_MESES * POR_MES
        cur.execute("SELECT COUNT(*) FROM egc.nf_conciliacao WHERE status = 'LANCADA_OUTRA_EMPRESA'")
        assert cur.fetchone()[0] == 0
    for r in res.values():
        assert r["outra_empresa"] == 0 and r["lancadas"] + r["pendencias"] == r["total"]
