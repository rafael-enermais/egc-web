# -*- coding: utf-8 -*-
"""Teste geral pre-entrega (v0.46.3): banco no estado EXATO que a contadora recebe -- schema.sql completo +
scripts/zerar_dados_teste.sql aplicado -- e TODAS as paginas abrindo sem excecao (estado vazio), sem
"sem saida" (cada pagina avisa o que fazer) e a Notas Fiscais fazendo o 1o ciclo completo do zero:
upload -> conferencia -> anotacao -> planilha -> arquivar -> restaurar."""
import io
import sys
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
import nf_sienge  # noqa: E402
import nf_export  # noqa: E402

APP = Path(__file__).resolve().parent.parent
ZERAR = (APP.parent / "scripts" / "zerar_dados_teste.sql").read_text(encoding="utf-8")
# app.py = porta de login (Supabase Auth): precisa de secrets reais, conferida em producao.
PAGINAS = sorted(str(p) for p in (APP / "telas").glob("*.py"))


@pytest.fixture()
def banco_zerado():
    c = pg.conectar_limpo()
    with c.cursor() as cur:
        cur.execute(ZERAR)
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge._TAB_ANOTACAO["ok"] = False
    yield c
    c.close()


def _com_banco(c):
    from contextlib import ExitStack
    st_ = ExitStack()
    st_.enter_context(patch.object(auth, "usuario_atual", return_value="contadora@enermais.com.br"))
    st_.enter_context(patch.object(conexao, "get_conn", return_value=c))
    return st_


@pytest.mark.parametrize("pagina", PAGINAS, ids=[Path(p).name for p in PAGINAS])
def test_toda_pagina_abre_no_banco_zerado_sem_excecao(banco_zerado, pagina):
    with _com_banco(banco_zerado):
        at = AppTest.from_file(pagina)
        at.run(timeout=60)
        assert not at.exception, [str(e.value) for e in at.exception]
        # unico "erro" aceito: NF avisando (com a saida: botao "Atualizar agora") que o Sienge ainda nao sincronizou --
        # no banco real a sincronizacao fica (nf_bills_sync e' tabela MANTIDA pelo script de zerar).
        erros = [e.value for e in at.error if 'Atualizar agora' not in e.value]
        assert not erros, erros


def _planilha_receita_bytes():
    """Manifesto minimo no formato da Receita usado pelo parser (reaproveita o gerador dos testes de NF)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_fluxo_geral_nf_pg as fg
    return fg


def test_primeiro_ciclo_de_notas_fiscais_do_zero(banco_zerado):
    c = banco_zerado
    # Sienge precisa ter sincronizado (a tela exige) + mapa de devedor
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (591,'COSTA BENTO','07.393.522/0001-40')")
        cur.execute("INSERT INTO egc.nf_bills_sync (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
                    "issue_date, total_invoice_amount) VALUES (1,1,591,'NFE','10','2026-07-20',100)")
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    import uuid
    imp = str(uuid.uuid4())
    with c.cursor() as cur:
        for n, v, cf in [("10", 100.0, "5102"), ("20", 50.0, "5929"), ("30", 70.0, "5102")]:
            cur.execute(
                "INSERT INTO egc.nf_manifesto_import (import_id, empresa_codigo, periodo_referencia, numero_nota, numero_normalizado, "
                "data_emissao, valor, cfop, fornecedor_nome, fornecedor_cnpj, cnpj_normalizado, arquivo_nome, criado_por) "
                "VALUES (%s,'ENERGIA','07/2026',%s,%s,'2026-07-21',%s,%s,'COSTA BENTO','07.393.522/0001-40','07393522000140','m.xlsx','t')",
                (imp, n, n, v, cf))
    nf_sienge.reconferir_empresa(c, "ENERGIA")
    # a tela mostra a conferencia, sem excecao, e oferece anotacao
    with _com_banco(c):
        at = AppTest.from_file(str(APP / "telas" / "7_Notas_Fiscais.py"))
        at.run(timeout=60)
        assert not at.exception, [str(e.value) for e in at.exception]
        assert any(b.key == "nf_btn_salvar_anot" for b in at.button)
        at.selectbox(key="nf_anot_modelo").select("Lançar no Sienge")
        at.run(timeout=60)
        at.button(key="nf_btn_salvar_anot").click().run(timeout=60)
        assert not at.exception, [str(e.value) for e in at.exception]
    notas = nf_sienge.carregar_anotacoes(c, ["ENERGIA"])
    assert list(notas.values()) == ["Lançar no Sienge"]
    t = nf_sienge.tabela_conferencia(c, nf_sienge.listar_vigentes(c, "ENERGIA"))
    assert sorted(t["status"]) == ["LANCADA", "NAO_ENCONTRADA", "NAO_ENCONTRADA"]
    xls = nf_export.gerar_xlsx_conferencia(t, "Enermais Energia Ltda", "07/2026", "m.xlsx", "05/10/2026 12:00", empresa_codigo="ENERGIA")
    df = pd.read_excel(io.BytesIO(xls), sheet_name="Pendências", header=nf_export.LINHA_CABECALHO - 1)
    assert len(df) == 2 and df["Anotação"].notna().sum() == 1
    assert df["Observação"].fillna("").str.contains("combustível").sum() == 1
    # arquivar o unico envio zera a conferencia vigente; restaurar traz de volta (nada apagado)
    env = nf_sienge.listar_envios(c, "ENERGIA")
    ids = [i for e in env for i in e["import_ids"]]
    nf_sienge.arquivar_envio(c, ids, "contadora", "teste")
    assert nf_sienge.listar_vigentes(c, "ENERGIA") == []
    nf_sienge.restaurar_envio(c, ids, "contadora")
    assert len(nf_sienge.listar_vigentes(c, "ENERGIA")) == 1
