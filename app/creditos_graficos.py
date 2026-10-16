# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- graficos do dashboard (plotly).  (v0.52.0, 16/10/2026)

Funcoes puras: recebem a serie mensal (creditos_db.serie_mensal) ou tabelas ja calculadas e devolvem `plotly` Figure -- sem Streamlit e sem banco.
Mesmas regras da Visao Grupo (skill dataviz): 1 eixo por grafico (PIS+COFINS somados em R$; nunca eixo duplo), cor = identidade da serie
(paleta categorica validada, slots em ordem fixa), categoria de credito com cor FIXA (nao muda se um mes nao a tem), legenda sempre com 2+
series, rotulo so' quando cabe, hover com valor completo em R$ pt-BR, fundo transparente (herda o tema).
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

import formatacao
from visao_grupo_graficos import COR_REF, _base, _ticks_moeda

_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# categorica validada (skill dataviz references/palette.md): slots 1-8 em ordem fixa; cinza = "outros"
_SLOTS = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
_CINZA = {"light": "#898781", "dark": "#9a9a95"}
# cor FIXA por categoria de credito (nao depende de quantas categorias o mes tem)
ORDEM_CATEGORIAS = ["Combustível", "Uso ou Consumo", "Serviços Tomados", "Aluguel", "Locação de Máquinas", "Fretes",
                    "Demais Produtos (energia)", "Depreciação (F120)", "Imobilizado (aquisição)"]


def _cor(i: int, dark: bool) -> str:
    return _SLOTS["dark" if dark else "light"][i]


def cor_categoria(cat: str, dark: bool) -> str:
    if cat in ORDEM_CATEGORIAS[:8]:
        return _cor(ORDEM_CATEGORIAS.index(cat), dark)
    return _CINZA["dark" if dark else "light"]


def rotulo_comp(comp: str) -> str:
    """'07/2026' -> 'jul/26'."""
    try:
        m, a = str(comp).split("/")
        return f"{_MESES[int(m) - 1]}/{a[-2:]}"
    except (ValueError, IndexError):
        return str(comp)


def _n(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def preparar_serie(serie: pd.DataFrame, tributo: str = "total") -> pd.DataFrame:
    """Acrescenta as colunas '*_total', o rotulo do mes e as diferencas/acumulados. Nao altera a original.
    tributo = 'total' (PIS+COFINS) | 'pis' | 'cofins': define o que as colunas '*_total' (KPIs e graficos) passam a mostrar."""
    if serie is None or serie.empty:
        return pd.DataFrame()
    d = serie.copy().reset_index(drop=True)

    def comb(a, b):
        return a if tributo == "pis" else b if tributo == "cofins" else a + b

    d["rotulo"] = d["competencia"].map(rotulo_comp)
    d["cred_total"] = comb(d["cred_pis"], d["cred_cofins"])
    tem = d["tem_original"].astype(bool)
    d["orig_total"] = [(comb(_n(a) or 0.0, _n(b) or 0.0)) if t else None for a, b, t in zip(d["orig_pis"], d["orig_cofins"], tem)]
    d["dif_total"] = [(c - o) if t and o is not None else None for c, o, t in zip(d["cred_total"], d["orig_total"], tem)]
    acum, soma = [], 0.0
    for v in d["dif_total"]:
        if v is not None and not pd.isna(v):
            soma += float(v)
            acum.append(soma)
        else:
            acum.append(None)
    d["dif_acum"] = acum
    d["cred_acum"] = d["cred_total"].cumsum()
    d["deb_nc_total"] = comb(d["deb_nc_pis"], d["deb_nc_cofins"])
    d["deb_cum_total"] = comb(d["deb_cum_pis"], d["deb_cum_cofins"])
    d["usado_total"] = comb(d["usado_pis"] + d["anterior_pis"], d["usado_cofins"] + d["anterior_cofins"])
    d["ret_total"] = comb(d["ret_pis"], d["ret_cofins"])
    d["recolher_total"] = comb(d["recolher_pis"], d["recolher_cofins"])
    d["saldo_total"] = comb(d["saldo_pis"], d["saldo_cofins"])
    return d


def _tributo(d: pd.DataFrame, tributo: str, pre: str) -> list:
    """pre = 'cred' | 'orig' -> lista conforme tributo ('total' | 'pis' | 'cofins')."""
    if tributo == "pis":
        return d[f"{pre}_pis"].tolist()
    if tributo == "cofins":
        return d[f"{pre}_cofins"].tolist()
    return d["cred_total" if pre == "cred" else "orig_total"].tolist()


def _barras(fig, x, vals, nome, cor, rotular: bool, stack: bool = False):
    fig.add_bar(x=x, y=[_n(v) for v in vals], name=nome, marker_color=cor, marker_line_width=0,
                text=[formatacao.moeda_curta(v) for v in vals] if rotular else None, textposition="inside" if stack else "outside",
                cliponaxis=False, customdata=[formatacao.moeda_br(v) for v in vals],
                hovertemplate=f"<b>{nome}</b><br>%{{x}}: %{{customdata}}<extra></extra>")


def _folga_x(fig: go.Figure, valores) -> None:
    """Barras horizontais com rotulo FORA da barra: abre espaco a direita (e a esquerda, se houver negativo) para o texto nao ser cortado."""
    vs = [float(v) for v in valores if v is not None and not pd.isna(v)]
    if vs:
        fig.update_xaxes(range=[min(0.0, min(vs) * 1.35), max(0.0, max(vs) * 1.35)])


def _mask(c: str) -> str:
    d = "".join(ch for ch in str(c) if ch.isdigit())
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else str(c)


def _nome(fornecedor, cnpj="") -> str:
    n = str(fornecedor or "").strip()
    return n[:34] if n and n != "0" else (_mask(cnpj) if cnpj else "(sem nome)")


def _vazio(texto: str, dark: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=texto, showarrow=False, font=dict(size=14))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base(fig, dark, altura=220, legenda=False)


# ─────────────────────────────────────────────
#  CREDITO
# ─────────────────────────────────────────────
def fig_credito_mensal(d: pd.DataFrame, dark: bool, tributo: str = "total", nome_apontado: str = "Crédito apontado") -> go.Figure:
    """Barras = credito apontado (o que esta' no SPED vigente); linha = credito do SPED original (so' jan-jul, carga fixa)."""
    if d.empty:
        return _vazio("Sem dados de crédito ainda.", dark)
    x = d["rotulo"].tolist()
    ap, orig = _tributo(d, tributo, "cred"), _tributo(d, tributo, "orig")
    fig = go.Figure()
    _barras(fig, x, ap, nome_apontado, _cor(0, dark), len(x) <= 10)
    if any(v is not None and not pd.isna(v) for v in orig):
        fig.add_scatter(x=x, y=[_n(v) for v in orig], name="Crédito no SPED original", mode="lines+markers",
                        line=dict(color=_cor(1, dark), width=2), marker=dict(size=8, color=_cor(1, dark)), connectgaps=False,
                        customdata=[formatacao.moeda_br(v) if v is not None and not pd.isna(v) else "—" for v in orig],
                        hovertemplate="<b>Crédito no SPED original</b><br>%{x}: %{customdata}<extra></extra>")
    tv, tt = _ticks_moeda([v for v in ap + orig if v is not None and not pd.isna(v)])
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark)


def fig_diferenca(d: pd.DataFrame, dark: bool) -> go.Figure:
    """Diferenca apontado - original por mes (barras) e acumulada (linha). So' meses com original (carga fixa)."""
    dd = d[d["dif_total"].notna()] if not d.empty else d
    if dd.empty:
        return _vazio("Sem meses com SPED original para comparar.", dark)
    x = dd["rotulo"].tolist()
    mensal, acum = dd["dif_total"].tolist(), dd["dif_acum"].tolist()
    fig = go.Figure()
    _barras(fig, x, mensal, "Diferença do mês", _cor(0, dark), True)
    fig.add_scatter(x=x, y=acum, name="Diferença acumulada", mode="lines+markers", line=dict(color=_cor(1, dark), width=2),
                    marker=dict(size=8, color=_cor(1, dark)), customdata=[formatacao.moeda_br(v) for v in acum],
                    hovertemplate="<b>Diferença acumulada</b><br>%{x}: %{customdata}<extra></extra>")
    tv, tt = _ticks_moeda(mensal + acum)
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark)


def fig_credito_acumulado(d: pd.DataFrame, dark: bool) -> go.Figure:
    if d.empty:
        return _vazio("Sem dados de crédito ainda.", dark)
    x, vals = d["rotulo"].tolist(), d["cred_acum"].tolist()
    fig = go.Figure(go.Scatter(x=x, y=vals, name="Crédito acumulado", mode="lines+markers", line=dict(color=_cor(0, dark), width=2),
                               marker=dict(size=8, color=_cor(0, dark)), fill="tozeroy", fillcolor="rgba(42,120,214,0.10)",
                               customdata=[formatacao.moeda_br(v) for v in vals],
                               hovertemplate="<b>Crédito acumulado</b><br>%{x}: %{customdata}<extra></extra>"))
    tv, tt = _ticks_moeda(vals)
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark, altura=290, legenda=False)


def fig_credito_categoria(d: pd.DataFrame, dark: bool, tributo: str = "total") -> go.Figure:
    """Barras empilhadas por mes, uma cor FIXA por categoria de credito."""
    if d.empty:
        return _vazio("Sem dados de crédito ainda.", dark)
    x = d["rotulo"].tolist()
    cats = [c for c in ORDEM_CATEGORIAS if any(c in (cat or {}) for cat in d["por_categoria"])]
    cats += sorted({c for cat in d["por_categoria"] for c in (cat or {})} - set(cats))
    if not cats:
        return _vazio("O resumo por categoria ainda não está disponível.", dark)
    fig = go.Figure()
    todos = []
    for c in cats:
        vals = []
        for cat in d["por_categoria"]:
            e = (cat or {}).get(c)
            vals.append(0.0 if not e else (e["pis"] if tributo == "pis" else e["cofins"] if tributo == "cofins" else e["pis"] + e["cofins"]))
        todos.append(vals)
        _barras(fig, x, vals, c, cor_categoria(c, dark), False, stack=True)
    fig.update_layout(barmode="stack")
    totais = [sum(v[i] for v in todos) for i in range(len(x))]
    tv, tt = _ticks_moeda(totais)
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    fig.update_traces(marker_line_color="rgba(0,0,0,0)", selector=dict(type="bar"))
    fig.update_layout(bargap=0.28, barcornerradius=3)
    return _base(fig, dark, altura=360)


def fig_creditos_por_categoria_mes(por_cat: dict, dark: bool, tributo: str = "total") -> go.Figure:
    """Barras horizontais: credito por categoria no mes selecionado."""
    if not por_cat:
        return _vazio("Sem créditos neste mês.", dark)
    itens = []
    for c, e in por_cat.items():
        v = e["pis"] if tributo == "pis" else e["cofins"] if tributo == "cofins" else e["pis"] + e["cofins"]
        itens.append((c, v))
    itens.sort(key=lambda t: t[1])
    fig = go.Figure(go.Bar(x=[v for _, v in itens], y=[c for c, _ in itens], orientation="h",
                           marker_color=[cor_categoria(c, dark) for c, _ in itens], text=[formatacao.moeda_curta(v) for _, v in itens],
                           textposition="outside", cliponaxis=False, customdata=[formatacao.moeda_br(v) for _, v in itens],
                           hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, [v for _, v in itens])
    return _base(fig, dark, altura=max(220, 44 * len(itens) + 60), legenda=False)


def fig_top_fornecedores(linhas: pd.DataFrame, dark: bool, topo: int = 10, tributo: str = "total") -> go.Figure:
    """Maiores fornecedores em credito no mes (linhas do SPED com CNPJ). Colunas esperadas: cnpj, fornecedor, pis, cofins."""
    if linhas is None or linhas.empty or not (linhas["cnpj"].astype(str) != "").any():
        return _vazio("Sem fornecedores identificados neste mês.", dark)
    x = linhas[linhas["cnpj"].astype(str) != ""]
    g = x.groupby("cnpj").agg(fornecedor=("fornecedor", "first"), pis=("pis", "sum"), cofins=("cofins", "sum"))
    g["v"] = g["pis"] if tributo == "pis" else g["cofins"] if tributo == "cofins" else g["pis"] + g["cofins"]
    g = g.sort_values("v", ascending=False).head(topo).sort_values("v")
    nomes = [_nome(n, c) for c, n in zip(g.index, g["fornecedor"])]
    fig = go.Figure(go.Bar(x=g["v"].tolist(), y=nomes, orientation="h", marker_color=_cor(0, dark),
                           text=[formatacao.moeda_curta(v) for v in g["v"]], textposition="outside", cliponaxis=False,
                           customdata=[formatacao.moeda_br(v) for v in g["v"]], hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, g["v"].tolist())
    return _base(fig, dark, altura=max(240, 40 * len(g) + 60), legenda=False)


def fig_dif_fornecedores(dif: list, dark: bool, topo: int = 10) -> go.Figure:
    """Fornecedores que mais mudaram o credito entre o SPED original e o apontado (carga fixa jan-jul). Positivo = ganhou credito."""
    if not dif:
        return _vazio("Sem comparação original × apontado para este mês.", dark)
    it = sorted(dif, key=lambda r: abs(r["dif_pis"] + r["dif_cofins"]), reverse=True)[:topo]
    it = sorted(it, key=lambda r: r["dif_pis"] + r["dif_cofins"])
    vals = [r["dif_pis"] + r["dif_cofins"] for r in it]
    fig = go.Figure(go.Bar(x=vals, y=[_nome(r["fornecedor"], r.get("cnpj", "")) for r in it], orientation="h",
                           marker_color=[_cor(0, dark) if v >= 0 else _cor(1, dark) for v in vals],
                           text=[formatacao.moeda_curta(v) for v in vals], textposition="outside", cliponaxis=False,
                           customdata=[formatacao.moeda_br(v, forcar_sinal=True) for v in vals],
                           hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    fig.add_vline(x=0, line_width=1, line_color=COR_REF["dark" if dark else "light"])
    _folga_x(fig, vals)
    return _base(fig, dark, altura=max(240, 40 * len(it) + 60), legenda=False)


# ─────────────────────────────────────────────
#  DEBITO / APURACAO
# ─────────────────────────────────────────────
def fig_debito_credito(d: pd.DataFrame, dark: bool) -> go.Figure:
    """Por mes: debito nao cumulativo, debito cumulativo e credito utilizado (PIS+COFINS)."""
    if d.empty:
        return _vazio("Sem dados de apuração ainda.", dark)
    x = d["rotulo"].tolist()
    series = [("Débito não cumulativo", d["deb_nc_total"].tolist(), _cor(0, dark)),
              ("Débito cumulativo", d["deb_cum_total"].tolist(), _cor(1, dark)),
              ("Crédito utilizado", d["usado_total"].tolist(), _cor(2, dark))]
    fig = go.Figure()
    for nome, vals, cor in series:
        _barras(fig, x, vals, nome, cor, len(x) <= 4)
    fig.update_layout(barmode="group")
    tv, tt = _ticks_moeda([v for _n_, vals, _c in series for v in vals])
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark)


def fig_a_recolher(d: pd.DataFrame, dark: bool) -> go.Figure:
    """Por mes: total a recolher e retencoes na fonte (PIS+COFINS)."""
    if d.empty:
        return _vazio("Sem dados de apuração ainda.", dark)
    x = d["rotulo"].tolist()
    series = [("A recolher", d["recolher_total"].tolist(), _cor(0, dark)), ("Retenções (não cumulativo)", d["ret_total"].tolist(), _cor(3, dark))]
    fig = go.Figure()
    for nome, vals, cor in series:
        _barras(fig, x, vals, nome, cor, len(x) <= 6)
    fig.update_layout(barmode="group")
    tv, tt = _ticks_moeda([v for _n_, vals, _c in series for v in vals])
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark)


def fig_saldo_credito(d: pd.DataFrame, dark: bool) -> go.Figure:
    """Saldo de credito a transportar para o proximo mes (PIS+COFINS)."""
    if d.empty:
        return _vazio("Sem dados de apuração ainda.", dark)
    x, vals = d["rotulo"].tolist(), d["saldo_total"].tolist()
    fig = go.Figure(go.Scatter(x=x, y=vals, name="Saldo de crédito", mode="lines+markers", line=dict(color=_cor(2, dark), width=2),
                               marker=dict(size=8, color=_cor(2, dark)), customdata=[formatacao.moeda_br(v) for v in vals],
                               hovertemplate="<b>Saldo de crédito a transportar</b><br>%{x}: %{customdata}<extra></extra>"))
    tv, tt = _ticks_moeda(vals)
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark, altura=290, legenda=False)


def fig_receita(d: pd.DataFrame, dark: bool) -> go.Figure:
    """Receita por regime: nao cumulativa x cumulativa (empilhadas = receita total)."""
    if d.empty:
        return _vazio("Sem dados de receita ainda.", dark)
    x = d["rotulo"].tolist()
    nc, cu = d["receita_nc"].tolist(), d["receita_cum"].tolist()
    fig = go.Figure()
    _barras(fig, x, nc, "Receita não cumulativa", _cor(0, dark), False, stack=True)
    _barras(fig, x, cu, "Receita cumulativa", _cor(1, dark), False, stack=True)
    fig.update_layout(barmode="stack", barcornerradius=3)
    tv, tt = _ticks_moeda([a + b for a, b in zip(nc, cu)])
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark)


def fig_base_natureza(por_nat: dict, dark: bool) -> go.Figure:
    """Credito (PIS+COFINS) por natureza da base de calculo no mes (M105/M505)."""
    if not por_nat:
        return _vazio("Sem bases de crédito neste mês.", dark)
    it = sorted(((v["natureza"], v["pis"] + v["cofins"]) for v in por_nat.values()), key=lambda t: t[1])
    fig = go.Figure(go.Bar(x=[v for _, v in it], y=[n for n, _ in it], orientation="h", marker_color=_cor(0, dark),
                           text=[formatacao.moeda_curta(v) for _, v in it], textposition="outside", cliponaxis=False,
                           customdata=[formatacao.moeda_br(v) for _, v in it], hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, [v for _, v in it])
    return _base(fig, dark, altura=max(220, 44 * len(it) + 60), legenda=False)


def fig_status_conferencia(resumo: pd.DataFrame, dark: bool) -> go.Figure:
    """Linhas conferidas por status (contagem). Cor por significado: conferido = azul; o que pede atencao = laranja."""
    if resumo is None or resumo.empty:
        return _vazio("Envie o SPED e o Detalhamento para ver a conferência.", dark)
    r = resumo.sort_values("linhas")
    cores_ = [_cor(0, dark) if s in ("Conferido", "Esperado (sem nota)") else _cor(1, dark) for s in r["status"]]
    fig = go.Figure(go.Bar(x=r["linhas"].tolist(), y=r["status"].tolist(), orientation="h", marker_color=cores_,
                           text=[str(int(v)) for v in r["linhas"]], textposition="outside", cliponaxis=False,
                           hovertemplate="<b>%{y}</b><br>%{x} linhas<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, r["linhas"].tolist())
    return _base(fig, dark, altura=max(200, 44 * len(r) + 50), legenda=False)


# ─────────────────────────────────────────────
#  (v0.52.0) PONTE, COMPOSICAO E COMPARATIVOS
# ─────────────────────────────────────────────
def fig_ponte(ponte: dict, dark: bool, nome_inicio: str = "Oficial / padrão", nome_fim: str = "Simulação") -> go.Figure:
    """Ponte (waterfall): total do periodo na versao oficial/padrao -> efeito de cada mes que mudou -> total da simulacao.
    Sobe = azul; desce = laranja (negativo sempre laranja e com '-'); extremos = cinza-azulado neutro."""
    itens = ponte.get("itens") or []
    if not itens:
        return _vazio("A simulação não muda nenhum mês em relação à oficial/padrão.", dark)
    x = [nome_inicio] + [r for r, _ in itens] + [nome_fim]
    y = [ponte["inicio"]] + [v for _r, v in itens] + [ponte["fim"]]
    medidas = ["absolute"] + ["relative"] * len(itens) + ["total"]
    texto = [formatacao.moeda_curta(ponte["inicio"])] + [formatacao.moeda_curta(v) if v < 0 else "+" + formatacao.moeda_curta(v) for _r, v in itens] + [formatacao.moeda_curta(ponte["fim"])]
    cd = [formatacao.moeda_br(ponte["inicio"])] + [formatacao.moeda_br(v, forcar_sinal=True) for _r, v in itens] + [formatacao.moeda_br(ponte["fim"])]
    fig = go.Figure(go.Waterfall(x=x, y=y, measure=medidas, text=texto, textposition="outside", cliponaxis=False, customdata=cd,
                                 hovertemplate="<b>%{x}</b><br>%{customdata}<extra></extra>",
                                 increasing=dict(marker=dict(color=_cor(0, dark))), decreasing=dict(marker=dict(color=_cor(1, dark))),
                                 totals=dict(marker=dict(color=_CINZA["dark" if dark else "light"])),
                                 connector=dict(line=dict(color=COR_REF["dark" if dark else "light"], width=1))))
    tv, tt = _ticks_moeda([ponte["inicio"], ponte["fim"]] + [ponte["inicio"] + sum(v for _r, v in itens[:k + 1]) for k in range(len(itens))])
    fig.update_yaxes(tickvals=tv, ticktext=tt)
    return _base(fig, dark, altura=340, legenda=False)


def fig_composicao(comp: pd.DataFrame, dark: bool) -> go.Figure:
    """Credito por categoria no mes com o % do total no rotulo (comp = creditos_analise.composicao)."""
    if comp is None or comp.empty:
        return _vazio("Sem créditos neste mês.", dark)
    c = comp.sort_values("valor")
    fig = go.Figure(go.Bar(x=c["valor"].tolist(), y=c["categoria"].tolist(), orientation="h", marker_color=[cor_categoria(k, dark) for k in c["categoria"]],
                           text=[f"{formatacao.moeda_curta(v)} · {formatacao.pct_br(p, casas=0)}" for v, p in zip(c["valor"], c["pct"])],
                           textposition="outside", cliponaxis=False,
                           customdata=[f"{formatacao.moeda_br(v)} ({formatacao.pct_br(p)})" for v, p in zip(c["valor"], c["pct"])],
                           hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, c["valor"].tolist())
    return _base(fig, dark, altura=max(220, 44 * len(c) + 60), legenda=False)


def fig_comparacao_categorias(df: pd.DataFrame, dark: bool, nome_a: str, nome_b: str) -> go.Figure:
    """Credito por categoria em duas versoes lado a lado. df = colunas item, a, b (creditos_analise.comparar_fontes, grupo 'Categoria')."""
    if df is None or df.empty:
        return _vazio("Sem créditos por categoria para comparar.", dark)
    d = df.sort_values("a")
    fig = go.Figure()
    for nome, col, cor in ((nome_a, "a", _cor(0, dark)), (nome_b, "b", _cor(1, dark))):
        fig.add_bar(x=d[col].tolist(), y=d["item"].tolist(), orientation="h", name=nome, marker_color=cor, marker_line_width=0,
                    text=[formatacao.moeda_curta(v) for v in d[col]], textposition="outside", cliponaxis=False,
                    customdata=[formatacao.moeda_br(v) for v in d[col]], hovertemplate=f"<b>{nome}</b><br>%{{y}}: %{{customdata}}<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_xaxes(showticklabels=False, showgrid=False)
    fig.update_yaxes(zeroline=False, showgrid=False)
    _folga_x(fig, list(d["a"]) + list(d["b"]))
    return _base(fig, dark, altura=max(260, 70 * len(d) + 60))
