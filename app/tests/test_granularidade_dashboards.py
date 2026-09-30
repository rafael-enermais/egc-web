# -*- coding: utf-8 -*-
"""
Granularidade OBRIGATORIA nos dashboards/funcoes (30/09/2026): Inicio
(empresa e consolidado do grupo), Visao Grupo, chat (consultar_indicadores),
indicadores.py e visao_grupo.py -- "o match de periodo+granularidade e'
OBRIGATORIO em tudo". Mesmo oraculo numerico de
test_granularidade_obrigatoria.py (EBITDA Energia trimestral
-847.960,37 / semestral 724.975,31 / grupo trimestral -16.268,75).
"""
import datetime
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import consultas_chat as cc  # noqa: E402
import db  # noqa: E402
import formatacao  # noqa: E402
import indicadores  # noqa: E402
import parser_egc  # noqa: E402
import visao_grupo  # noqa: E402
import test_granularidade_obrigatoria as T  # noqa: E402  (mesmo "banco" em memoria do oraculo)

PAGE_INICIO = str(Path(__file__).resolve().parent.parent / "app.py")
PAGE_GRUPO = str(Path(__file__).resolve().parent.parent / "telas" / "4_Visao_Grupo.py")
P_JUN = T.P_JUN


@pytest.fixture(autouse=True)
def banco():
    T._montar_banco()
    yield


def _patch_db_dashboards():
    """Patches de db.* que app.py / 4_Visao_Grupo.py / consultas_chat usam, servidos pelo banco em memoria."""
    def hist(conn, cod, tipo, status="ATIVO", granularidade=None):
        return T.FakeDB.listar_historico_grupo(conn, cod, tipo, status, granularidade)

    def det_grupo(conn, cods, status="ATIVO"):
        pares = sorted({(l["periodo"], l["gran"]) for l in T.LINHAS if l["empresa"] in cods}, reverse=True)
        return [{"periodo": p, "granularidade": g} for p, g in pares]

    return [
        patch.object(auth, "require_login", return_value="t@enermais.com.br"),
        patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"),
        patch.object(conexao, "get_conn", return_value=None),
        patch.object(db, "listar_periodos", side_effect=lambda conn, cod, status="ATIVO": T.FakeDB.listar_periodos(conn, cod, status)),
        patch.object(db, "listar_historico_grupo", side_effect=hist),
        patch.object(db, "listar_periodos_grupo", side_effect=T.FakeDB.listar_periodos_grupo),
        patch.object(db, "listar_periodos_grupo_detalhado", side_effect=det_grupo),
        patch.object(db, "listar_periodos_detalhado", side_effect=T.FakeDB.listar_periodos_detalhado),
        patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=T.FakeDB.listar_lancamentos_grupo_periodos),
    ]


class _Ctx:
    def __enter__(self):
        self.ps = _patch_db_dashboards()
        for p in self.ps:
            p.start()

    def __exit__(self, *a):
        for p in self.ps:
            p.stop()


# ------------------------------------------------------------ indicadores.py
def test_filtrar_granularidade_exata_e_sem_coluna_conta_como_vazio():
    lancs = [dict(periodo=P_JUN, conta="X", valor=1, granularidade="trimestral"),
             dict(periodo=P_JUN, conta="X", valor=2, granularidade="semestral"),
             dict(periodo=P_JUN, conta="X", valor=3)]
    assert [l["valor"] for l in indicadores.filtrar_granularidade_exata(lancs, "trimestral")] == [1]
    assert [l["valor"] for l in indicadores.filtrar_granularidade_exata(lancs, "")] == [3]
    assert indicadores.filtrar_granularidade_exata(lancs, "anual") == []


def test_granularidades_disponiveis_ordem_mais_abrangente_primeiro_e_bimestral():
    lancs = [dict(granularidade=g, periodo=P_JUN) for g in ["mensal", "", "bimestral", "anual", "trimestral", "semestral", "outra"]]
    assert indicadores.granularidades_disponiveis(lancs) == [
        "anual", "semestral", "trimestral", "bimestral", "mensal", "outra", ""]
    assert indicadores.rotulo_granularidade("bimestral") == "Bimestral"
    assert indicadores.rotulo_granularidade("") == "Não declarada"


def test_granularidade_padrao_empresa_unica_e_a_mais_abrangente_do_periodo_mais_recente():
    bp = T.FakeDB.listar_historico_grupo(None, "ENERGIA", "BP")
    dre = T.FakeDB.listar_historico_grupo(None, "ENERGIA", "DRE")
    assert indicadores.granularidade_padrao(bp, dre) == "semestral"   # 06/2026 tem trimestral e semestral
    assert indicadores.granularidade_padrao([], []) == ""


def test_granularidade_padrao_grupo_ignora_base_que_so_uma_empresa_tem():
    bp = T.FakeDB.listar_lancamentos_grupo_periodos(None, [P_JUN, T.P_MAR], "BP", T.TODAS)
    dre = T.FakeDB.listar_lancamentos_grupo_periodos(None, [P_JUN, T.P_MAR], "DRE", T.TODAS)
    # semestral de 06/2026 so' existe na Energia -> default do consolidado = trimestral (6 empresas)
    assert indicadores.granularidade_padrao(bp, dre, T.TODAS) == "trimestral"
    assert indicadores.empresas_faltando(bp, dre, T.TODAS, P_JUN, "semestral") == ["SMG", "ENG", "RENOV", "CONST", "SOL"]
    assert indicadores.empresas_faltando(bp, dre, T.TODAS, P_JUN, "trimestral") == []


def test_calcular_indicadores_granularidade_exata_por_periodo_sem_misturar():
    bp = T.FakeDB.listar_historico_grupo(None, "ENERGIA", "BP")
    dre = T.FakeDB.listar_historico_grupo(None, "ENERGIA", "DRE")
    tri = indicadores.calcular_indicadores(bp, dre, granularidade="trimestral")
    sem = indicadores.calcular_indicadores(bp, dre, granularidade="semestral")
    anu = indicadores.calcular_indicadores(bp, dre, granularidade="anual")
    assert list(tri.index) == [pd.Timestamp(T.P_MAR), pd.Timestamp(P_JUN)]
    assert list(sem.index) == [pd.Timestamp(T.P_DEZ25), pd.Timestamp(P_JUN)]
    assert list(anu.index) == [pd.Timestamp(T.P_DEZ24)]
    assert tri.loc[pd.Timestamp(P_JUN), "EBITDA"] == pytest.approx(-847960.37, abs=0.01)
    assert sem.loc[pd.Timestamp(P_JUN), "EBITDA"] == pytest.approx(724975.31, abs=0.01)
    assert tri.loc[pd.Timestamp(T.P_MAR), "EBITDA"] == pytest.approx(1572935.68, abs=0.01)
    assert indicadores.calcular_indicadores(bp, dre, granularidade="mensal").empty


# ------------------------------------------------------------ visao_grupo.py
def test_montar_serie_kpis_grupo_granularidade_exata():
    lancs = T.FakeDB.listar_lancamentos_grupo_periodos(None, [P_JUN], "DRE", ["ENERGIA"])
    tri = visao_grupo.montar_serie_kpis_grupo(lancs, ["ENERGIA"], visao_grupo.CONTAS_KPI_DRE, [P_JUN], granularidade="trimestral")
    sem = visao_grupo.montar_serie_kpis_grupo(lancs, ["ENERGIA"], visao_grupo.CONTAS_KPI_DRE, [P_JUN], granularidade="semestral")
    assert tri.loc[pd.Timestamp(P_JUN), "RECEITA OPERACIONAL LIQUIDA"] == pytest.approx(17380604.41)
    assert sem.loc[pd.Timestamp(P_JUN), "RECEITA OPERACIONAL LIQUIDA"] == pytest.approx(33350846.80)
    # sem granularidade (legado) continua = vencedora (semestral), nunca soma os 2
    legado = visao_grupo.montar_serie_kpis_grupo(lancs, ["ENERGIA"], visao_grupo.CONTAS_KPI_DRE, [P_JUN])
    assert legado.loc[pd.Timestamp(P_JUN), "RECEITA OPERACIONAL LIQUIDA"] == pytest.approx(33350846.80)


# ------------------------------------------------------------ chat
def test_chat_consultar_indicadores_informa_a_base_e_respeita_a_granularidade_pedida():
    with _Ctx():
        r = cc.consultar_indicadores(None, ["ENERGIA"])
        assert r["base_do_periodo"] == "Semestral" and r["granularidade"] == "semestral"
        assert r["indicadores"]["EBITDA"] == pytest.approx(724975.31, abs=0.01)
        assert "Semestral" in r["granularidades_disponiveis"] and "Trimestral" in r["granularidades_disponiveis"]

        r = cc.consultar_indicadores(None, ["ENERGIA"], "trimestral")
        assert r["base_do_periodo"] == "Trimestral"
        assert r["indicadores"]["EBITDA"] == pytest.approx(-847960.37, abs=0.01)

        r = cc.consultar_indicadores(None, ["ENERGIA"], "mensal")
        assert "erro" in r and "Trimestral" in r["granularidades_disponiveis"]


def test_chat_consultar_indicadores_grupo_default_trimestral_ebitda_do_grupo():
    with _Ctx():
        r = cc.consultar_indicadores(None, T.TODAS)
        assert r["granularidade"] == "trimestral", "default do grupo nao pode ser o semestral so' da Energia"
        assert r["periodo"] == "2026-06"
        assert r["indicadores"]["EBITDA"] == pytest.approx(-16268.75, abs=0.01)
        assert "aviso_consolidado_parcial" not in r
        r2 = cc.consultar_indicadores(None, T.TODAS, "semestral")
        assert "aviso_consolidado_parcial" in r2 and "SMG" in r2["aviso_consolidado_parcial"]


# ------------------------------------------------------------ Inicio (AppTest)
def test_inicio_empresa_base_do_periodo_troca_os_numeros_sem_misturar():
    with _Ctx():
        at = AppTest.from_file(PAGE_INICIO)
        at.run(timeout=60)
        assert not at.exception, at.exception
        sel = at.selectbox(key="inicio_base_sel_ENERGIA")
        assert sel.options == ["Anual", "Semestral", "Trimestral"]   # so' as bases que existem, da mais abrangente pra menos
        assert sel.value == "semestral"
        ebitda = next(m for i, m in enumerate(at.metric) if m.label == "EBITDA" and i < 9)
        assert ebitda.value == formatacao.moeda_br(724975.31)

        sel.set_value("trimestral").run(timeout=60)
        assert not at.exception, at.exception
        ebitda = next(m for i, m in enumerate(at.metric) if m.label == "EBITDA" and i < 9)
        assert ebitda.value == formatacao.moeda_br(-847960.37)
        # delta = vs trimestral 03/2026 (mesma base): -847.960,37 - 1.572.935,68
        assert ebitda.delta == formatacao.moeda_br(-847960.37 - 1572935.68, forcar_sinal=True)
        legenda = " ".join(c.value for c in at.caption)
        assert "Base: Trimestral" in legenda and "(mesma base)" in legenda


def test_inicio_consolidado_do_grupo_default_trimestral_ebitda_menos_16268_75():
    with _Ctx():
        at = AppTest.from_file(PAGE_INICIO)
        at.run(timeout=60)
        assert not at.exception, at.exception
        sel = at.selectbox(key="inicio_base_grupo_sel")
        assert sel.value == "trimestral"
        ebitda_grupo = next(m for i, m in enumerate(at.metric) if m.label == "EBITDA" and i >= 9)
        assert ebitda_grupo.value == formatacao.moeda_br(-16268.75)
        assert not any("Consolidado parcial" in w.value for w in at.warning)

        sel.set_value("semestral").run(timeout=60)   # so' a Energia tem -> avisa que e' parcial
        assert any("Consolidado parcial" in w.value and "SMG" in w.value for w in at.warning)


# ------------------------------------------------------------ Visao Grupo (AppTest)
def test_visao_grupo_base_trimestral_kpis_do_trimestral():
    with _Ctx():
        at = AppTest.from_file(PAGE_GRUPO)
        at.run(timeout=60)
        assert not at.exception, at.exception
        assert at.selectbox(key="grupo_base_sel").value == "trimestral"
        receita = next(m for m in at.metric if m.label == "Receita Líquida")
        assert receita.value == formatacao.moeda_br(23874026.45)   # grupo trimestral, 06/2026


# ------------------------------------------------------------ bimestral (classificador)
@pytest.mark.parametrize("ini,fim,esperado", [
    ("01/05/2026", "30/06/2026", "bimestral"),    # 61 dias
    ("01/01/2026", "28/02/2026", "bimestral"),    # 59 dias
    ("01/07/2026", "31/08/2026", "bimestral"),    # 62 dias
    ("01/06/2026", "30/06/2026", "mensal"),
    ("01/04/2026", "30/06/2026", "trimestral"),   # 91 dias
    ("01/01/2026", "30/06/2026", "semestral"),
    ("01/01/2025", "31/12/2025", "anual"),
])
def test_calcular_granularidade_bimestral(ini, fim, esperado):
    assert parser_egc.calcular_granularidade(ini, fim) == esperado
