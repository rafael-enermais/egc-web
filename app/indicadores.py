# -*- coding: utf-8 -*-
"""
Indicadores contabeis classicos calculados em cima do BP/DRE que ja esta
importado no banco -- pedido do Rafael (22/09/2026): "poderiamos fazer
todos os calculos que forem uteis em cima com funcao contabil, tudo oq
ajudar e saber e interessante". Zero import novo, zero conta nova no
parser -- so' pivota contas que ja sao extraidas hoje (mesma fonte que
Visao Grupo e Dashboard de Projecao, que usava db.listar_historico_grupo
pra montar a serie temporal).

Modulo puro (sem banco/Streamlit), mesmo padrao de projecao.py e
visao_grupo.py: recebe as listas ja buscadas via db.listar_historico_grupo
(1 empresa, todos os periodos, BP e DRE separados) e devolve um
pd.DataFrame indexado por periodo (pd.DatetimeIndex, mesma convencao
anti-bug-de-ordenacao usada no grafico de Visao Grupo/Projecao -- ver
visao_grupo.montar_serie_kpis_grupo).

Liquidez seca (Ativo Circulante - Estoques) / Passivo Circulante NAO
entra: conferido em parser_egc.py (BP_CANDIDATES) -- as 6 empresas do
grupo sao EPC/energia, o parser nunca mapeia conta "ESTOQUES" no BP.
Sem estoque, liquidez seca fica numericamente identica a liquidez
corrente -- indicador redundante, nao construido (simplicidade
operacional).

Contas usadas (todas ja extraidas pelo parser hoje, nenhuma nova):
  BP:  TOTAL DO ATIVO, TOTAL CIRCULANTE ATIVO, TOTAL CIRCULANTE PASSIVO,
       TOTAL NAO CIRCULANTE PASSIVO, TOTAL PATRIMONIO LIQUIDO
  DRE: RECEITA OPERACIONAL LIQUIDA, LUCRO BRUTO, LUCRO LIQUIDO DO EXERCICIO

"TOTAL DO PASSIVO" (que ja existe em egc.v_indicadores) NAO e' usado pra
endividamento -- no BP brasileiro essa linha soma Passivo exigivel +
Patrimonio Liquido (e' por isso que TOTAL DO ATIVO = TOTAL DO PASSIVO
sempre fecha). Endividamento geral usa só o passivo exigivel de verdade
(Circulante + Nao Circulante), sem o PL misturado.
"""
from __future__ import annotations

import pandas as pd

CONTAS_BP = [
    "TOTAL DO ATIVO",
    "TOTAL CIRCULANTE ATIVO",
    "TOTAL CIRCULANTE PASSIVO",
    "TOTAL NAO CIRCULANTE PASSIVO",
    "TOTAL PATRIMONIO LIQUIDO",
]
CONTAS_DRE = [
    "RECEITA OPERACIONAL LIQUIDA",
    "LUCRO BRUTO",
    "LUCRO LIQUIDO DO EXERCICIO",
]

COLUNAS_INDICADORES = [
    "Liquidez Corrente",
    "Capital de Giro",
    "Endividamento Geral",
    "Margem Bruta",
    "Margem Líquida",
    "ROA",
    "ROE",
]


def _pivot(lancamentos: list[dict], contas: list[str]) -> pd.DataFrame:
    """(periodo x conta), valor float, só as contas pedidas. Vazio (sem
    period nenhum) se não tiver dado nenhum bater."""
    if not lancamentos:
        return pd.DataFrame(columns=contas)
    df = pd.DataFrame(lancamentos)
    df = df[df["conta"].isin(contas)]
    if df.empty:
        return pd.DataFrame(columns=contas)
    df["valor"] = df["valor"].astype(float)  # psycopg2 devolve Decimal, nao float
    pivot = df.groupby(["periodo", "conta"])["valor"].sum().unstack("conta")
    return pivot.reindex(columns=contas)


def calcular_indicadores(lancamentos_bp: list[dict], lancamentos_dre: list[dict]) -> pd.DataFrame:
    """
    lancamentos_bp/lancamentos_dre: saida de db.listar_historico_grupo
    (1 empresa, 1 tipo, todos os periodos ATIVO) -- lista de dicts com
    periodo/grupo/conta/valor.

    Devolve DataFrame indexado por pd.DatetimeIndex (nao string -- mesma
    convencao de visao_grupo.montar_serie_kpis_grupo, evita o bug de
    ordenacao alfabetica que o grafico do Dashboard de Projecao teve),
    colunas = COLUNAS_INDICADORES. Vazio se nao tiver periodo nenhum em
    BP nem DRE.
    """
    bp = _pivot(lancamentos_bp, CONTAS_BP)
    dre = _pivot(lancamentos_dre, CONTAS_DRE)

    periodos = sorted(set(bp.index) | set(dre.index))
    if not periodos:
        vazio = pd.DataFrame(columns=COLUNAS_INDICADORES)
        vazio.index = pd.DatetimeIndex([], name="periodo")
        return vazio

    bp = bp.reindex(index=periodos)
    dre = dre.reindex(index=periodos)

    def _div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / b.where(b != 0)

    out = pd.DataFrame(index=periodos)
    ativo_circ = bp["TOTAL CIRCULANTE ATIVO"]
    passivo_circ = bp["TOTAL CIRCULANTE PASSIVO"]
    passivo_exigivel = bp["TOTAL CIRCULANTE PASSIVO"].fillna(0) + bp["TOTAL NAO CIRCULANTE PASSIVO"].fillna(0)
    # se as 2 partes do passivo exigivel vieram NaN nos 2, mantem NaN (sem dado), nao 0
    passivo_exigivel = passivo_exigivel.where(~(bp["TOTAL CIRCULANTE PASSIVO"].isna() & bp["TOTAL NAO CIRCULANTE PASSIVO"].isna()))

    out["Liquidez Corrente"] = _div(ativo_circ, passivo_circ)
    out["Capital de Giro"] = ativo_circ - passivo_circ
    out["Endividamento Geral"] = _div(passivo_exigivel, bp["TOTAL DO ATIVO"])
    out["Margem Bruta"] = _div(dre["LUCRO BRUTO"], dre["RECEITA OPERACIONAL LIQUIDA"])
    out["Margem Líquida"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], dre["RECEITA OPERACIONAL LIQUIDA"])
    out["ROA"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], bp["TOTAL DO ATIVO"])
    out["ROE"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], bp["TOTAL PATRIMONIO LIQUIDO"])

    out.index = pd.to_datetime(out.index)
    out.index.name = "periodo"
    return out
