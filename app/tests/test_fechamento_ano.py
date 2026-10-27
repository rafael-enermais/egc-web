# -*- coding: utf-8 -*-
"""v0.54.0 -- granularidade '9meses' e logica pura do Fechamento do ano (grade, sugestoes, conferencias)."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dados_relatorio_comentado as drc  # noqa: E402
import fechamento_ano as fa  # noqa: E402
import importacoes_ui  # noqa: E402
import indicadores  # noqa: E402
import selecao_periodos as sp  # noqa: E402
from parser_egc import calcular_granularidade  # noqa: E402


# ───────────── 9 meses como granularidade ─────────────
@pytest.mark.parametrize("ini,fim,esperado", [
    ("01/01/2026", "30/09/2026", "9meses"),   # caso real ENG 09/2026 (273 dias)
    ("01/04/2026", "31/12/2026", "9meses"),   # 275 dias
    ("01/01/2026", "30/06/2026", "semestral"),
    ("01/07/2026", "30/09/2026", "trimestral"),
    ("01/01/2026", "31/12/2026", "anual"),     # exercicio continua anual
    ("01/01/2026", "31/10/2026", "anual"),     # 10 meses: comportamento antigo preservado
])
def test_calcular_granularidade_9meses(ini, fim, esperado):
    assert calcular_granularidade(ini, fim) == esperado


def test_9meses_nos_rotulos_e_prioridade():
    assert sp.MESES_GRANULARIDADE["9meses"] == 9
    assert sp.rotulo_granularidade("9meses") == "Acumulado 9 meses"
    assert sp.rotulo_coluna_padrao(date(2026, 9, 30), "9meses") == "9M/2026"
    assert indicadores.NOME_GRANULARIDADE["9meses"] == "Acumulado 9 meses"
    p = indicadores._PRIORIDADE_GRANULARIDADE
    assert p["anual"] > p["9meses"] > p["semestral"]
    assert "9 meses" in drc.periodo_extenso_padrao(date(2026, 9, 30), "9meses")


def test_inferir_9meses_pelo_nome_do_arquivo():
    f = importacoes_ui.inferir_granularidade_nome
    assert f("ENG - DRE - Acumulado 9M 2026.pdf") == "9meses"
    assert f("ENG - DRE - Nove meses 2026.pdf") == "9meses"
    assert f("ENG - Balanço - 2º Trimestre 2026.pdf") == "trimestral"


def test_periodo_anterior_imediato_9meses_exige_9_meses_de_distancia():
    ant, ign = drc._periodo_anterior_imediato([date(2025, 9, 30)], date(2026, 9, 30), "9meses")
    assert ant is None and ign == date(2025, 9, 30)  # 12 meses de distancia != 9


# ───────────── grade ─────────────
def _doc(emp, fim, gran, bp=True, dre=True, ini=None):
    return fa.Doc(emp, fim, gran, bp, dre, ini)


def test_slots_do_ano_sao_8_e_9m_fecha_em_setembro():
    s = {x.chave: x for x in fa.slots_do_ano(2026)}
    assert list(s) == ["1T", "2T", "3T", "4T", "1S", "2S", "9M", "ANUAL"]
    assert s["9M"].par == (date(2026, 9, 30), "9meses")
    assert s["1S"].par == (date(2026, 6, 30), "semestral") and s["2T"].par == (date(2026, 6, 30), "trimestral")


def test_grade_estados_e_lacunas():
    docs = [
        _doc("ENG", date(2026, 3, 31), "trimestral", ini=date(2026, 1, 1)),
        _doc("ENG", date(2026, 6, 30), "trimestral", bp=False, ini=date(2026, 4, 1)),
        _doc("ENG", date(2026, 6, 30), "semestral", dre=False),
    ]
    g = fa.montar_grade(2026, ["ENG", "SOL"], docs)
    assert g[("ENG", "1T")].estado == fa.ESTADO_COMPLETO
    assert g[("ENG", "2T")].estado == fa.ESTADO_SO_DRE
    assert g[("ENG", "1S")].estado == fa.ESTADO_SO_BP
    assert g[("ENG", "3T")].estado == fa.ESTADO_AUSENTE
    assert all(g[("SOL", k)].estado == fa.ESTADO_AUSENTE for k in ("1T", "2T", "9M", "ANUAL"))
    r = fa.resumo_por_empresa(g, ["ENG", "SOL"])
    assert r["ENG"] == {"completos": 1, "parciais": 2, "faltando": 5} and r["SOL"]["faltando"] == 8


def test_caso_eng_09_2026_rotulo_trimestral_com_intervalo_de_9_meses():
    docs = [_doc("ENG", date(2026, 9, 30), "trimestral", ini=date(2026, 1, 1))]
    g = fa.montar_grade(2026, ["ENG"], docs)
    assert "9meses" in g[("ENG", "3T")].suspeita
    assert g[("ENG", "9M")].estado == fa.ESTADO_AUSENTE
    [sg] = fa.sugestoes_correcao(2026, ["ENG"], docs)
    assert (sg.empresa, sg.fim, sg.gravada, sg.declarada) == ("ENG", date(2026, 9, 30), "trimestral", "9meses")


def test_sem_sugestao_quando_rotulo_bate_ou_pdf_nao_declarou_intervalo():
    docs = [
        _doc("ENG", date(2026, 9, 30), "9meses", ini=date(2026, 1, 1)),
        _doc("ENG", date(2026, 6, 30), "semestral", ini=None),
    ]
    assert fa.sugestoes_correcao(2026, ["ENG"], docs) == []
    assert all(not c.suspeita for c in fa.montar_grade(2026, ["ENG"], docs).values())


# ───────────── conferencias (so' aviso) ─────────────
def _v(rb, rl, lb, ll):
    return {"RECEITA OPERACIONAL BRUTA": rb, "RECEITA OPERACIONAL LIQUIDA": rl, "LUCRO BRUTO": lb,
            "LUCRO LIQUIDO DO EXERCICIO": ll}


def test_conferencia_ok_nao_avisa():
    vals = {("ENG", "1T"): _v(110, 100, 40, 10), ("ENG", "2T"): _v(220, 200, 80, 20),
            ("ENG", "1S"): _v(330, 300, 120, 30)}
    assert fa.conferir_composicoes(2026, vals) == []


def test_conferencia_divergente_avisa_sem_levantar():
    vals = {("ENG", "1T"): _v(110, 100, 40, 10), ("ENG", "2T"): _v(220, 200, 80, 20),
            ("ENG", "1S"): _v(330, 350, 120, 25)}
    av = fa.conferir_composicoes(2026, vals)
    assert {(a.todo, a.conta) for a in av} == {("1S", "RECEITA OPERACIONAL LIQUIDA"), ("1S", "LUCRO LIQUIDO DO EXERCICIO")}
    a = next(x for x in av if x.conta == "RECEITA OPERACIONAL LIQUIDA")
    assert a.soma == 300 and a.declarado == 350 and a.diferenca == -50
    assert "1T + 2T" in a.mensagem and "só aviso" in a.mensagem


def test_conferencia_so_compara_quando_todas_as_partes_existem_e_respeita_tolerancia():
    # falta o 2T -> nada a comparar
    assert fa.conferir_composicoes(2026, {("ENG", "1T"): _v(1, 1, 1, 1), ("ENG", "1S"): _v(9, 9, 9, 9)}) == []
    # diferenca de centavos (arredondamento) fica dentro da tolerancia
    vals = {("ENG", "1T"): _v(0, 100.00, 0, 0), ("ENG", "2T"): _v(0, 200.00, 0, 0), ("ENG", "1S"): _v(0, 300.40, 0, 0)}
    assert fa.conferir_composicoes(2026, vals) == []


def test_conferencia_9m_e_anual():
    vals = {("ENG", "1S"): _v(0, 300, 0, 0), ("ENG", "3T"): _v(0, 100, 0, 0), ("ENG", "9M"): _v(0, 400, 0, 0),
            ("ENG", "4T"): _v(0, 100, 0, 0), ("ENG", "ANUAL"): _v(0, 999, 0, 0)}
    av = fa.conferir_composicoes(2026, vals)
    assert [(a.todo, a.partes) for a in av] == [("ANUAL", ("9M", "4T"))]


def test_valores_de_linhas_ignora_documento_fora_da_grade_e_conta_ambigua():
    linhas = [
        {"empresa_codigo": "ENG", "periodo": date(2026, 9, 30), "granularidade": "9meses",
         "conta": "LUCRO BRUTO", "valor": 5, "n": 1},
        {"empresa_codigo": "ENG", "periodo": date(2026, 9, 30), "granularidade": "9meses",
         "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": 5, "n": 2},   # ambigua
        {"empresa_codigo": "ENG", "periodo": date(2026, 9, 30), "granularidade": "mensal",
         "conta": "LUCRO BRUTO", "valor": 5, "n": 1},                   # nao e' um fechamento da grade
    ]
    assert fa.valores_de_linhas(2026, linhas) == {("ENG", "9M"): {"LUCRO BRUTO": 5.0}}


# ───────────── atalhos ─────────────
def test_preset_so_inclui_fechamentos_completos_em_todas_as_empresas():
    docs = [_doc(e, date(2026, m, 30 if m != 3 else 31), "trimestral") for e in ("ENG", "SOL") for m in (3, 6)]
    docs.append(_doc("ENG", date(2026, 9, 30), "trimestral"))
    g = fa.montar_grade(2026, ["ENG", "SOL"], docs)
    pares, faltando = fa.pares_do_preset("Trimestres até hoje (acumulado)", 2026, ["ENG", "SOL"], g)
    assert pares == [(date(2026, 3, 31), "trimestral"), (date(2026, 6, 30), "trimestral")]
    assert faltando == ["SOL 3T"]
