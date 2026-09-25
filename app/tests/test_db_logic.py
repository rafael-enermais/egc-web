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
    (empresa, tipo, periodo, grupo, conta, valor, origem, arquivo, usuario,
     periodo_inicio, granularidade) = registros[0]
    assert (empresa, tipo, grupo, conta, valor, origem) == (
        "ENERGIA", "BP", "ATIVO CIRCULANTE", "CLIENTES", 1094484.54, "PDF 31/12/2023"
    )
    # periodo_inicio/granularidade (24/09/2026): opcionais, None quando o
    # chamador nao passa (comportamento antigo preservado).
    assert (periodo_inicio, granularidade) == (None, None)

    cur2 = FakeCursor()
    conn2 = FakeConn(cur2)
    dre_rows = [["RECEITA OPERACIONAL LIQUIDA", "500.000,00", "RECEITAS", "PDF 31/12/2023"]]
    db.inserir_lancamentos(
        conn2, "ENERGIA", "DRE", datetime.date(2023, 12, 31), dre_rows, "arq2.pdf", "maria",
        periodo_inicio=datetime.date(2023, 1, 1), granularidade="anual",
    )
    _, registros2 = cur2.executed[0]
    (empresa, tipo, periodo, grupo, conta, valor, origem, arquivo, usuario,
     periodo_inicio2, granularidade2) = registros2[0]
    assert (grupo, conta, valor) == ("RECEITAS", "RECEITA OPERACIONAL LIQUIDA", 500000.00)
    assert (periodo_inicio2, granularidade2) == (datetime.date(2023, 1, 1), "anual")
    print("OK: inserir_lancamentos (BP e DRE com ordem de coluna correta, periodo_inicio/granularidade opcionais)")


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


def test_listar_lancamentos_grupo_periodos_usa_any_pra_lista_de_periodos():
    cols = ["empresa_codigo", "periodo", "grupo", "conta", "valor"]
    linhas = [
        ("ENERGIA", datetime.date(2026, 5, 31), "ATIVO", "TOTAL DO ATIVO", 1000000.00),
        ("ENERGIA", datetime.date(2026, 6, 30), "ATIVO", "TOTAL DO ATIVO", 1100000.00),
    ]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    periodos = [datetime.date(2026, 5, 31), datetime.date(2026, 6, 30)]
    lancs = db.listar_lancamentos_grupo_periodos(conn, periodos, "BP", ["ENERGIA"], status="ATIVO")
    sql, params = cur.executed[0]
    assert "periodo = ANY(%s)" in sql
    assert "empresa_codigo = ANY(%s)" in sql
    assert "ORDER BY periodo, grupo, conta" in sql
    assert params == (periodos, "BP", "ATIVO", ["ENERGIA"])
    assert len(lancs) == 2
    assert lancs[0] == {
        "empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 5, 31),
        "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": 1000000.00,
    }
    print("OK: listar_lancamentos_grupo_periodos — usa ANY(%s) pra lista de períodos, cada linha com o campo periodo")


def test_listar_historico_grupo_todos_periodos_sem_filtro_de_periodo():
    cols = ["periodo", "grupo", "conta", "valor"]
    linhas = [
        (datetime.date(2026, 5, 31), "ATIVO CIRCULANTE", "CLIENTES", 100.0),
        (datetime.date(2026, 6, 30), "ATIVO CIRCULANTE", "CLIENTES", 200.0),
    ]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    hist = db.listar_historico_grupo(conn, "SMG", "BP", status="ATIVO")
    sql, params = cur.executed[0]
    assert "FROM egc.lancamentos" in sql
    assert "ORDER BY periodo, grupo, conta" in sql
    assert params == ("SMG", "BP", "ATIVO")  # sem filtro de periodo especifico -- so empresa/tipo/status
    assert len(hist) == 2
    print("OK: listar_historico_grupo")


def test_salvar_ajuste_projecao_grava_e_retorna_id():
    cur = FakeCursor(fetchone_result=(7,))
    conn = FakeConn(cur)
    novo_id = db.salvar_ajuste_projecao(
        conn, "CONST", "DRE", datetime.date(2026, 12, 31), "RECEITAS", "RECEITA OPERACIONAL BRUTA",
        500000.0, "Contrato X fechando", "rafael",
    )
    sql, params = cur.executed[0]
    assert "INSERT INTO egc.projecoes_ajustes" in sql
    assert novo_id == 7
    assert params == ("CONST", "DRE", datetime.date(2026, 12, 31), "RECEITAS", "RECEITA OPERACIONAL BRUTA", 500000.0, "Contrato X fechando", "rafael")
    print("OK: salvar_ajuste_projecao")


def test_inativar_ajuste_projecao_so_muda_status_nunca_apaga():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.inativar_ajuste_projecao(conn, 7)
    sql, params = cur.executed[0]
    assert "DELETE" not in sql.upper()
    assert "SET status = 'INATIVO'" in sql
    assert params == (7,)
    print("OK: inativar_ajuste_projecao (so status, sem DELETE)")


def test_gravar_projecoes_upsert_em_lote_com_on_conflict():
    cur = FakeCursor()
    conn = FakeConn(cur)
    linhas = [{
        "empresa_codigo": "CONST", "tipo": "DRE", "periodo": datetime.date(2026, 7, 31),
        "grupo": "RECEITAS", "conta": "RECEITA OPERACIONAL BRUTA",
        "valor_base": 1000.0, "valor_ajuste": 500.0, "valor_projetado": 1500.0,
        "metodo": "flat_ultimo_valor", "periodos_historico": 1,
    }]
    n = db.gravar_projecoes(conn, linhas)
    sql, registros = cur.executed[0]
    assert "ON CONFLICT (empresa_codigo, tipo, periodo, grupo, conta) DO UPDATE" in sql
    assert registros[0] == ("CONST", "DRE", datetime.date(2026, 7, 31), "RECEITAS", "RECEITA OPERACIONAL BRUTA", 1000.0, 500.0, 1500.0, "flat_ultimo_valor", 1)
    print("OK: gravar_projecoes (upsert em lote)")


def test_gravar_projecoes_lista_vazia_nao_executa_sql():
    cur = FakeCursor()
    conn = FakeConn(cur)
    n = db.gravar_projecoes(conn, [])
    assert n == 0
    assert cur.executed == []
    print("OK: gravar_projecoes — lista vazia nao chama o banco")


def test_listar_projecoes_le_materializado_sem_recalcular():
    cols = ["periodo", "grupo", "conta", "valor_base", "valor_ajuste", "valor_projetado", "metodo", "periodos_historico", "gerado_em"]
    linhas = [(datetime.date(2026, 7, 31), "RECEITAS", "RECEITA OPERACIONAL BRUTA", 1000.0, 500.0, 1500.0, "flat_ultimo_valor", 1, datetime.datetime(2026, 9, 22, 10, 0))]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    projecoes = db.listar_projecoes(conn, "CONST", "DRE")
    sql, params = cur.executed[0]
    assert "FROM egc.projecoes" in sql
    assert params == ("CONST", "DRE")
    assert projecoes[0]["valor_projetado"] == 1500.0
    print("OK: listar_projecoes")


# ─────────────────────────────────────────────
#  EVENTOS DO SISTEMA (log completo — task #16, 22/09/2026)
# ─────────────────────────────────────────────

def test_registrar_evento_grava_todos_os_campos():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.registrar_evento(
        conn, "revisao_correcao", "ERRO", "Falha ao salvar correção",
        empresa_codigo="SMG", periodo=datetime.date(2026, 6, 30), usuario="teste@enermais.com.br",
        detalhe="ConnectionError: timeout",
    )
    sql, params = cur.executed[0]
    assert "INSERT INTO egc.eventos_sistema" in sql
    assert params == ("revisao_correcao", "ERRO", "Falha ao salvar correção", "ConnectionError: timeout",
                       "SMG", datetime.date(2026, 6, 30), "teste@enermais.com.br")
    print("OK: registrar_evento — grava origem/nivel/mensagem/detalhe/empresa/periodo/usuario")


def test_registrar_evento_campos_opcionais_default_none():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.registrar_evento(conn, "chat", "INFO", "teste sem opcionais")
    _, params = cur.executed[0]
    assert params == ("chat", "INFO", "teste sem opcionais", None, None, None, None)
    print("OK: registrar_evento — sem empresa/periodo/usuario/detalhe, grava None (nao quebra)")


def test_listar_eventos_recentes_sem_filtro():
    cols = ["id", "origem", "nivel", "mensagem", "detalhe", "empresa_codigo", "periodo", "usuario", "criado_em"]
    linhas = [(1, "chat", "ERRO", "falha na API", "timeout", None, None, "a@b.com", datetime.datetime(2026, 9, 22, 10, 0))]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    eventos = db.listar_eventos_recentes(conn)
    sql, params = cur.executed[0]
    assert "FROM egc.eventos_sistema" in sql
    assert "WHERE" not in sql  # sem filtro nenhum, so' ORDER BY + LIMIT
    assert params == [100]
    assert eventos[0]["origem"] == "chat"
    print("OK: listar_eventos_recentes — sem filtro, limite default 100")


def test_listar_eventos_recentes_filtra_nivel_e_origem():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.listar_eventos_recentes(conn, limite=10, nivel="ERRO", origem="arquivar_recuperar")
    sql, params = cur.executed[0]
    assert "nivel = %s" in sql and "origem = %s" in sql
    assert params == ["ERRO", "arquivar_recuperar", 10]
    print("OK: listar_eventos_recentes — filtro por nivel + origem monta WHERE certo")


def test_buscar_lancamentos_manuais_sem_filtro_de_empresa():
    cols = ["id", "empresa_codigo", "tipo", "periodo", "grupo", "conta", "valor", "pdf_original",
            "origem", "usuario", "atualizado_em"]
    linhas = [(1, "ENERGIA", "BP", datetime.date(2026, 8, 1), "ATIVO", "CLIENTES", 5000.0, 4500.0,
               "MANUAL 08/2026", "a@b.com", datetime.datetime(2026, 9, 22, 15, 0))]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    resultado = db.buscar_lancamentos_manuais(conn)
    sql, params = cur.executed[0]
    assert "origem LIKE 'MANUAL%" in sql
    assert "empresa_codigo = ANY" not in sql  # sem filtro, busca nas 6 empresas juntas
    assert "ORDER BY atualizado_em DESC" in sql
    assert params == [50]
    assert resultado[0]["empresa_codigo"] == "ENERGIA"
    assert resultado[0]["origem"] == "MANUAL 08/2026"
    print("OK: buscar_lancamentos_manuais — sem filtro de empresa, WHERE so' origem LIKE MANUAL%")


def test_buscar_lancamentos_manuais_filtra_empresas_e_limite():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.buscar_lancamentos_manuais(conn, empresas_codigos=["ENERGIA", "SMG"], limite=10)
    sql, params = cur.executed[0]
    assert "empresa_codigo = ANY(%s)" in sql
    assert params == [["ENERGIA", "SMG"], 10]
    print("OK: buscar_lancamentos_manuais — filtro por empresas + limite customizado")


# ─────────────────────────────────────────────
#  CONTEXTO FISCAL (Reforma Tributaria — 22/09/2026)
# ─────────────────────────────────────────────

def test_listar_contexto_fiscal_sem_tema_traz_so_ativos():
    cols = ["chave", "tema", "titulo", "conteudo", "fonte", "atualizado_em"]
    linhas = [("REFORMA_TRIB_CRONOGRAMA", "reforma_tributaria", "Cronograma", "texto...", "fonte X",
               datetime.datetime(2026, 9, 22, 10, 0))]
    cur = FakeCursor(fetchall_result=linhas, description=[(c,) for c in cols])
    conn = FakeConn(cur)
    linhas_ret = db.listar_contexto_fiscal(conn)
    sql, params = cur.executed[0]
    assert "FROM egc.contexto_fiscal" in sql
    assert "ativo = true" in sql
    assert params is None
    assert linhas_ret[0]["titulo"] == "Cronograma"
    print("OK: listar_contexto_fiscal — sem tema, so' ativos, ordenado")


def test_listar_contexto_fiscal_filtra_por_tema():
    cur = FakeCursor()
    conn = FakeConn(cur)
    db.listar_contexto_fiscal(conn, tema="reforma_tributaria")
    sql, params = cur.executed[0]
    assert "tema = %s" in sql
    assert params == ("reforma_tributaria",)
    print("OK: listar_contexto_fiscal — filtro por tema")


def test_salvar_despesas_admin_itens_delete_e_insere_na_ordem():
    cur = FakeCursor()
    conn = FakeConn(cur)
    itens = [("Serviços Profissionais", -1602399.52), ("Salários e Ordenados", -544822.11)]
    n = db.salvar_despesas_admin_itens(conn, "CONST", datetime.date(2026, 6, 30), itens, "arquivo.pdf")
    assert n == 2
    sql_delete, params_delete = cur.executed[0]
    assert "DELETE FROM egc.despesas_admin_itens" in sql_delete
    assert params_delete == ("CONST", datetime.date(2026, 6, 30))
    sql_insert, registros = cur.executed[1]
    assert "INSERT INTO egc.despesas_admin_itens" in sql_insert
    assert registros == [
        ("CONST", datetime.date(2026, 6, 30), 0, "Serviços Profissionais", -1602399.52, "arquivo.pdf"),
        ("CONST", datetime.date(2026, 6, 30), 1, "Salários e Ordenados", -544822.11, "arquivo.pdf"),
    ]
    print("OK: salvar_despesas_admin_itens — delete+insert na ordem")


def test_salvar_despesas_admin_itens_lista_vazia_so_deleta():
    cur = FakeCursor()
    conn = FakeConn(cur)
    n = db.salvar_despesas_admin_itens(conn, "CONST", datetime.date(2026, 6, 30), [])
    assert n == 0
    assert len(cur.executed) == 1  # so o DELETE, nenhum INSERT
    print("OK: salvar_despesas_admin_itens — lista vazia nao insere nada")


def test_listar_despesas_admin_itens_retorna_na_ordem_gravada():
    cur = FakeCursor(fetchall_result=[("Serviços Profissionais", 1602399.52), ("Salários e Ordenados", 544822.11)])
    conn = FakeConn(cur)
    itens = db.listar_despesas_admin_itens(conn, "CONST", datetime.date(2026, 6, 30))
    assert itens == [("Serviços Profissionais", 1602399.52), ("Salários e Ordenados", 544822.11)]
    sql, params = cur.executed[0]
    assert "SELECT conta, valor FROM egc.despesas_admin_itens" in sql
    assert "ORDER BY ordem" in sql
    assert params == ("CONST", datetime.date(2026, 6, 30))
    print("OK: listar_despesas_admin_itens — ordem preservada")


class _FakeCursorTabelaInexistente(FakeCursor):
    """Simula a tabela do bloco 12 ainda nao existir no banco (migracao
    pendente) -- salvar/listar devem degradar (0/[]), nunca quebrar."""

    def execute(self, sql, params=None):
        raise db.psycopg2.errors.UndefinedTable("relation \"egc.despesas_admin_itens\" does not exist")


class _FakeConnComRollback(FakeConn):
    def __init__(self, cursor):
        super().__init__(cursor)
        self.rollback_chamado = False

    def rollback(self):
        self.rollback_chamado = True


def test_salvar_despesas_admin_itens_tabela_ainda_nao_existe_nao_quebra():
    conn = _FakeConnComRollback(_FakeCursorTabelaInexistente())
    n = db.salvar_despesas_admin_itens(conn, "CONST", datetime.date(2026, 6, 30), [("X", -1.0)])
    assert n == 0
    assert conn.rollback_chamado
    print("OK: salvar_despesas_admin_itens — tabela ausente nao quebra a importacao")


def test_listar_despesas_admin_itens_tabela_ainda_nao_existe_retorna_vazio():
    conn = _FakeConnComRollback(_FakeCursorTabelaInexistente())
    itens = db.listar_despesas_admin_itens(conn, "CONST", datetime.date(2026, 6, 30))
    assert itens == []
    assert conn.rollback_chamado
    print("OK: listar_despesas_admin_itens — tabela ausente retorna vazio")


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
