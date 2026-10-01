# -*- coding: utf-8 -*-
"""
Graficos da Visao Grupo (01/10/2026, "faz um dash com leitura boa, dinamica,
fluida e compreensivel facilmente"). Funcoes puras: recebem DataFrames ja
calculados (visao_grupo.py / indicadores.py) e devolvem `plotly` Figure --
sem Streamlit, sem banco. Testadas em tests/test_visao_grupo_graficos.py.

Regras de design (skill dataviz): 1 eixo por grafico (nunca eixo duplo);
cor identifica a SERIE (azul = receita, laranja = EBITDA, verde-agua =
resultado), nunca o valor; negativo e' mostrado pelo sinal/posicao da barra
(abaixo do zero) e, nos rankings, pela cor laranja; fundo transparente (herda
o tema claro/escuro do Streamlit); legenda sempre presente com 2+ series;
rotulo de valor so' quando cabe (poucos periodos/barras); hover com valor
completo em R$ no padrao BR.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

import formatacao

# Paleta categorica validada (skill dataviz, references/palette.md) -- passos
# claro / escuro. Slot 1 azul, 2 laranja, 3 verde-agua.
_CAT = {
    "light": {"azul": "#2a78d6", "laranja": "#eb6834", "aqua": "#1baf7a"},
    "dark": {"azul": "#3987e5", "laranja": "#d95926", "aqua": "#199e70"},
}
COR_REF = {"light": "#8a8985", "dark": "#9a9a95"}  # linha de referencia (zero, 1,0x)


def cores(dark: bool) -> dict:
    return _CAT["dark" if dark else "light"]


def _base(fig: go.Figure, dark: bool, altura: int = 330, legenda: bool = True) -> go.Figure:
    fig.update_layout(
        height=altura, margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=legenda, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hoverlabel=dict(font_size=13), bargap=0.28, bargroupgap=0.08,
        separators=",.",  # pt-BR: decimal ',' e milhar '.' nos eixos
    )
    fig.update_xaxes(showgrid=False, zeroline=False, fixedrange=True)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.18)", zeroline=True, zerolinecolor=COR_REF["dark" if dark else "light"],
                     zerolinewidth=1, fixedrange=True)
    return fig


def _ticks_moeda(valores) -> tuple[list[float], list[str]]:
    """Marcas do eixo Y em R$ com 'mil'/'mi'/'bi' (pt-BR) -- o ~s do plotly
    escreve '10M'/'5k'. Passo "bonito" (1/2/5 x 10^n), ~5 marcas, sempre
    incluindo o zero quando o intervalo o atravessa."""
    import math
    vals = [float(v) for v in valores if v is not None and not pd.isna(v)]
    if not vals:
        return [], []
    lo, hi = min(min(vals), 0.0), max(max(vals), 0.0)
    if hi == lo:
        hi = lo + 1.0
    bruto = (hi - lo) / 5.0
    pot = 10 ** math.floor(math.log10(bruto))
    passo = next(m * pot for m in (1, 2, 5, 10) if m * pot >= bruto)
    ini = math.floor(lo / passo) * passo
    ticks, v = [], ini
    while v <= hi + passo * 0.5 and len(ticks) < 14:
        ticks.append(round(v, 2))
        v += passo

    def rot(x: float) -> str:
        if x == 0:
            return "0"
        return formatacao.moeda_curta(x).replace("R$ ", "")

    return ticks, [rot(t) for t in ticks]


def rotulos_periodos(periodos, granularidade: str) -> list[str]:
    import selecao_periodos
    return [selecao_periodos.rotulo_coluna_padrao(pd.Timestamp(p).date(), granularidade) for p in periodos]


def fig_resultado_por_periodo(serie_dre: pd.DataFrame, ind_grupo: pd.DataFrame, granularidade: str, dark: bool) -> go.Figure:
    """Barras agrupadas por periodo: Receita líquida, EBITDA e Resultado líquido
    (mesma unidade R$, 1 eixo)."""
    c = cores(dark)
    idx = list(serie_dre.index)
    x = rotulos_periodos(idx, granularidade)
    series = [
        ("Receita líquida", serie_dre["RECEITA OPERACIONAL LIQUIDA"].tolist(), c["azul"]),
        ("EBITDA", ind_grupo["EBITDA"].reindex(serie_dre.index).tolist(), c["laranja"]),
        ("Resultado líquido", serie_dre["LUCRO LIQUIDO DO EXERCICIO"].tolist(), c["aqua"]),
    ]
    fig = go.Figure()
    rotular = len(x) <= 3
    for nome, vals, cor in series:
        txt = [formatacao.moeda_curta(v) for v in vals]
        fig.add_bar(
            x=x, y=[None if pd.isna(v) else v for v in vals], name=nome, marker_color=cor,
            marker_line_width=0, text=txt if rotular else None, textposition="outside", cliponaxis=False,
            customdata=[formatacao.moeda_br(v) for v in vals],
            hovertemplate=f"<b>{nome}</b><br>%{{x}}: %{{customdata}}<extra></extra>",
        )
    fig.update_layout(barmode="group")
    tv, tt = _ticks_moeda([v for _n, vals, _c in series for v in vals])
    fig.update_yaxes(tickvals=tv, ticktext=tt, title=None)
    return _base(fig, dark)


def fig_margens(ind_grupo: pd.DataFrame, granularidade: str, dark: bool) -> go.Figure:
    """Linhas de Margem EBITDA e Margem líquida (%), rótulo no último ponto.
    A margem bruta (~80%+) fica de fora de propósito: na mesma escala achatava
    as outras duas contra o zero -- ela aparece como cartão no 'Rentabilidade'."""
    c = cores(dark)
    x = rotulos_periodos(list(ind_grupo.index), granularidade)
    fig = go.Figure()
    for nome, col, cor, pos in (("Margem EBITDA", "Margem EBITDA", c["laranja"], "top center"),
                                 ("Margem líquida", "Margem Líquida", c["aqua"], "bottom center")):
        vals = (ind_grupo[col] * 100.0).tolist()
        textos = [""] * len(vals)
        for i in range(len(vals) - 1, -1, -1):  # rotulo so' no ultimo ponto com dado
            if not pd.isna(vals[i]):
                textos[i] = f"{vals[i]:.1f}%".replace(".", ",")
                break
        fig.add_scatter(
            x=x, y=[None if pd.isna(v) else v for v in vals], name=nome, mode="lines+markers+text",
            line=dict(color=cor, width=2), marker=dict(size=8, color=cor),
            text=textos, textposition=pos, cliponaxis=False,
            customdata=[formatacao.pct_br(v / 100.0) if not pd.isna(v) else "—" for v in vals],
            hovertemplate=f"<b>{nome}</b><br>%{{x}}: %{{customdata}}<extra></extra>",
        )
    fig.update_yaxes(ticksuffix="%")
    return _base(fig, dark)


def fig_contribuicao(resumo_empresas: pd.DataFrame, coluna: str, nome_por_cod: dict, dark: bool) -> go.Figure:
    """Barras horizontais, 1 por empresa, ordenadas: quanto cada empresa
    contribui na métrica escolhida. Negativo = laranja (e fica à esquerda do zero)."""
    c = cores(dark)
    df = resumo_empresas[coluna].dropna().sort_values()
    nomes = [nome_por_cod.get(i, i) for i in df.index]
    valores = df.tolist()
    moeda = coluna != "Margem EBITDA"
    textos = [formatacao.moeda_curta(v) if moeda else formatacao.pct_br(v) for v in valores]
    completo = [formatacao.moeda_br(v) if moeda else formatacao.pct_br(v) for v in valores]
    fig = go.Figure(go.Bar(
        x=valores, y=nomes, orientation="h", marker_color=[c["azul"] if v >= 0 else c["laranja"] for v in valores],
        text=textos, textposition="outside", cliponaxis=False, customdata=completo,
        hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>",
    ))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    fig.add_vline(x=0, line_width=1, line_color=COR_REF["dark" if dark else "light"])
    return _base(fig, dark, altura=max(220, 56 * len(nomes) + 60), legenda=False)


def fig_capital_giro(ind_grupo: pd.DataFrame, granularidade: str, dark: bool) -> go.Figure:
    c = cores(dark)
    x = rotulos_periodos(list(ind_grupo.index), granularidade)
    vals = ind_grupo["Capital de Giro"].tolist()
    fig = go.Figure(go.Bar(
        x=x, y=[None if pd.isna(v) else v for v in vals], name="Capital de giro",
        marker_color=[c["azul"] if (not pd.isna(v) and v >= 0) else c["laranja"] for v in vals],
        text=[formatacao.moeda_curta(v) for v in vals] if len(x) <= 6 else None, textposition="outside", cliponaxis=False,
        customdata=[formatacao.moeda_br(v) for v in vals],
        hovertemplate="<b>Capital de giro</b><br>%{x}: %{customdata}<extra></extra>",
    ))
    tv, tt = _ticks_moeda(vals)
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark, altura=290, legenda=False)


def fig_linha_unica(serie: pd.Series, nome: str, granularidade: str, dark: bool, formato: str = "x",
                    referencia: float | None = None) -> go.Figure:
    """Uma linha (ex.: liquidez corrente 'x' ou endividamento '%'), com linha
    de referência opcional (ex.: 1,0x) e rótulo no último ponto."""
    c = cores(dark)
    x = rotulos_periodos(list(serie.index), granularidade)
    vals = serie.tolist()
    if formato == "pct":
        plot = [None if pd.isna(v) else v * 100.0 for v in vals]
        completo = [formatacao.pct_br(v) for v in vals]
    else:
        plot = [None if pd.isna(v) else v for v in vals]
        completo = [formatacao.numero_br(v, sufixo="x") for v in vals]
    textos = [""] * len(vals)
    for i in range(len(vals) - 1, -1, -1):
        if not pd.isna(vals[i]):
            textos[i] = completo[i]
            break
    fig = go.Figure(go.Scatter(
        x=x, y=plot, name=nome, mode="lines+markers+text", line=dict(color=c["azul"], width=2),
        marker=dict(size=8, color=c["azul"]), text=textos, textposition="top center", cliponaxis=False,
        customdata=completo, hovertemplate=f"<b>{nome}</b><br>%{{x}}: %{{customdata}}<extra></extra>",
    ))
    if referencia is not None:
        ref = referencia * 100.0 if formato == "pct" else referencia
        fig.add_hline(y=ref, line_dash="dot", line_width=1, line_color=COR_REF["dark" if dark else "light"],
                      annotation_text=("referência " + (f"{referencia:.0%}" if formato == "pct" else f"{referencia:.1f}x".replace(".", ","))),
                      annotation_position="bottom right")
    if formato == "pct":
        fig.update_yaxes(ticksuffix="%")
    return _base(fig, dark, altura=290, legenda=False)
