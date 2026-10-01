# -*- coding: utf-8 -*-
"""v0.44.2 -- fluxo REAL das telas contra Postgres (teste geral antes de entregar).

1. Toda tela carrega sem excecao no cenario semeado (6 empresas, varios periodos).
2. Mensagens de gravacao SOBREVIVEM ao st.rerun() (antes: st.success + st.rerun sumia).
3. Consulta rapida do Erik.AI de empresa que so' tem DRE (sem BP) nao quebra (KeyError 'valor').
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402

APP = Path(__file__).resolve().parent.parent
TELAS = ["telas/1_Importar_PDF.py", "telas/2_Revisao_Correcao.py", "telas/3_Arquivar_Recuperar.py",
         "telas/4_Visao_Grupo.py", "telas/6_Assistente.py", "telas/7_Notas_Fiscais.py",
         "telas/8_Relatorio_Comentado.py"]


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    pg.semear_cenario(c)
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(auth, "require_login", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=c):
        yield c
    c.close()


def _abrir(rel):
    at = AppTest.from_file(str(APP / rel))
    at.run(timeout=120)
    return at


@pytest.mark.parametrize("rel", TELAS + ["app.py"])
def test_toda_tela_carrega_sem_excecao(conn, rel):
    at = _abrir(rel)
    assert not at.exception, f"{rel}: {[str(e.value) for e in at.exception]}"


def test_revisao_adicionar_conta_mantem_mensagem_apos_rerun(conn):
    at = _abrir("telas/2_Revisao_Correcao.py")
    at.text_input[0].set_value("CONTA TESTE FLASH")
    at.number_input[0].set_value(10.0).run(timeout=120)
    next(b for b in at.button if "Adicionar" in b.label).click().run(timeout=120)
    assert not at.exception
    assert any("CONTA TESTE FLASH" in s.value and "adicionada" in s.value for s in at.success), \
        "confirmacao sumiu apos o rerun"
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM egc.lancamentos WHERE conta='CONTA TESTE FLASH'")
        assert cur.fetchone()[0] == 1


def test_importar_gravar_mantem_mensagem_apos_rerun(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM egc.lancamentos WHERE empresa_codigo='SOL'")
    item = {
        "arquivo": "SOL - DRE.pdf", "admin_itens": [], "log": [["OK", "SOL - DRE.pdf", "DRE (TEXTO)", "2 contas"]],
        "bp_rows": [],
        "dre_rows": [["RECEITA BRUTA", "1.000,00", "RECEITAS", "PDF 31/12/2025"], ["IMPOSTOS", "10,00", "DEDUCOES", "PDF 31/12/2025"]],
        "meta": [["Enermais Solucoes Ltda", "60.353.219/0001-04", "31/12/2025", "SOL - DRE.pdf", "DRE", "TEXTO", "01/01/2025", "anual"]],
    }
    at = AppTest.from_file(str(APP / "telas/1_Importar_PDF.py"))
    at.run(timeout=120)
    at.session_state["import_resultados"] = [item]
    at.run(timeout=120)
    at.button(key="gravar_SOL").click().run(timeout=120)
    assert not at.exception
    assert any("gravado" in s.value for s in at.success), "confirmacao da gravacao sumiu apos o rerun"


def test_consulta_rapida_do_erik_empresa_so_com_dre(conn):
    # CONST so' tem DRE (sem BP) no periodo -- antes: KeyError 'valor'
    with conn.cursor() as cur:
        cur.execute("UPDATE egc.lancamentos SET status='INATIVO' WHERE empresa_codigo='CONST' AND tipo='BP'")
    at = _abrir("telas/6_Assistente.py")
    idx = next(i for i, (c, _n, _j) in enumerate(conexao.EMPRESAS_FIXAS) if c == "CONST")
    at.selectbox(key="assistente_empresa_sel").select(idx).run(timeout=120)
    assert not at.exception, [str(e.value) for e in at.exception]
    assert any("não tem BP ativo" in i.value for i in at.info)
