# -*- coding: utf-8 -*-
"""
Motor de projecao (BP/DRE) -- modulo puro, sem Streamlit nem banco (mesma
filosofia de parser_egc.py/validacoes.py: testavel isolado com dado
sintetico, sem mock de conexao). db.py cuida de ler o historico e gravar
o resultado; app/telas/5_Dashboard_Projecao.py cuida da UI.

Premissa testada em 22/09/2026 (Visao Grupo, uniao de periodos entre as
6 empresas): hoje so existe 1 periodo ativo no banco real. Nao da pra
fazer tendencia/sazonalidade de verdade com 1 ponto -- por isso o
metodo escolhido por conta e automatico, conforme o tamanho real do
historico disponivel, e cada linha devolvida carrega o metodo usado
("flat_ultimo_valor", "tendencia_linear" ou "tendencia_sazonal") pra
nunca apresentar uma extrapolacao como mais confiavel do que o dado
real sustenta. O nivel sobe sozinho conforme mais periodo for
importado -- nenhum ajuste de codigo necessario.

Assume periodos MENSAIS (mesma premissa que o resto do sistema usa em
todo lugar que faz periodo.strftime("%m/%Y")).
"""
from __future__ import annotations

import calendar
from datetime import date

import numpy as np

LIMITE_FLAT = 4       # historico com menos de 4 periodos -> carrega o ultimo valor
LIMITE_SAZONAL = 24   # historico com 24+ periodos (2 anos) -> tendencia + sazonalidade mensal


def proximo_periodo_mensal(periodo: date) -> date:
    """Ultimo dia do mes seguinte ao periodo dado."""
    ano, mes = periodo.year, periodo.month
    mes += 1
    if mes > 12:
        mes = 1
        ano += 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, ultimo_dia)


def gerar_periodos_futuros(ultimo_periodo: date, horizonte: int) -> list[date]:
    """`horizonte` proximos periodos mensais apos `ultimo_periodo`, em ordem."""
    periodos = []
    p = ultimo_periodo
    for _ in range(horizonte):
        p = proximo_periodo_mensal(p)
        periodos.append(p)
    return periodos


def gerar_baseline(historico: list[tuple], periodos_futuros: list) -> list[dict]:
    """
    historico: [(periodo: date, valor: float), ...] de UMA (empresa, tipo,
    grupo, conta) -- qualquer ordem, esta funcao ordena. periodos_futuros:
    datas a projetar (ja em ordem, ver gerar_periodos_futuros).

    Retorna 1 dict por periodo futuro: periodo, valor_base, metodo,
    periodos_historico. Lista vazia se `historico` vier vazio (sem dado
    real nao ha o que projetar).
    """
    if not historico:
        return []

    hist_ordenado = sorted(historico, key=lambda t: t[0])
    valores = [float(v) for _, v in hist_ordenado]
    n = len(valores)

    if n < LIMITE_FLAT:
        metodo = "flat_ultimo_valor"
        base_por_periodo = {p: valores[-1] for p in periodos_futuros}
    elif n < LIMITE_SAZONAL:
        metodo = "tendencia_linear"
        base_por_periodo = _extrapolar_linear(valores, periodos_futuros)
    else:
        metodo = "tendencia_sazonal"
        base_por_periodo = _extrapolar_sazonal(hist_ordenado, valores, periodos_futuros)

    return [
        {
            "periodo": p,
            "valor_base": round(base_por_periodo[p], 2),
            "metodo": metodo,
            "periodos_historico": n,
        }
        for p in periodos_futuros
    ]


def _extrapolar_linear(valores: list[float], periodos_futuros: list) -> dict:
    """Minimos quadrados sobre o indice sequencial do historico (assume espacamento mensal uniforme)."""
    n = len(valores)
    xs = np.arange(n)
    coef = np.polyfit(xs, valores, 1)  # [a, b] de y = a*x + b
    resultado = {}
    for i, p in enumerate(periodos_futuros, start=1):
        x_futuro = n - 1 + i
        resultado[p] = float(coef[0] * x_futuro + coef[1])
    return resultado


def _extrapolar_sazonal(hist_ordenado: list[tuple], valores: list[float], periodos_futuros: list) -> dict:
    """Tendencia linear + media do residuo por mes-do-ano (sazonalidade naive)."""
    n = len(valores)
    xs = np.arange(n)
    coef = np.polyfit(xs, valores, 1)
    tendencia = coef[0] * xs + coef[1]
    residuos = np.array(valores) - tendencia

    residuo_por_mes: dict[int, list[float]] = {}
    for (periodo, _), r in zip(hist_ordenado, residuos):
        residuo_por_mes.setdefault(periodo.month, []).append(float(r))
    media_residuo_por_mes = {m: sum(rs) / len(rs) for m, rs in residuo_por_mes.items()}

    resultado = {}
    for i, p in enumerate(periodos_futuros, start=1):
        x_futuro = n - 1 + i
        tend_futura = coef[0] * x_futuro + coef[1]
        ajuste_sazonal = media_residuo_por_mes.get(p.month, 0.0)
        resultado[p] = float(tend_futura + ajuste_sazonal)
    return resultado


def aplicar_ajustes(baseline: list[dict], ajustes: list[dict]) -> list[dict]:
    """
    baseline: saida de gerar_baseline. ajustes: [{"periodo": date,
    "valor_ajuste": float}, ...] ja filtrados pra esta (empresa, tipo,
    grupo, conta) -- soma todos os ajustes do mesmo periodo. Retorna o
    baseline com valor_ajuste/valor_projetado adicionados (0 quando nao
    ha ajuste pro periodo).
    """
    soma_por_periodo: dict = {}
    for a in ajustes:
        soma_por_periodo[a["periodo"]] = soma_por_periodo.get(a["periodo"], 0.0) + float(a["valor_ajuste"])

    resultado = []
    for linha in baseline:
        ajuste = round(soma_por_periodo.get(linha["periodo"], 0.0), 2)
        resultado.append({
            **linha,
            "valor_ajuste": ajuste,
            "valor_projetado": round(linha["valor_base"] + ajuste, 2),
        })
    return resultado
