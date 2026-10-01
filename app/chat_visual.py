# -*- coding: utf-8 -*-
"""
Visualizacao e relatorio do resultado de uma consulta do chat (01/10/2026,
pedido do Rafael: "ao lado do chat, a possibilidade do dash gerado pelo chat
virar relatorio, tabela ou grafico interessante").

Funcoes puras (sem Streamlit, sem banco): recebem o dict que uma ferramenta de
consultas_chat devolveu e produzem
  - `preparar(nome, resultado)` -> tabela numerica + opcoes de grafico;
  - `tabela_exibicao(prep)` -> DataFrame formatado em pt-BR;
  - `figura(...)` -> Figure plotly (paleta do app: azul / laranja / verde-agua);
  - `excel_bytes(...)` / `pdf_bytes(...)` -> relatorio para download.
Nenhum numero e' recalculado aqui: tudo vem do resultado da ferramenta.
"""
from __future__ import annotations

import datetime
import io
import re
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

import formatacao

AZUL, LARANJA, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CORES = [AZUL, LARANJA, AQUA, "#8a8985"]

FORMATO_INDICADOR = {
    "Liquidez Corrente": "x", "Capital de Giro": "R$", "Endividamento Geral": "pct", "Margem Bruta": "pct",
    "Margem Líquida": "pct", "ROA": "pct", "ROE": "pct", "EBITDA": "R$", "Margem EBITDA": "pct",
}
TIPOS_GRAFICO = ["Barras", "Barras horizontais", "Linhas"]

TITULOS = {
    "consultar_bp_dre": "BP/DRE", "consultar_visao_grupo": "Visão Grupo", "consultar_periodos": "Períodos disponíveis",
    "consultar_indicadores": "Indicadores", "consultar_evolucao_indicadores": "Evolução dos indicadores",
    "consultar_completude": "Completude dos dados", "consultar_notas_fiscais_kpi": "Conciliação de notas fiscais",
    "consultar_notas_pendentes": "Notas pendentes", "consultar_nota_fiscal": "Nota fiscal",
    "consultar_historico_importacoes": "Histórico de importações", "consultar_correcoes_manuais": "Correções manuais",
    "consultar_relatorios_gerados": "Relatórios gerados",
}


def _rotulo_formato(fmt: str, v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if fmt == "R$":
        return formatacao.moeda_br(v)
    if fmt == "pct":
        return formatacao.pct_br(v)
    if fmt == "x":
        return formatacao.numero_br(v, sufixo="x")
    if fmt == "int":
        return f"{int(v):,}".replace(",", ".")
    return str(v)


def _is_pct_col(c) -> bool:
    return str(c).strip().startswith("%")


def preparar(nome: str, r: dict, nomes_empresa: Optional[dict] = None) -> Optional[dict]:
    """-> {"titulo","subtitulo","df","formatos":{col:fmt},"graficos":{nome:{"df","x","ys","formato"}}} ou None."""
    if not isinstance(r, dict) or "erro" in r:
        return None
    titulo = TITULOS.get(nome, nome)
    sub = []

    if "contas" in r and r["contas"]:
        df = pd.DataFrame(r["contas"])
        money = [c for c in df.columns if c not in ("grupo", "conta") and not _is_pct_col(c)]
        formatos = {c: ("pct" if _is_pct_col(c) else "R$") for c in df.columns if c not in ("grupo", "conta")}
        if r.get("tipo"):
            titulo = f"{r['tipo']} — " + ("Grupo" if nome == "consultar_visao_grupo" else str(r.get("empresa", "")))
        sub = [f"Período {r.get('periodo', '—')}", f"Base: {r.get('granularidade') or 'não declarada'}"]
        graficos = {}
        if money:
            col = "valor" if "valor" in money else ("VALOR CONSOLIDADO" if "VALOR CONSOLIDADO" in money else money[0])
            por_grupo = df.groupby("grupo", sort=False)[money].sum().reset_index()
            graficos["Total por grupo"] = {"df": por_grupo, "x": "grupo", "ys": [col], "formato": "R$"}
            top = df.reindex(df[col].abs().sort_values(ascending=False).index).head(15)
            graficos["15 maiores contas"] = {"df": top[["conta"] + money], "x": "conta", "ys": [col], "formato": "R$"}
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df, "formatos": formatos, "graficos": graficos}

    if "periodos_por_empresa" in r:
        nomes = nomes_empresa or {}
        linhas = [{"Empresa": nomes.get(cod, cod), "Períodos disponíveis": ", ".join(sorted(ps, reverse=True)) if ps else "—",
                   "Qtde": len(ps)} for cod, ps in r["periodos_por_empresa"].items()]
        df = pd.DataFrame(linhas)
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": {"Qtde": "int"},
                "graficos": {"Períodos por empresa": {"df": df[["Empresa", "Qtde"]], "x": "Empresa",
                                                      "ys": ["Qtde"], "formato": "int"}}}

    if "indicadores" in r:
        linhas = [{"Indicador": k, "Valor": v, "_fmt": FORMATO_INDICADOR.get(k, "R$")} for k, v in r["indicadores"].items()]
        df = pd.DataFrame(linhas)
        sub = [f"Período {r.get('periodo', '—')}", f"Base: {r.get('base_do_periodo', '—')}",
               "Empresas: " + ", ".join(r.get("empresas_incluidas", []))]
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df.drop(columns="_fmt"),
                "formatos": {}, "linha_formato": dict(zip(df["Indicador"], df["_fmt"])), "graficos": {}}

    if "evolucao" in r and r["evolucao"]:
        df = pd.DataFrame(r["evolucao"])
        cols = [c for c in df.columns if c != "periodo"]
        formatos = {c: FORMATO_INDICADOR.get(c, "R$") for c in cols}
        graficos = {}
        for rotulo, fmt in (("Valores em R$", "R$"), ("Margens e retornos (%)", "pct"), ("Liquidez (x)", "x")):
            ys = [c for c in cols if formatos[c] == fmt]
            if ys:
                graficos[rotulo] = {"df": df[["periodo"] + ys], "x": "periodo", "ys": ys[:3], "formato": fmt}
        sub = [f"Base: {r.get('base_do_periodo', '—')}", "Empresas: " + ", ".join(r.get("empresas_incluidas", []))]
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df, "formatos": formatos, "graficos": graficos}

    if "completude_por_periodo" in r and r["completude_por_periodo"]:
        df = pd.DataFrame(r["completude_por_periodo"])
        graf = {}
        comp, pend, per = [], [], []
        for l in r["completude_por_periodo"]:
            m = re.search(r"\((\d+)/(\d+)\)", str(l.get("Status", "")))
            if m:
                per.append(str(l.get("Período"))); comp.append(int(m.group(1))); pend.append(int(m.group(2)) - int(m.group(1)))
        if per:
            graf["Empresas completas x pendentes"] = {
                "df": pd.DataFrame({"Período": per, "Completas": comp, "Pendentes": pend}),
                "x": "Período", "ys": ["Completas", "Pendentes"], "formato": "int", "empilhado": True}
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": {}, "graficos": graf}

    if "rodadas" in r and r["rodadas"]:
        df = pd.DataFrame(r["rodadas"])
        formatos = {"taxa_conciliacao": "pct"}
        g = df.assign(rodada=df["empresa_codigo"] + " " + df["periodo_referencia"].astype(str))
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": formatos, "graficos": {
            "Notas lançadas x pendentes por rodada": {"df": g[["rodada", "total_lancadas", "total_pendencias"]], "x": "rodada",
                                                       "ys": ["total_lancadas", "total_pendencias"], "formato": "int", "empilhado": True}}}

    for chave, rotulo_x, col_valor in (("pendencias", "fornecedor_nome", "valor"), ("notas", "fornecedor_nome", "valor")):
        if chave in r and r[chave]:
            df = pd.DataFrame(r[chave]).drop(columns=["registro_id"], errors="ignore")
            formatos = {c: "R$" for c in ("valor", "sienge_valor") if c in df.columns}
            graficos = {}
            if "status" in df.columns:
                cont = df["status"].value_counts().rename_axis("Status").reset_index(name="Quantidade")
                graficos["Por status"] = {"df": cont, "x": "Status", "ys": ["Quantidade"], "formato": "int"}
            if "valor" in df.columns and rotulo_x in df.columns:
                top = (df.groupby(rotulo_x)["valor"].sum().sort_values(ascending=False).head(10).reset_index())
                graficos["10 maiores fornecedores (valor)"] = {"df": top, "x": rotulo_x, "ys": ["valor"], "formato": "R$"}
            return {"titulo": titulo, "subtitulo": f"{len(df)} registro(s)", "df": df, "formatos": formatos, "graficos": graficos}

    for chave in ("importacoes", "correcoes", "relatorios"):
        if chave in r and r[chave]:
            df = pd.DataFrame(r[chave])
            for c in df.columns:
                df[c] = df[c].apply(lambda v: ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v)
            formatos = {c: "R$" for c in ("valor_atual", "valor_original_pdf") if c in df.columns}
            return {"titulo": titulo, "subtitulo": f"{len(df)} registro(s)", "df": df, "formatos": formatos, "graficos": {}}
    return None


def tabela_exibicao(prep: dict) -> pd.DataFrame:
    df = prep["df"].copy()
    lf = prep.get("linha_formato")
    if lf is not None and "Valor" in df.columns:
        df["Valor"] = [_rotulo_formato(lf.get(i, "R$"), v) for i, v in zip(df["Indicador"], df["Valor"])]
        return df
    for c, fmt in prep["formatos"].items():
        if c in df.columns and fmt != "int":  # contagens ficam numericas (ordenaveis na tabela)
            df[c] = df[c].apply(lambda v, f=fmt: _rotulo_formato(f, v))
    return df


def figura(grafico: dict, tipo: str, dark: bool = True, ys: Optional[list[str]] = None) -> go.Figure:
    """grafico = prep["graficos"][nome]; tipo em TIPOS_GRAFICO."""
    df, x, fmt = grafico["df"], grafico["x"], grafico["formato"]
    ys = [y for y in (ys or grafico["ys"]) if y in df.columns] or grafico["ys"]
    fig = go.Figure()
    horizontal = tipo == "Barras horizontais"
    for i, y in enumerate(ys):
        vals = df[y].tolist()
        plot = [None if pd.isna(v) else (v * 100.0 if fmt == "pct" else v) for v in vals]
        hover = [_rotulo_formato(fmt, v) for v in vals]
        kw = dict(name=str(y), customdata=hover, marker_color=CORES[i % len(CORES)])
        if tipo == "Linhas":
            fig.add_scatter(x=df[x].astype(str), y=plot, mode="lines+markers", line=dict(color=CORES[i % len(CORES)], width=2),
                            marker=dict(size=7), name=str(y), customdata=hover,
                            hovertemplate="<b>%{fullData.name}</b><br>%{x}: %{customdata}<extra></extra>")
        elif horizontal:
            fig.add_bar(x=plot, y=df[x].astype(str), orientation="h", hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>", **kw)
        else:
            fig.add_bar(x=df[x].astype(str), y=plot, hovertemplate="<b>%{x}</b><br>%{customdata}<extra></extra>", **kw)
    fig.update_layout(
        height=340 if not horizontal else max(300, 30 * len(df) + 90), margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", separators=",.",
        barmode="stack" if grafico.get("empilhado") else "group", showlegend=len(ys) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    eixo_val = dict(gridcolor="rgba(128,128,128,0.18)", zeroline=True, zerolinecolor="#8a8985")
    if fmt == "pct":
        eixo_val["ticksuffix"] = "%"
    if horizontal:
        fig.update_xaxes(**eixo_val)
        fig.update_yaxes(autorange="reversed", showgrid=False)
    else:
        fig.update_yaxes(**eixo_val)
        fig.update_xaxes(showgrid=False)
    return fig


def _meta(prep: dict, usuario: str, consulta: str) -> list[tuple[str, str]]:
    return [("Relatório", prep["titulo"]), ("Detalhes", prep.get("subtitulo") or "—"), ("Origem", consulta),
            ("Gerado por", usuario), ("Gerado em", datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
            ("Observação", "Dados consultados no sistema EGC pela Erik.AI; nenhum valor foi recalculado ou estimado.")]


def excel_bytes(prep: dict, usuario: str = "", consulta: str = "") -> bytes:
    """Planilha com a tabela (numeros reais, nao texto) + aba 'Fonte'."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df = prep["df"].copy()
        df.to_excel(xw, sheet_name="Resultado", index=False)
        ws = xw.sheets["Resultado"]
        lf = prep.get("linha_formato")
        for j, c in enumerate(df.columns, start=1):
            largura = max([len(str(c))] + [len(str(v)) for v in df[c].head(200)]) + 2
            ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = min(largura, 60)
            fmt = prep["formatos"].get(c)
            if fmt in ("R$", "pct", "x") or (lf is not None and c == "Valor"):
                for i in range(2, len(df) + 2):
                    cell = ws.cell(row=i, column=j)
                    f = fmt or lf.get(df.iloc[i - 2]["Indicador"], "R$")
                    cell.number_format = {"R$": '#,##0.00;[Red]-#,##0.00', "pct": "0.0%", "x": '0.00"x"'}.get(f, "General")
        pd.DataFrame(_meta(prep, usuario, consulta), columns=["Campo", "Valor"]).to_excel(xw, sheet_name="Fonte", index=False)
        xw.sheets["Fonte"].column_dimensions["A"].width = 16
        xw.sheets["Fonte"].column_dimensions["B"].width = 90
    return buf.getvalue()


def _png_grafico(grafico: dict, tipo: str, ys: Optional[list[str]] = None) -> Optional[bytes]:
    """PNG do grafico via matplotlib (sem kaleido), pro PDF."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    df, x, fmt = grafico["df"], grafico["x"], grafico["formato"]
    ys = [y for y in (ys or grafico["ys"]) if y in df.columns] or grafico["ys"]
    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=150)
    rotulos = df[x].astype(str).tolist()
    n = len(ys)
    pos = list(range(len(rotulos)))
    base = [0.0] * len(rotulos)
    for i, y in enumerate(ys):
        vals = [0.0 if pd.isna(v) else (v * 100.0 if fmt == "pct" else v) for v in df[y]]
        cor = CORES[i % len(CORES)]
        if tipo == "Linhas":
            ax.plot(pos, vals, marker="o", color=cor, label=str(y))
        elif grafico.get("empilhado"):
            ax.bar(pos, vals, bottom=base, color=cor, label=str(y)); base = [b + v for b, v in zip(base, vals)]
        else:
            w = 0.8 / n
            ax.bar([p - 0.4 + w * (i + 0.5) for p in pos], vals, width=w, color=cor, label=str(y))
    ax.set_xticks(pos)
    ax.set_xticklabels([r[:22] for r in rotulos], rotation=30 if len(rotulos) > 4 else 0, ha="right" if len(rotulos) > 4 else "center", fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.axhline(0, color="#8a8985", linewidth=0.6)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    if n > 1:
        ax.legend(fontsize=7, frameon=False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _p: (f"{v:,.0f}".replace(",", ".")) + ("%" if fmt == "pct" else "")))
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()


def pdf_bytes(prep: dict, usuario: str = "", consulta: str = "", grafico: Optional[dict] = None,
              tipo_grafico: str = "Barras", ys: Optional[list[str]] = None) -> bytes:
    """Relatorio PDF simples: titulo, detalhes, grafico (opcional) e tabela formatada pt-BR."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=prep["titulo"], author="EnerMais EGC")
    st = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=st["Title"], fontSize=16, alignment=0, textColor=colors.HexColor("#1f2a37"))
    small = ParagraphStyle("small", parent=st["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))
    cel = ParagraphStyle("cel", parent=st["Normal"], fontSize=7, leading=8.5)
    el = [Paragraph(escape(prep["titulo"]), h1)]
    if prep.get("subtitulo"):
        el.append(Paragraph(escape(prep["subtitulo"]), small))
    el.append(Paragraph(escape(f"Gerado por {usuario or '—'} em {datetime.datetime.now():%d/%m/%Y %H:%M} · fonte: {consulta or 'sistema EGC'} · "
                              "valores consultados no sistema, sem recálculo ou estimativa."), small))
    el.append(Spacer(1, 6))
    if grafico is not None:
        png = _png_grafico(grafico, tipo_grafico, ys)
        if png:
            el += [Image(io.BytesIO(png), width=170 * mm, height=76 * mm), Spacer(1, 6)]
    disp = tabela_exibicao(prep)
    MAX = 300
    cortado = len(disp) > MAX
    disp = disp.head(MAX)
    dados = [[Paragraph(f"<b>{escape(str(c))}</b>", cel) for c in disp.columns]]
    for _i, row in disp.iterrows():
        dados.append([Paragraph(escape(str(v if v is not None and not (isinstance(v, float) and pd.isna(v)) else "—")), cel) for v in row])
    tb = Table(dados, repeatRows=1)
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
    ]))
    el.append(tb)
    if cortado:
        el.append(Paragraph(f"Tabela limitada às primeiras {MAX} linhas — baixe o Excel para ver tudo.", small))
    doc.build(el)
    return buf.getvalue()
