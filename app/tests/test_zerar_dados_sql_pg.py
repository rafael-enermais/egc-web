# -*- coding: utf-8 -*-
"""scripts/zerar_dados_teste.sql contra Postgres real: apaga o que promete,
preserva a configuracao, reinicia ids, e cobre TODA tabela do schema.sql
(se alguem criar tabela nova e esquecer de classificar, este teste avisa)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

RAIZ = Path(__file__).resolve().parent.parent.parent
SQL = (RAIZ / "scripts" / "zerar_dados_teste.sql").read_text(encoding="utf-8")
APAGADAS = {"lancamentos", "importacoes", "despesas_admin_itens", "relatorios_gerados", "projecoes",
            "projecoes_ajustes", "eventos_sistema", "nf_manifesto_import", "nf_conciliacao",
            "nf_import_historico", "nf_bills_orfaos", "nf_manifesto_ignoradas", "chat_mensagem", "chat_acao"}
MANTIDAS = {"empresas", "textos_relatorio", "config_relatorio", "contexto_fiscal", "contatos_relatorio",
            "nf_debtor_empresa", "nf_bills_sync", "nf_creditors_sync"}


def test_toda_tabela_do_schema_esta_classificada():
    tabelas = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?egc\.(\w+)", (RAIZ / "schema.sql").read_text(encoding="utf-8")))
    assert tabelas == APAGADAS | MANTIDAS, f"tabela nao classificada: {tabelas ^ (APAGADAS | MANTIDAS)}"


def test_script_apaga_o_que_promete_e_preserva_config():
    c = pg.conectar_limpo()
    pg.semear(c) if hasattr(pg, "semear") else None
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.lancamentos (empresa_codigo,tipo,periodo,grupo,conta,valor,origem,granularidade) "
                    "VALUES ('ENERGIA','DRE','2026-06-30','G','C',1,'PDF','trimestral')")
        cur.execute("INSERT INTO egc.importacoes (empresa_codigo,periodo,arquivos,nivel,tipo,mensagem) "
                    "VALUES ('ENERGIA','2026-06-30',ARRAY['a.pdf'],'OK','DRE','x')")
        cur.execute("INSERT INTO egc.chat_mensagem (usuario,papel,conteudo) VALUES ('u','user','oi')")
        cur.execute("INSERT INTO egc.chat_acao (usuario,tipo,parametros,status) VALUES ('u','X','{}','APROVADA')")
        cur.execute("INSERT INTO egc.eventos_sistema (origem,nivel,mensagem) VALUES ('t','INFO','m')")
        cur.execute("INSERT INTO egc.nf_debtor_empresa (debtor_id,empresa_codigo) VALUES (1,'ENERGIA')")
        cur.execute("SELECT count(*) FROM egc.nf_debtor_empresa"); n_deb = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM egc.textos_relatorio"); n_textos = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM egc.config_relatorio"); n_cfg = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM egc.empresas"); n_emp = cur.fetchone()[0]
        cur.execute(SQL)
        for t in APAGADAS:
            cur.execute(f"SELECT count(*) FROM egc.{t}")
            assert cur.fetchone()[0] == 0, t
        cur.execute("SELECT count(*) FROM egc.textos_relatorio"); assert cur.fetchone()[0] == n_textos
        cur.execute("SELECT count(*) FROM egc.config_relatorio"); assert cur.fetchone()[0] == n_cfg
        cur.execute("SELECT count(*) FROM egc.empresas"); assert cur.fetchone()[0] == n_emp
        cur.execute("SELECT count(*) FROM egc.nf_debtor_empresa"); assert cur.fetchone()[0] == n_deb
        # ids reiniciados
        cur.execute("INSERT INTO egc.lancamentos (empresa_codigo,tipo,periodo,grupo,conta,valor,origem,granularidade) "
                    "VALUES ('ENERGIA','DRE','2026-06-30','G','C',1,'PDF','trimestral') RETURNING id")
        assert cur.fetchone()[0] == 1
    c.close()


def test_script_nao_quebra_se_tabela_opcional_nao_existe():
    c = pg.conectar_limpo()
    with c.cursor() as cur:
        cur.execute("DROP TABLE egc.nf_bills_orfaos CASCADE")
        cur.execute(SQL)  # nao pode levantar
        cur.execute("SELECT count(*) FROM egc.lancamentos"); assert cur.fetchone()[0] == 0
    c.close()


def test_conferencia_final_lista_cada_tabela_e_nao_deixa_nenhuma_sem_classificar():
    c = pg.conectar_limpo()
    with c.cursor() as cur:
        cur.execute(SQL)
        cur.execute("SELECT 1")  # o script termina no SELECT de conferencia; rodamos de novo so' o final
    with c.cursor() as cur:
        final = SQL[SQL.index("SELECT CASE"):]
        cur.execute(final)
        linhas = cur.fetchall()
    c.close()
    assert linhas, "conferencia final vazia"
    assert {l[0] for l in linhas} == {"APAGADA", "MANTIDA"}, [l for l in linhas if l[0] == "NAO CLASSIFICADA"]
    assert all(l[2] == 0 for l in linhas if l[0] == "APAGADA")
