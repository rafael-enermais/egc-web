# -*- coding: utf-8 -*-
"""v0.52.0 -- dashboard flexivel da tela Creditos PIS/COFINS (streamlit AppTest) contra Postgres real: filtros, simulacao, oficial, conferido, nota."""
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


def _cenario(c, com_det=False):
    """jan/26: historico ORIGINAL (450) + APONTADO (550); jul/26: SPED original (712,25) + retificadora com +5 de PIS (717,25, a apontada)."""
    cdb.upsert_historico(c, "ENERGIA", [
        {"competencia": "01/2026", "versao": "ORIGINAL", "resumo": {"credito_pis": 80, "credito_cofins": 370}, "por_categoria": {"Aluguel": {"pis": 80, "cofins": 370}}},
        {"competencia": "01/2026", "versao": "APONTADO", "sped_tipo": "Retificadora", "resumo": {"credito_pis": 100, "credito_cofins": 450},
         "por_categoria": {"Aluguel": {"pis": 100, "cofins": 450}}}])
    for tipo, aj in (("0", 0.0), ("1", 5.0)):
        cont, nome = sped_sintetico(tipo=tipo, ajuste_servico=aj)
        cdb.gravar_sped(c, "ENERGIA", sc.ler_sped(cont, nome), f"{tipo}_{nome}", "t")
    if com_det:
        cont, nome = detalhamento_sintetico()
        cdb.gravar_detalhamento(c, "ENERGIA", cdet.ler_detalhamento(cont, nome), nome, "t")


def _rodar():
    at = AppTest.from_file(str(TELA), default_timeout=90)
    at.run()
    return at


def _m(at):
    return {x.label: x for x in at.metric}


def test_estado_padrao_sem_simulacao_e_com_controles(conn):
    _cenario(conn)
    at = _rodar()
    assert not at.exception
    assert not any("Simulação ligada" in w.value for w in at.warning)
    assert _m(at)["Crédito acumulado no período"].value.endswith("1.267,25")           # 550 + 717,25
    assert _m(at)["Crédito acumulado no período"].delta in (None, "")
    assert any("Sugestões de conferência" in e.label for e in at.expander)
    assert any("Controle de jul/26" in e.label for e in at.expander)
    assert any(k.key == "cred_btn_ctrl_ENERGIA_07/2026" for k in at.button)


def test_simulacao_muda_os_numeros_na_hora_sem_gravar(conn):
    _cenario(conn)
    at = _rodar()
    at.selectbox(key="cred_sim_ENERGIA_01/2026").select("Carga fixa jan–jul · SPED original")
    at.run()
    assert not at.exception
    assert any("Simulação ligada" in w.value for w in at.warning)
    k = _m(at)["Crédito acumulado no período"]
    assert k.value.endswith("1.167,25") and "100,00" in (k.delta or "") and "vs oficial" in k.delta        # 450 + 717,25
    assert len(at.get("plotly_chart")) >= 9                                              # + a ponte
    assert cdb.carregar_mes(conn, "ENERGIA") == {}                                      # nada gravado
    [b for b in at.button if b.key == "cred_btn_sim_limpar"][0].click()
    at.run()
    assert not any("Simulação ligada" in w.value for w in at.warning)
    assert _m(at)["Crédito acumulado no período"].value.endswith("1.267,25")


def test_marcar_oficial_conferido_e_nota_gravam_e_mudam_o_dashboard(conn):
    _cenario(conn)
    at = _rodar()
    at.selectbox(key="cred_dash_mes").select("01/2026")
    at.run()
    alvo = [o for o in at.selectbox(key="cred_of_ENERGIA_01/2026_0").options if "original" in o.lower()][0]
    at.selectbox(key="cred_of_ENERGIA_01/2026_0").select(alvo)
    at.checkbox(key="cred_conf_ENERGIA_01/2026").check()
    at.text_area(key="cred_nota_ENERGIA_01/2026").input("usar o original por enquanto")
    [b for b in at.button if b.key == "cred_btn_ctrl_ENERGIA_01/2026"][0].click()
    at.run()
    assert not at.exception
    m = cdb.carregar_mes(conn, "ENERGIA")["01/2026"]
    assert m["fonte_oficial"] == "HIST_ORIGINAL" and m["conferido"] and m["nota"] == "usar o original por enquanto"
    at = _rodar()                                                                         # reabre: a oficial vale no dashboard
    assert _m(at)["Crédito acumulado no período"].value.endswith("1.167,25")
    assert not any("Simulação ligada" in w.value for w in at.warning)
    at.selectbox(key="cred_dash_mes").select("01/2026")
    at.run()
    assert any("oficial (marcada pela contadora)" in c.value and "conferido" in c.value for c in at.caption)


def test_filtro_de_periodo_recorta_kpis_e_graficos(conn):
    _cenario(conn)
    at = _rodar()
    at.selectbox(key="cred_de_ENERGIA").select("07/2026")
    at.run()
    assert not at.exception
    assert _m(at)["Crédito acumulado no período"].value.endswith("717,25")


def test_tributo_pis_vale_na_simulacao(conn):
    _cenario(conn)
    at = _rodar()
    at.radio(key="cred_trib").set_value("pis")
    at.run()
    assert not at.exception and _m(at)["Crédito acumulado no período"].value.endswith("232,05")      # 100 + 132,05


def test_sem_bloco30_dashboard_funciona_e_avisa_so_no_controle(conn):
    _cenario(conn)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.cred_mes_log")
        cur.execute("DROP TABLE egc.cred_mes")
    at = _rodar()
    assert not at.exception
    assert any("bloco 30" in i.value for i in at.info)
    assert _m(at)["Crédito acumulado no período"].value.endswith("1.267,25")
    assert not any(b.key == "cred_btn_ctrl_ENERGIA_07/2026" for b in at.button)


def test_comparar_versoes_e_sped_x_detalhamento(conn):
    _cenario(conn, com_det=True)
    at = _rodar()
    assert not at.exception
    assert any("Comparar duas versões" in e.label for e in at.expander) and any("SPED × Detalhamento" in e.label for e in at.expander)
    assert len(at.get("plotly_chart")) >= 11


# ---------------- v0.53.0: aba Relatorio (PDF) ----------------
def test_aba_relatorio_oficial_gera_o_botao_de_download(conn):
    _cenario(conn)
    at = _rodar()
    assert not at.exception
    assert any(d.proto.label == "⬇️ Baixar o relatório em PDF" for d in at.get("download_button"))
    assert any("Versão oficial de 07/2026" in s.value for s in at.success)
    assert not any("SIMULAÇÃO" in w.value for w in at.warning)
    assert at.selectbox(key="cred_rel_mes").value == "07/2026"


def test_aba_relatorio_com_simulacao_avisa_mas_nao_trava(conn):
    _cenario(conn)
    at = _rodar()
    at.selectbox(key="cred_sim_ENERGIA_01/2026").select("Carga fixa jan–jul · SPED original")
    at.run()
    assert not at.exception
    assert any("simulação" in w.value and "NÃO É A VERSÃO OFICIAL" in w.value for w in at.warning)      # aviso na aba Relatorio
    assert any(d.proto.label == "⬇️ Baixar o relatório em PDF" for d in at.get("download_button"))      # continua gerando
    [b for b in at.button if b.key == "cred_btn_sim_limpar_rel"][0].click()
    at.run()
    assert not any("NÃO É A VERSÃO OFICIAL" in w.value for w in at.warning)
    assert any("Versão oficial de 07/2026" in s.value for s in at.success)


def test_aba_relatorio_sem_dados_so_informa(conn):
    at = _rodar()
    assert not at.exception and any("O relatório fica disponível" in i.value for i in at.info)
    assert not any(d.proto.label == "⬇️ Baixar o relatório em PDF" for d in at.get("download_button"))


def test_aba_relatorio_escolhe_outro_mes(conn):
    _cenario(conn)
    at = _rodar()
    at.selectbox(key="cred_rel_mes").select("01/2026")
    at.run()
    assert not at.exception and any("Versão oficial de 01/2026" in s.value for s in at.success)
