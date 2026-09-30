# -*- coding: utf-8 -*-
"""db.salvar/listar_despesas_admin_itens com granularidade (bloco 19 do
schema.sql, 30/09/2026): o documento e' (empresa, periodo_fim,
granularidade) -- reimportar o semestral nao pode apagar o ranking do
trimestral do mesmo periodo_fim."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
from test_db_logic import FakeConn, FakeCursor  # noqa: E402

P = datetime.date(2026, 6, 30)


def test_salvar_com_granularidade_deleta_e_insere_so_do_documento():
    cur = FakeCursor()
    n = db.salvar_despesas_admin_itens(FakeConn(cur), "ENERGIA", P, [("A", -1.0), ("B", -2.0)], "x.pdf", granularidade="trimestral")
    assert n == 2
    sql_del, p_del = cur.executed[0]
    assert "COALESCE(granularidade, '') = %s" in sql_del and p_del == ("ENERGIA", P, "trimestral")
    sql_ins, registros = cur.executed[1]
    assert "granularidade" in sql_ins
    assert registros[0] == ("ENERGIA", P, 0, "A", -1.0, "x.pdf", "trimestral")


def test_salvar_sem_granularidade_mantem_comportamento_antigo():
    cur = FakeCursor()
    db.salvar_despesas_admin_itens(FakeConn(cur), "ENERGIA", P, [("A", -1.0)], "x.pdf")
    assert cur.executed[0][1] == ("ENERGIA", P)
    assert "granularidade" not in cur.executed[1][0]


def test_listar_com_granularidade_filtra_pelo_documento():
    cur = FakeCursor(fetchall_result=[("A", 1.0)])
    assert db.listar_despesas_admin_itens(FakeConn(cur), "ENERGIA", P, granularidade="semestral") == [("A", 1.0)]
    sql, params = cur.executed[0]
    assert "COALESCE(granularidade, '') = %s" in sql and params == ("ENERGIA", P, "semestral")
    # granularidade "" (nao declarada) tambem filtra (nao vira "sem filtro")
    cur2 = FakeCursor(fetchall_result=[])
    db.listar_despesas_admin_itens(FakeConn(cur2), "ENERGIA", P, granularidade="")
    assert cur2.executed[0][1] == ("ENERGIA", P, "")


class _CursorSemColuna(FakeCursor):
    """Banco onde o bloco 19 ainda nao rodou: qualquer SQL que cite a coluna falha."""

    def execute(self, sql, params=None):
        if "granularidade" in sql:
            raise db.psycopg2.errors.UndefinedColumn('column "granularidade" does not exist')
        super().execute(sql, params)

    def executemany(self, sql, seq):
        if "granularidade" in sql:
            raise db.psycopg2.errors.UndefinedColumn('column "granularidade" does not exist')
        super().executemany(sql, seq)


class _ConnRollback(FakeConn):
    def rollback(self):
        pass


def test_sem_a_coluna_ainda_cai_no_comportamento_antigo_sem_quebrar():
    cur = _CursorSemColuna(fetchall_result=[("A", 1.0)])
    conn = _ConnRollback(cur)
    assert db.listar_despesas_admin_itens(conn, "ENERGIA", P, granularidade="trimestral") == [("A", 1.0)]
    n = db.salvar_despesas_admin_itens(conn, "ENERGIA", P, [("A", -1.0)], "x.pdf", granularidade="trimestral")
    assert n == 1
    assert any("DELETE FROM egc.despesas_admin_itens" in sql and "granularidade" not in sql for sql, _ in cur.executed)
