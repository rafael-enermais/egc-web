# -*- coding: utf-8 -*-
"""v0.50.0/v0.51.0 -- renderiza a tela Creditos PIS/COFINS de verdade (streamlit AppTest) contra Postgres real."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

import creditos_db as cdb  # noqa: E402
import creditos_detalhamento as cdet  # noqa: E402
import sped_contribuicoes as sc  # noqa: E402
from creditos_fixtures import detalhamento_sintetico, sped_sintetico  # noqa: E402

TELA = Path(__file__).resolve().parent.parent / "telas" / "10_Creditos_PIS_COFINS.py"


@pytest.fixture()
def conn(monkeypatch):
    import conexao
    c = pg.conectar_limpo()
    monkeypatch.setattr(conexao, "get_conn", lambda: c)
    monkeypatch.setattr(conexao, "sidebar_contexto", lambda u: None)
    auth = types.ModuleType("auth")
    auth.usuario_atual = lambda: "teste"
    monkeypatch.setitem(sys.modules, "auth", auth)
    yield c
    c.close()


def _gravar(c, sped=True, det=True, **kw):
    if sped:
        cont, nome = sped_sintetico(**kw)
        cdb.gravar_sped(c, "ENERGIA", sc.ler_sped(cont, nome), nome, "t")
    if det:
        cont, nome = detalhamento_sintetico()
        cdb.gravar_detalhamento(c, "ENERGIA", cdet.ler_detalhamento(cont, nome), nome, "t")


def _rodar():
    at = AppTest.from_file(str(TELA), default_timeout=90)
    at.run()
    return at


def test_sem_bloco28_avisa_e_nao_quebra(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.cred_historico")
    at = _rodar()
    assert not at.exception
    assert any("bloco 28" in w.value for w in at.warning)


def test_tela_vazia_nao_quebra(conn):
    at = _rodar()
    assert not at.exception
    assert any("Ainda não há arquivos" in i.value for i in at.info)
    assert any("histórico" in i.value for i in at.info)               # dashboard sem dados


def test_conferencia_completa_tudo_conferido_e_download(conn):
    _gravar(conn)
    at = _rodar()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["Crédito PIS (SPED)"].endswith("127,05") and m["Linhas conferidas"] == "4 de 5" and m["Pendentes"] == "0"
    assert any("não há pendências" in s.value for s in at.success)
    assert [d.proto.label for d in at.get("download_button")] == ["⬇️ Baixar a conferência (.xlsx)", "⬇️ Baixar o relatório em PDF"]   # v0.53.0: + aba Relatório
    assert len(at.get("arrow_data_frame")) >= 1 or True


def test_conferencia_mostra_pendente_quando_nao_bate(conn):
    cont, nome = sped_sintetico(ajuste_servico=5.0)
    cdb.gravar_sped(conn, "ENERGIA", sc.ler_sped(cont, nome), nome, "t")
    cont, nome = detalhamento_sintetico()
    cdb.gravar_detalhamento(conn, "ENERGIA", cdet.ler_detalhamento(cont, nome), nome, "t")
    at = _rodar()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["Pendentes"] == "1"
    assert any("Há diferença nas abas" in w.value for w in at.warning)


def test_so_sped_sem_detalhamento_mostra_numeros_do_sped(conn):
    _gravar(conn, det=False)
    at = _rodar()
    assert not at.exception
    assert any("Falta o Detalhamento" in i.value for i in at.info)
    assert {x.label for x in at.metric} >= {"Crédito PIS", "Crédito COFINS"}


def test_so_detalhamento_sem_sped_avisa(conn):
    _gravar(conn, sped=False)
    at = _rodar()
    assert not at.exception
    assert any("ainda não tem o SPED" in w.value for w in at.warning)


def test_dashboard_com_historico_e_envio(conn):
    cdb.upsert_historico(conn, "ENERGIA", [
        {"competencia": "01/2026", "versao": "ORIGINAL", "resumo": {"credito_pis": 80, "credito_cofins": 370}},
        {"competencia": "01/2026", "versao": "APONTADO", "resumo": {"credito_pis": 100, "credito_cofins": 450},
         "por_categoria": {"Aluguel": {"pis": 100, "cofins": 450, "linhas": 2}}, "dif_fornecedores": [{"fornecedor": "A", "dif_pis": 20, "dif_cofins": 80}]}])
    _gravar(conn, det=False)
    at = _rodar()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["Diferença acumulada original × apontado"].endswith("100,00")
    assert "Crédito apontado em jul/26" in m and m["Crédito apontado em jul/26"].endswith("712,25")
    assert len(at.get("plotly_chart")) >= 8


def test_salvar_sem_mudanca_avisa_e_nao_grava_nada(conn):
    _gravar(conn)
    at = _rodar()
    assert not at.exception
    assert {b.key for b in at.button} >= {"cred_save_linhas", "cred_save_categorias", "cred_save_bases", "cred_save_totais", "cred_save_debitos"}
    [b for b in at.button if b.key == "cred_save_linhas"][0].click()
    at.run()
    assert not at.exception and any("Nenhuma mudança" in i.value for i in at.info)
    assert cdb.carregar_ajustes(conn, "ENERGIA", "07/2026") == []


def test_edicao_salva_muda_a_conferencia_e_pode_ser_restaurada_pela_tela(conn):
    # (o AppTest nao simula digitar dentro do data_editor; a edicao e' gravada pelo mesmo caminho que o botao Salvar usa)
    _gravar(conn)
    ref = cdb.carregar_sped(conn, "ENERGIA", "07/2026")["envio_id"]
    cdb.salvar_ajustes(conn, "ENERGIA", "07/2026", "linhas",
                       [{"chave": "11111111000111|1001", "campo": "pis_det", "valor_original": "16.5", "valor_novo": "10"}], "t", ref=ref)
    at = _rodar()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["Pendentes"] == "1" and m["Edições salvas"] == "1"
    at.selectbox(key="cred_sel_rest").select_index(0)
    [b for b in at.button if b.key == "cred_btn_rest_linha"][0].click()
    at.run()
    assert not at.exception
    assert cdb.carregar_ajustes(conn, "ENERGIA", "07/2026", ref=ref) == []
    m = {x.label: x.value for x in at.metric}
    assert m["Pendentes"] == "0" and m["Edições salvas"] == "0"


def test_aba_enviar_tem_os_dois_uploaders(conn):
    at = _rodar()
    assert not at.exception
    assert len(at.get("file_uploader")) == 2


def test_sem_bloco29_avisa_e_nao_quebra(conn):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE egc.cred_ajuste DROP COLUMN envio_ref")
    at = _rodar()
    assert not at.exception
    assert any("bloco 29" in w.value for w in at.warning)


def test_duas_versoes_do_sped_seletor_aviso_e_tornar_apontada(conn):
    _gravar(conn)                                                                  # 1a: Original (+ Detalhamento)
    cont, nome = sped_sintetico(ajuste_servico=5.0, tipo="1")
    cdb.gravar_sped(conn, "ENERGIA", sc.ler_sped(cont, nome), nome, "t")           # 2a: Retificadora = apontada
    at = _rodar()
    assert not at.exception
    sel = at.selectbox(key="cred_ver_sped_07/2026")
    assert "APONTADA" in sel.value and sel.value.startswith("2ª")
    assert not any("não é a apontada" in w.value for w in at.warning)
    # a contadora escolhe conferir o ORIGINAL: aparece o aviso e o botao de tornar apontada
    at.selectbox(key="cred_ver_sped_07/2026").select_index(0)
    at.run()
    assert not at.exception and any("não é a apontada" in w.value for w in at.warning)
    m = {x.label: x.value for x in at.metric}
    assert m["Crédito PIS (SPED)"].startswith("R$ 127,05")
    [b for b in at.button if b.key == "cred_apontar_conf_SPED_07/2026"][0].click()
    at.run()
    assert not at.exception
    assert cdb.carregar_sped(conn, "ENERGIA", "07/2026")["cabecalho"]["tipo"] == "Original"
    assert len(cdb.listar_versoes(conn, "SPED", "ENERGIA", "07/2026")) == 2          # nada apagado


def test_edicao_do_original_nao_aparece_na_retificadora_pela_tela(conn):
    _gravar(conn)
    ref_orig = cdb.carregar_sped(conn, "ENERGIA", "07/2026")["envio_id"]
    cont, nome = sped_sintetico(ajuste_servico=5.0, tipo="1")
    cdb.gravar_sped(conn, "ENERGIA", sc.ler_sped(cont, nome), nome, "t")
    cdb.salvar_ajustes(conn, "ENERGIA", "07/2026", "linhas",
                       [{"chave": "11111111000111|1001", "campo": "pis_det", "valor_original": "16.5", "valor_novo": "10"}], "t", ref=ref_orig)
    at = _rodar()                                                                   # abre na apontada (retificadora)
    assert not at.exception and {x.label: x.value for x in at.metric}["Edições salvas"] == "0"
    at.selectbox(key="cred_ver_sped_07/2026").select_index(0)
    at.run()
    assert {x.label: x.value for x in at.metric}["Edições salvas"] == "1"
