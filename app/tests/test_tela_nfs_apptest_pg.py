# -*- coding: utf-8 -*-
"""v0.49.0 -- renderiza a tela Notas Fiscais de verdade (streamlit AppTest) contra Postgres real.
Pegou, na construcao, uma regressao que teste de funcao nao pega: COLUNAS_MOEDA ganhou a coluna das NFS e a tabela de NF-e
(que nao tem essa coluna) dava KeyError na tela."""
import io
import sys
import types
import uuid
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

import nf_sienge  # noqa: E402
import nfs_escrituracao as ne  # noqa: E402

TELA = Path(__file__).resolve().parent.parent / "telas" / "7_Notas_Fiscais.py"
COLS = ["CNPJ / CPF / CNO", "Número", "Espécie", "Data Emissão", "Data Escrituração/Serviço", "Razão Social", "Valor Total", "Descrição"]


@pytest.fixture()
def conn(monkeypatch):
    import conexao
    c = pg.conectar_limpo()
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj, sincronizado_em) VALUES (591,'FORN','11.222.333/0001-81', now())")
        cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
                    "issue_date, total_invoice_amount) VALUES (1,1,591,'NFE ','500','2026-08-05',10)")
        cur.execute("INSERT INTO egc.nfs_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
                    "issue_date, total_invoice_amount) VALUES (2,1,591,'NFS ','10','2026-08-05',100),(3,1,591,'NFS ','99','2026-08-06',9)")
    monkeypatch.setattr(conexao, "get_conn", lambda: c)
    monkeypatch.setattr(conexao, "sidebar_contexto", lambda u: None)
    auth = types.ModuleType("auth")
    auth.usuario_atual = lambda: "teste"
    monkeypatch.setitem(sys.modules, "auth", auth)
    yield c
    c.close()


def _manifesto(c):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                    "data_emissao, valor, cfop, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por) VALUES "
                    "(%s,'ENERGIA','08/2026','500','500','2026-08-05',10,'5102','11.222.333/0001-81','11222333000181','m.xlsx','t')",
                    (str(uuid.uuid4()),))
    nf_sienge.reconferir_empresa(c, "ENERGIA")


def _escrituracao(c):
    b = io.BytesIO()
    pd.DataFrame([["11.222.333/0001-81", "10", "NFSE", "2026-08-05", "2026-08-10", "FORN", 100.0, "S"],
                  ["11.222.333/0001-81", "11", "NFSE", "2026-08-05", "2026-08-10", "FORN", 50.0, "S"]], columns=COLS).to_excel(b, index=False)
    a = ne.analisar_planilha(b.getvalue(), "x.xlsx")
    ne.gravar_envio(c, "ENERGIA", "08/2026", "x.xlsx", a["df"], 2, "t")


def _rodar():
    at = AppTest.from_file(str(TELA), default_timeout=60)
    at.run()
    return at


def test_tela_sem_envio_de_nfs_nao_quebra_e_nfe_segue_igual(conn):
    _manifesto(conn)
    at = _rodar()
    assert not at.exception
    rot = [m.label for m in at.metric]
    assert "Notas no manifesto" in rot and "NFS na escrituração" not in rot
    dl = [d.proto.label for d in at.get("download_button")]
    assert dl == ["⬇️ Baixar conferência COMPLETA (todas as notas + Sienge)"]      # sem pendencia nem NFS: so' o completo


def test_tela_com_nfs_mostra_kpis_e_baixa_planilhas_com_aba_nfs(conn):
    _manifesto(conn)
    _escrituracao(conn)
    at = _rodar()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["NFS na escrituração"] == "2" and m["Sienge sem escrituração"] == "1" and m["Lançadas"] in ("1",)
    dl = [d.proto.label for d in at.get("download_button")]
    assert "⬇️ Baixar só as pendências (pra mandar ao Suprimentos)" in dl      # pendencia so' de NFS tambem aparece
    assert "⬇️ Baixar só a conferência de NFS" in dl


def test_tela_somente_nfs_sem_manifesto(conn):
    _escrituracao(conn)
    at = _rodar()
    assert not at.exception
    assert any(x.label == "NFS na escrituração" for x in at.metric)


def test_tela_sem_bloco27_avisa_e_nao_quebra(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.nfs_escrituracao CASCADE")
    _manifesto(conn)
    at = _rodar()
    assert not at.exception
    assert any("BLOCO 27" in i.value for i in at.info)
    assert any(x.label == "Notas no manifesto" for x in at.metric)
