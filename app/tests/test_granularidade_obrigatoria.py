# -*- coding: utf-8 -*-
"""
ORACULO NUMERICO do bug real de granularidade (30/09/2026).

Regra do Rafael: "o match de periodo+granularidade e' OBRIGATORIO em tudo
-- relatorio, funcoes, dashboards -- para os valores fecharem".

Bug: indicadores.calcular_indicadores escolhia UMA granularidade
"vencedora" por periodo_fim, ignorando a granularidade escolhida pro
relatorio. Um relatorio TRIMESTRAL de 06/2026 da Energia mostrava receita
do trimestral (17.380.604,41) mas EBITDA/margens do SEMESTRAL (724.975,31);
no GRUPO o mesmo 724.975,31 aparecia porque a "vencedora" descartava as
outras 5 empresas (so' a Energia tem semestral).

Estes testes usam um "banco" em memoria (linhas ATIVAS de BP/DRE) com os
valores conferidos pelo Rafael nos dados brutos do Supabase, e rodam o
pipeline REAL (indicadores.calcular_indicadores, visao_grupo, montagem do
dict) -- so' o acesso ao banco (db.*) e' substituido.

Valores do oraculo (DRE ATIVO, 30/06/2026):
  ENERGIA trimestral : rec 17380604.41 | LO -1804157.20 | DF -636411.11 |
                       RF 33116.50 | deprec -352902.22 | LL -1804157.20
                       -> EBITDA -847960.37 (margem -4,88%), ML -10,38%
  ENERGIA semestral  : rec 33350846.80 | LO -1106049.63 | DF -1195046.22 |
                       RF 55548.53 | deprec -691527.25 | LL -1337674.90
                       -> EBITDA 724975.31 (margem 2,17%)
  Outras (so' trimestral): CONST 2319740.04, ENG -1036441.72, RENOV
                       -191306.52, SMG 73921.45, SOL -334221.63
  GRUPO trimestral   : EBITDA -16268.75 | rec 23874026.45 | LL -1226491.10
"""
import datetime
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
import indicadores  # noqa: E402

P_JUN = datetime.date(2026, 6, 30)
P_MAR = datetime.date(2026, 3, 31)
P_DEZ25 = datetime.date(2025, 12, 31)
P_DEZ24 = datetime.date(2024, 12, 31)

EMPRESAS = [
    {"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"},
    {"codigo": "SMG", "nome": "SMG Soluções", "cnpj": "18.387.666/0001-00"},
    {"codigo": "ENG", "nome": "Enermais Engenharia", "cnpj": "50.337.899/0001-00"},
    {"codigo": "RENOV", "nome": "Enermais Renováveis", "cnpj": "51.671.106/0001-58"},
    {"codigo": "CONST", "nome": "Enermais Construtora", "cnpj": "55.244.465/0001-80"},
    {"codigo": "SOL", "nome": "Enermais Soluções", "cnpj": "60.353.219/0001-04"},
]
TODAS = [e["codigo"] for e in EMPRESAS]

# ---------------------------------------------------------------- "banco"
LINHAS: list = []  # cada item: dict(empresa, periodo, gran, tipo, grupo, conta, valor)
ITENS_ADMIN: dict = {}  # (empresa, periodo, gran) -> [(conta, valor)]


def _dre(emp, per, gran, rec, lo, df, rf, dep, ll, adm=None):
    contas = [
        ("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", rec),
        ("RESULTADO", "LUCRO OPERACIONAL LIQUIDO", lo),
        ("DESPESAS", "DESPESAS FINANCEIRAS", df),
        ("RECEITAS", "RECEITAS FINANCEIRAS", rf),
        ("DESPESAS", "DEPRECIACOES", dep),
        ("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", ll),
    ]
    if adm is not None:
        contas.append(("DESPESAS", "ADMINISTRATIVAS", adm))
    for grupo, conta, valor in contas:
        LINHAS.append(dict(empresa=emp, periodo=per, gran=gran, tipo="DRE", grupo=grupo, conta=conta, valor=valor))


def _bp(emp, per, gran, ativo):
    for grupo, conta in (("TOTAL", "TOTAL DO ATIVO"), ("TOTAL", "TOTAL DO PASSIVO")):
        LINHAS.append(dict(empresa=emp, periodo=per, gran=gran, tipo="BP", grupo=grupo, conta=conta, valor=ativo))


def _montar_banco():
    LINHAS.clear()
    ITENS_ADMIN.clear()
    # ENERGIA 06/2026 trimestral (oraculo)
    _dre("ENERGIA", P_JUN, "trimestral", 17380604.41, -1804157.20, -636411.11, 33116.50, -352902.22, -1804157.20, adm=-1000000.0)
    _bp("ENERGIA", P_JUN, "trimestral", 40_000_000.0)
    # ENERGIA 06/2026 semestral (oraculo)
    _dre("ENERGIA", P_JUN, "semestral", 33350846.80, -1106049.63, -1195046.22, 55548.53, -691527.25, -1337674.90, adm=-2000000.0)
    _bp("ENERGIA", P_JUN, "semestral", 43_359_080.94)
    # ENERGIA 03/2026 trimestral = semestral - 2T (consistente com o oraculo) -> EBITDA 1.572.935,68
    _dre("ENERGIA", P_MAR, "trimestral", 15970242.39, 698107.57, -558635.11, 22432.03, -338625.03, 466482.30, adm=-900000.0)
    _bp("ENERGIA", P_MAR, "trimestral", 39_000_000.0)
    # ENERGIA 12/2025 semestral e 12/2024 anual: NAO podem ser usados como "anterior" do trimestral
    _dre("ENERGIA", P_DEZ25, "semestral", 31_200_000.0, 500_000.0, -1_000_000.0, 50_000.0, -600_000.0, -640_000.0, adm=-1800000.0)
    _bp("ENERGIA", P_DEZ25, "semestral", 41_700_000.0)
    _dre("ENERGIA", P_DEZ24, "anual", 60_000_000.0, 900_000.0, -2_000_000.0, 80_000.0, -1_200_000.0, 400_000.0, adm=-3000000.0)
    _bp("ENERGIA", P_DEZ24, "anual", 38_000_000.0)
    # Demais empresas: so' trimestral em 06/2026. Componentes (DF -5000, RF 1000, dep -10000) escolhidos
    # de modo que EBITDA = LO - (DF+RF) - dep bata exatamente com o oraculo: LO = EBITDA + (DF+RF) + dep.
    oraculo = {
        "CONST": (6004883.69, 2319740.04, 2105771.09),
        "ENG": (18266.69, -1036441.72, -1040039.18),
        "RENOV": (0.0, -191306.52, -200082.17),
        "SMG": (448790.60, 73921.45, 49104.40),
        "SOL": (21481.06, -334221.63, -337088.04),
    }
    for emp, (rec, ebitda, ll) in oraculo.items():
        lo = round(ebitda + (-5000.0 + 1000.0) + (-10000.0), 2)
        _dre(emp, P_JUN, "trimestral", rec, lo, -5000.0, 1000.0, -10000.0, ll)
        _bp(emp, P_JUN, "trimestral", 1_000_000.0)
    # itens de despesas administrativas por documento (periodo + granularidade)
    ITENS_ADMIN[("ENERGIA", P_JUN, "trimestral")] = [("Serviços Profissionais", -600000.0), ("Aluguéis", -400000.0)]
    ITENS_ADMIN[("ENERGIA", P_JUN, "semestral")] = [("Serviços Profissionais", -1200000.0), ("Aluguéis", -800000.0)]


def _ativos(status="ATIVO"):
    return LINHAS if status == "ATIVO" else []


class FakeDB:
    """Implementacao em memoria de tudo que dados_relatorio_comentado lê do db."""

    @staticmethod
    def listar_empresas(conn):
        return EMPRESAS

    @staticmethod
    def listar_periodos_detalhado(conn, cod, status="ATIVO"):
        pares = sorted({(l["periodo"], l["gran"]) for l in _ativos(status) if l["empresa"] == cod}, reverse=True)
        return [{"periodo": p, "granularidade": g_} for p, g_ in pares]

    @staticmethod
    def listar_periodos(conn, cod, status="ATIVO"):
        return sorted({l["periodo"] for l in _ativos(status) if l["empresa"] == cod}, reverse=True)

    @staticmethod
    def listar_periodos_grupo(conn, cods, status="ATIVO"):
        return sorted({l["periodo"] for l in _ativos(status) if l["empresa"] in cods}, reverse=True)

    @staticmethod
    def listar_lancamentos(conn, cod, periodo, tipo, status="ATIVO", granularidade=None):
        return [
            dict(grupo=l["grupo"], conta=l["conta"], valor=l["valor"])
            for l in _ativos(status)
            if l["empresa"] == cod and l["periodo"] == periodo and l["tipo"] == tipo
            and (granularidade is None or l["gran"] == granularidade)
        ]

    @staticmethod
    def listar_historico_grupo(conn, cod, tipo, status="ATIVO", granularidade=None):
        return [
            dict(periodo=l["periodo"], grupo=l["grupo"], conta=l["conta"], valor=l["valor"], granularidade=l["gran"])
            for l in _ativos(status)
            if l["empresa"] == cod and l["tipo"] == tipo and (granularidade is None or l["gran"] == granularidade)
        ]

    @staticmethod
    def listar_lancamentos_grupo_periodos(conn, periodos, tipo, cods, status="ATIVO", granularidade=None):
        return [
            dict(empresa_codigo=l["empresa"], periodo=l["periodo"], grupo=l["grupo"], conta=l["conta"],
                 valor=l["valor"], granularidade=l["gran"])
            for l in _ativos(status)
            if l["periodo"] in periodos and l["tipo"] == tipo and l["empresa"] in cods
            and (granularidade is None or l["gran"] == granularidade)
        ]

    @staticmethod
    def listar_lancamentos_grupo(conn, periodo, tipo, cods, status="ATIVO", granularidade=None):
        return [
            dict(empresa_codigo=l["empresa"], grupo=l["grupo"], conta=l["conta"], valor=l["valor"])
            for l in _ativos(status)
            if l["periodo"] == periodo and l["tipo"] == tipo and l["empresa"] in cods
            and (granularidade is None or l["gran"] == granularidade)
        ]

    @staticmethod
    def listar_despesas_admin_itens(conn, cod, periodo, granularidade=None):
        if granularidade is None:
            for (e, p, _g), itens in ITENS_ADMIN.items():
                if e == cod and p == periodo:
                    return itens
            return []
        return ITENS_ADMIN.get((cod, periodo, granularidade), [])


@pytest.fixture(autouse=True)
def banco():
    _montar_banco()
    alvos = [
        "listar_empresas", "listar_periodos_detalhado", "listar_periodos", "listar_periodos_grupo",
        "listar_lancamentos", "listar_historico_grupo", "listar_lancamentos_grupo_periodos",
        "listar_lancamentos_grupo", "listar_despesas_admin_itens",
    ]
    patches = [patch(f"dados_relatorio_comentado.db.{n}", side_effect=getattr(FakeDB, n)) for n in alvos]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def _rel(emp, periodo, gran, **kw):
    return drc.montar_dados_relatorio(object(), emp, periodo, periodo_label="t", granularidade=gran, **kw)[0]


# =========================================================== 1 empresa
def test_energia_trimestral_ebitda_e_margens_do_proprio_trimestral():
    d = _rel("ENERGIA", P_JUN, "trimestral")
    assert d["receita_liquida"] == pytest.approx(17380604.41)
    assert d["ebitda"] == pytest.approx(-847960.37, abs=0.01)
    assert d["margem_ebitda"] == pytest.approx(-847960.37 / 17380604.41, abs=1e-6)
    assert round(d["margem_ebitda"] * 100, 2) == -4.88
    assert round(d["margem_liquida"] * 100, 2) == -10.38
    assert d["resultado_liquido"] == pytest.approx(-1804157.20)
    # a propria decomposicao do PDF fecha: -1.804.157,20 + 603.294,61 + 352.902,22 = -847.960,37
    assert d["resultado_financeiro"] == pytest.approx(603294.61, abs=0.01)
    assert d["deprec_amortiz"] == pytest.approx(352902.22, abs=0.01)
    assert (d["resultado_liquido"] + d["resultado_financeiro"] + d["deprec_amortiz"]) == pytest.approx(d["ebitda"], abs=0.01)
    assert d["granularidade"] == "trimestral"


def test_energia_semestral_continua_724975_31():
    d = _rel("ENERGIA", P_JUN, "semestral")
    assert d["receita_liquida"] == pytest.approx(33350846.80)
    assert d["ebitda"] == pytest.approx(724975.31, abs=0.01)
    assert round(d["margem_ebitda"] * 100, 2) == 2.17
    assert round(d["margem_liquida"] * 100, 1) == -4.0


def test_bug_antigo_reproduzido_sem_granularidade_a_vencedora_mistura():
    """Documenta o bug: calcular_indicadores SEM granularidade (modo legado)
    devolve o EBITDA do semestral mesmo num relatorio trimestral."""
    hist_bp = FakeDB.listar_historico_grupo(None, "ENERGIA", "BP")
    hist_dre = FakeDB.listar_historico_grupo(None, "ENERGIA", "DRE")
    legado = indicadores.calcular_indicadores(hist_bp, hist_dre)
    import pandas as pd
    assert legado.loc[pd.Timestamp(P_JUN), "EBITDA"] == pytest.approx(724975.31, abs=0.01)  # semestral "vencedora"
    exato = indicadores.calcular_indicadores(hist_bp, hist_dre, granularidade="trimestral")
    assert exato.loc[pd.Timestamp(P_JUN), "EBITDA"] == pytest.approx(-847960.37, abs=0.01)


def test_periodo_anterior_so_da_mesma_granularidade():
    # TRIMESTRAL 06/2026: anterior = trimestral 03/2026 (nao o semestral 12/2025 nem o anual 12/2024)
    d = _rel("ENERGIA", P_JUN, "trimestral")
    assert d["periodo_anterior"] == P_MAR
    # receita 17.380.604,41 vs 15.970.242,39 = +8,8%
    assert "+8.8%" in d["complemento_receita"], d["complemento_receita"]
    # EBITDA -847.960,37 vs 1.572.935,68 = -153,9%
    assert "-153.9%" in d["complemento_ebitda"], d["complemento_ebitda"]


def test_semestral_sem_semestral_anterior_nao_compara_com_trimestral_nem_anual():
    # semestral 06/2026: existe semestral 12/2025 -> usa esse
    d = _rel("ENERGIA", P_JUN, "semestral")
    assert d["periodo_anterior"] == P_DEZ25
    # ANUAL 12/2024: nao ha anual anterior, mesmo havendo trimestral/semestral antes -> fallback
    d_anual = _rel("ENERGIA", P_DEZ24, "anual")
    assert d_anual["periodo_anterior"] is None
    assert "sem período anterior disponível" in d_anual["complemento_ebitda"]
    # e o trimestral mais antigo (03/2026) nao tem trimestral anterior -> fallback, apesar de haver 12/2025 semestral
    d_mar = _rel("ENERGIA", P_MAR, "trimestral")
    assert d_mar["periodo_anterior"] is None
    assert "sem período anterior disponível" in d_mar["complemento_receita"]


def test_ranking_de_despesas_vem_do_documento_certo():
    d_t = _rel("ENERGIA", P_JUN, "trimestral")
    d_s = _rel("ENERGIA", P_JUN, "semestral")
    assert round(sum(i[1] for i in d_t["despesas_admin_itens"])) == 1000000
    assert round(sum(i[1] for i in d_s["despesas_admin_itens"])) == 2000000
    assert d_t["avisos"] == [] and d_s["avisos"] == []


def test_ranking_de_outro_documento_e_descartado_com_aviso():
    # so' existe o ranking do semestral (legado: gravado sem granularidade) -> o trimestral NAO pode usa-lo
    ITENS_ADMIN.clear()
    ITENS_ADMIN[("ENERGIA", P_JUN, "")] = [("Serviços Profissionais", -1200000.0), ("Aluguéis", -800000.0)]
    d_t = _rel("ENERGIA", P_JUN, "trimestral")
    assert d_t["despesas_admin_itens"] == []
    assert d_t["avisos"] and "Ranking de despesas administrativas" in d_t["avisos"][0]
    # ja o legado que bate com o total do documento e' aceito
    ITENS_ADMIN.clear()
    ITENS_ADMIN[("ENERGIA", P_JUN, "")] = [("Serviços Profissionais", -600000.0), ("Aluguéis", -400000.0)]
    d_t2 = _rel("ENERGIA", P_JUN, "trimestral")
    assert round(sum(i[1] for i in d_t2["despesas_admin_itens"])) == 1000000 and d_t2["avisos"] == []


# =========================================================== GRUPO
def test_grupo_trimestral_ebitda_menos_16268_75():
    d = _rel(TODAS, P_JUN, "trimestral")
    assert d["ebitda"] == pytest.approx(-16268.75, abs=0.01)
    assert d["receita_liquida"] == pytest.approx(23874026.45, abs=0.01)
    assert d["resultado_liquido"] == pytest.approx(-1226491.10, abs=0.01)
    assert d["empresas_codigos"] == TODAS
    # sanidade: a soma dos EBITDA das 6 empresas (cada uma no seu proprio trimestral)
    soma = sum(_rel(c, P_JUN, "trimestral")["ebitda"] for c in TODAS)
    assert soma == pytest.approx(-16268.75, abs=0.01)


def test_grupo_semestral_falha_com_mensagem_clara_pois_so_a_energia_tem():
    with pytest.raises(ValueError) as exc:
        _rel(TODAS, P_JUN, "semestral")
    msg = str(exc.value)
    assert "semestral" in msg and "SMG" in msg


def test_grupo_nao_usa_semestral_da_energia_no_historico_consolidado():
    hist_dre = drc._consolidar_historico(object(), TODAS, "DRE", granularidade="trimestral")
    assert {r["granularidade"] for r in hist_dre} == {"trimestral"}
    # 03/2026 so' a Energia tem trimestral -> periodo incompleto nao entra no historico do consolidado
    assert P_MAR not in {r["periodo"] for r in hist_dre}
    assert P_JUN in {r["periodo"] for r in hist_dre}


def test_grupo_sem_periodo_anterior_completo_cai_no_fallback():
    d = _rel(TODAS, P_JUN, "trimestral")
    assert d["periodo_anterior"] is None
    assert "sem período anterior disponível" in d["complemento_ebitda"]


# =========================================================== COMPARATIVO
def test_comparativo_2_periodos_mesmo_fim_com_granularidades_diferentes_tem_ebitda_proprio():
    dados = drc.montar_dados_relatorio_comparativo(
        object(), ["ENERGIA"], [P_JUN, P_JUN], ["2T26", "1S26"], "2T26 A 1S26",
        granularidades=["trimestral", "semestral"],
    )
    fluxo = {k["label"]: k["valores"] for k in dados["kpis_fluxo"]}
    assert fluxo["EBITDA"] == pytest.approx([-847960.37, 724975.31], abs=0.01)
    assert fluxo["Receita Operacional Líquida"] == pytest.approx([17380604.41, 33350846.80], abs=0.01)
    assert fluxo["Resultado Líquido"] == pytest.approx([-1804157.20, -1337674.90], abs=0.01)
    # grafico de evolucao usa os mesmos valores (as 2 barras de 06/2026 NAO podem ter EBITDA igual)
    graf = {m["label"]: m["valores"] for m in dados["grafico_evolucao_metricas"]}
    assert graf["EBITDA"][0] != graf["EBITDA"][1]


def test_comparativo_rejeita_o_mesmo_item_duas_vezes():
    # v0.40.0: (periodo + granularidade) repetido gerava 2 colunas identicas
    # (PDFs de 01/10/2026) -- agora e' erro claro. A soma do EBITDA do grupo
    # trimestral (-16.268,75) e' conferida em tests/test_integracao_granularidade_pg.py.
    with pytest.raises(ValueError) as exc:
        drc.montar_dados_relatorio_comparativo(
            object(), TODAS, [P_JUN, P_JUN], ["a", "b"], "a A b", granularidades=["trimestral", "trimestral"],
        )
    assert "mais de uma vez" in str(exc.value)


def test_comparativo_grupo_com_semestral_so_da_energia_falha_claro():
    with pytest.raises(ValueError) as exc:
        drc.montar_dados_relatorio_comparativo(
            object(), TODAS, [P_JUN, P_JUN], ["a", "b"], "a A b", granularidades=["trimestral", "semestral"],
        )
    assert "semestral" in str(exc.value)


# =========================================================== PDFs reais
def test_pdfs_reais_nao_quebram_e_carregam_a_base_certa():
    d, incluir = drc.montar_dados_relatorio(object(), "ENERGIA", P_JUN, periodo_label="t", granularidade="trimestral")
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "x.pdf")
        g.gerar_pdf_completo(d, caminho, incluir_pagina_resultado=incluir)
        assert os.path.getsize(caminho) > 5000
        dados_b = drc.montar_dados_relatorio_comparativo(
            object(), ["ENERGIA"], [P_JUN, P_JUN], ["2T26", "1S26"], "2T26 A 1S26",
            granularidades=["trimestral", "semestral"],
        )
        caminho_b = os.path.join(tmp, "y.pdf")
        gc.gerar_pdf_comparativo(dados_b, caminho_b)
        assert os.path.getsize(caminho_b) > 5000
