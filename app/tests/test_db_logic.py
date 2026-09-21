# -*- coding: utf-8 -*-
"""
Testes de logica de app/db.py SEM banco real — o schema.sql ainda nao foi
aplicado no Supabase (pendencia aberta), entao aqui validamos que as
funcoes montam o SQL/parametros certos e respeitam as regras de negocio
(trilha de auditoria, arquivar/recuperar so muda status, etc.), usando um
mock de conexao/cursor no padrao DB-API 2.0 (mesma interface que
psycopg2 expoe). Quando o banco real estiver disponivel, um teste de
integracao separado pode rodar as mesmas funcoes contra ele.
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402


class FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None, description=None):
        self.executed = []
        self.rowcount = 0
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result
        self.description = description or []

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))
        self.rowcount = len(self._fetchall_result) if self._fetchall_result else 1

    def executemany(self, sql, seq_params):
        self.executed.append((sql.strip(), list(seq_params)))
        self.rowcount = len(seq_params)

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_inativar_periodo_existente_status_e_condicoes():
    cur = FakeCursor()
    conn = FakeConn(cur)
    n = db.inativar_periodo_existente(conn, "ENERGIA", datetime.date(2023, 12, 31), "BP")
    sql, params = cur.executed[0]
    assert "UPDATE egc.lancamentos" in sql
    assert "SET status = 'INATIVO'" in sql
    assert "status = 'ATIVO'" in sql  # so mexe no que esta ativo
    assert params == ("ENERGIA", datetime.date(2023, 12, 31), "BP")
    print("OK: inativar_periodo_existente")


def test_inserir_lancamentos_bp_e_dre_ordem_de_colunas():
    # BP: [grupo, conta, valor_br, origem]  |  DRE: [conta, valor_br, grupo, origem]
    cur = FakeCursor()
    conn = FakeConn(cur)

    bp_rows = [["ATIVO CIRCULANTE", "CLIENTES", "1.094.484,54", "PDF 31/12/2023"]]
    n = db.inserir_lancamentos(conn, "ENERGIA", "BP", datetime.date(2023, 12, 31), bp_rows, "arq.pdf", "maria")
    sql, registros = cur.executed[0]
    assert n == 1
    empresa, tipo, periodo, grupo, conta, valor, origem, arquivo, usuario = registros[0]
    assert (empresa, tipo, grupo, conta, valor, origem) == (
        "ENERGIA", "BP", "ATIVO CIRCULANTE", "CLIENTES", 1094484.54, "PDF 31/12/2023"
    )

    cur2 = FakeCursor()
    conn2 = FakeConn(cur2)
    dre_rows = [["RECEITA OPERACIONAL LIQUIDA", "500.000,00", "RECEITAS", "PDF 31/12/2023"]]
    db.inserir_lancamentos(conn2, "ENERGIA", "DRE", datetime.date(2023, 12, 31), dre_rows, "arq2.pdf", "maria")
    _, registros2 = cur2.executed[0]
    empresa, tipo, periodo, grupo, conta, valor, origem, arquivo, usuario = registros2[0]
    assert (grupo, conta, valor) == ("RECEITAS", "RECEITA OPERACIONAL LIQUIDA", 500000.00)
    print("OK: inserir_lancamentos (BP e DRE com ordem de coluna correta)")


def test_salvar_correcao_manual_preserva_pdf_original_so_na_1a_edicao():
    cur = FakeCursor(fetchone_result=(42,))
    conn = FakeConn(cur)
    novo_id = db.salvar_correcao_manual(conn, 42, 999.99, datetime.date(2023, 12, 31), "maria")
    sql, params = cur.executed[0]
    assert "COALESCE(pdf_original, valor)" in sql  # nunca sobrescreve se ja tinha
    assert "origem = %s" in sql
    assert novo_id == 42
    valor, origem, usuario, lancamento_id = params
    assert valor == 999.99
    assert origem == "MANUAL 12/2023"
    print("OK: salvar_correcao_manual (update, pdf_original protegido)")


def test_salvar_correcao_manual_cria_linha_ajuste_manual_quando_conta_nao_existia():
    cur = FakeCursor(fetchone_result=(77,))
    conn = FakeConn(cur)
    novo_id = db.salvar_correcao_manual(
        conn, None, 123.45, datetime.date(2023, 12, 31), "maria",
        empresa_codigo="ENERGIA", tipo="BP", conta="CONTA NOVA",
    )
    sql, params = cur.executed[0]
    assert "AJUSTE MANUAL" in sql
    assert "INSERT INTO egc.lancamentos" in sql
    assert novo_id == 77
    print("OK: salvar_correcao_manual (insert, grupo AJUSTE MANUAL)")


def test_arquivar_e_recuperar_so_alternam_status_nunca_apagam():
    cur_arq = FakeCursor()
    db.arquivar_periodo(FakeConn(cur_arq), "ENERGIA", datetime.date(2023, 12, 31))
    sql_arq, _ = cur_arq.executed[0]
    assert "DELETE" not in sql_arq.upper()
    assert "SET status = 'INATIVO'" in sql_arq
    assert "status = 'ATIVO'" in sql_arq

    cur_rec = FakeCursor()
    db.recuperar_periodo(FakeConn(cur_rec), "ENERGIA", datetime.date(2023, 12, 31))
    sql_rec, _ = cur_rec.executed[0]
    assert "DELETE" not in sql_rec.upper()
    assert "SET status = 'ATIVO'" in sql_rec
    assert "status = 'INATIVO'" in sql_rec
    print("OK: arquivar_periodo/recuperar_periodo (so status, sem DELETE)")


def test_listar_periodos_filtra_por_status():
    cur = FakeCursor(fetchall_result=[(datetime.date(2023, 12, 31),)])
    conn = FakeConn(cur)
    periodos = db.listar_periodos(conn, "ENERGIA", status="INATIVO")
    sql, params = cur.executed[0]
    assert params == ("ENERGIA", "INATIVO")
    assert periodos == [datetime.date(2023, 12, 31)]
    print("OK: listar_periodos")


def test_listar_importacoes_recentes_monta_sql_e_dict():
    cols = ["empresa_codigo", "periodo", "criado_em", "usuario", "tipo", "nivel", "mensagem"]
    linhas = [
        ("ENERGIA", datetime.date(2026, 6, 30), datetime.datetime(2026, 9, 21, 10, 0), "maria", "BP", "OK", "5 conta(s) gravada(s)"),
        ("SMG", datetime.date(2026, 6, 30), datetime.datetime(2026, 9, 21, 9, 0), "maria", "DRE", "OK", "3 conta(s) gravada(s)"),
    ]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    eventos = db.listar_importacoes_recentes(conn, limite=50)
    sql, params = cur.executed[0]
    assert "FROM egc.importacoes" in sql
    assert "ORDER BY criado_em DESC" in sql
    assert params == (50,)
    assert len(eventos) == 2
    assert eventos[0]["empresa_codigo"] == "ENERGIA"
    assert eventos[0]["periodo"] == datetime.date(2026, 6, 30)
    assert eventos[1]["empresa_codigo"] == "SMG"
    print("OK: listar_importacoes_recentes")


def test_listar_periodos_grupo_usa_any_e_status():
    cur = FakeCursor(fetchall_result=[(datetime.date(2026, 6, 30),), (datetime.date(2026, 5, 31),)])
    conn = FakeConn(cur)
    periodos = db.listar_periodos_grupo(conn, ["ENERGIA", "SMG"], status="ATIVO")
    sql, params = cur.executed[0]
    assert "empresa_codigo = ANY(%s)" in sql
    assert "status = %s" in sql
    assert params == (["ENERGIA", "SMG"], "ATIVO")
    assert periodos == [datetime.date(2026, 6, 30), datetime.date(2026, 5, 31)]
    print("OK: listar_periodos_grupo")


def test_listar_lancamentos_grupo_monta_sql_filtros_e_ordem():
    cols = ["empresa_codigo", "grupo", "conta", "valor"]
    linhas = [
        ("SMG", "ATIVO CIRCULANTE", "CLIENTES", 121755.76),
        ("CONST", "ATIVO CIRCULANTE", "CLIENTES", 50000.00),
    ]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    lancs = db.listar_lancamentos_grupo(
        conn, datetime.date(2026, 6, 30), "BP", ["SMG", "CONST"], status="ATIVO"
    )
    sql, params = cur.executed[0]
    assert "empresa_codigo = ANY(%s)" in sql
    assert "ORDER BY grupo, conta" in sql
    assert params == (datetime.date(2026, 6, 30), "BP", "ATIVO", ["SMG", "CONST"])
    assert len(lancs) == 2
    assert lancs[0] == {
        "empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": 121755.76
    }
    print("OK: listar_lancamentos_grupo")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
